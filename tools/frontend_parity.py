#!/usr/bin/env python3
"""Synchronize and verify the closed Dify frontend snapshot."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = "web-legacy/upstream-manifest.json"
CONTRACT_PATH = "contracts/frontend.json"
SOURCE_LOCK_PATH = "contracts/sources.json"
LOCK_PATH = ".frontend-parity.lock"
ROOT_SNAPSHOT_FILES = frozenset(
    {
        "package.json",
        "pnpm-lock.yaml",
        "pnpm-workspace.yaml",
        "LICENSE",
    }
)
SNAPSHOT_DIRECTORIES = frozenset({"web", "web-legacy", "packages", "patches"})


def _local_frontend_path(path: str) -> str:
    return f"web-legacy/{path.removeprefix('web/')}" if path.startswith("web/") else path


def _upstream_frontend_path(path: str) -> str:
    return f"web/{path.removeprefix('web-legacy/')}" if path.startswith("web-legacy/") else path


SNAPSHOT_EXCLUDED_PREFIXES = ("packages/openapi-content-mapper/examples/",)
GENERATED_FILES = frozenset({"web-legacy/next-env.d.ts", "web-legacy/tsconfig.tsbuildinfo"})
PROJECT_OWNED_ROOT_FILES = frozenset({"package.json", "pnpm-workspace.yaml", "pnpm-lock.yaml"})
GENERATED_DIRECTORIES = frozenset(
    {
        "node_modules",
        ".next",
        ".vinext",
        "build",
        "dist",
        "coverage",
        ".cache",
        "__pycache__",
        ".mypy_cache",
        ".parcel-cache",
        ".pytest_cache",
        ".ruff_cache",
        ".turbo",
    }
)
ROUTE_PATTERN = re.compile(r"^(?:page|route)\.(?:ts|tsx|js|jsx)$")
TEST_FILE_PATTERN = re.compile(r"\.(?:spec|test)\.[cm]?[jt]sx?$")
RETAINED_TEST_HELPERS = frozenset(
    {
        "web-legacy/app/components/base/prompt-editor/plugins/__tests__/test-helpers.ts",
        "web-legacy/app/components/base/prompt-editor/plugins/__tests__/test-utils.tsx",
        "web-legacy/app/components/rag-pipeline/__tests__/datasource-fixtures.ts",
    }
)
PRUNED_TEST_FILES = frozenset(
    {
        "web-legacy/app/components/workflow/__tests__/node-catalog.spec.ts",
        "web-legacy/app/components/workflow-app/components/__tests__/workflow-main.spec.tsx",
        "web-legacy/app/components/workflow/hooks/__tests__/use-nodes-interactions.spec.ts",
        "web-legacy/app/components/workflow/nodes/_base/components/__tests__/agent-strategy-selector.spec.tsx",
        "web-legacy/app/components/workflow/nodes/_base/components/__tests__/agent-strategy.spec.tsx",
        "web-legacy/app/components/workflow/nodes/start-placeholder/__tests__/panel.spec.tsx",
        "web-legacy/app/components/workflow/nodes/parameter-extractor/components/extract-parameter/__tests__/import-from-tool.spec.tsx",
    }
)
EXCLUDED_TEST_SEGMENTS = frozenset(
    {
        "agent",
        "agent-v2",
        "trigger-plugin",
        "knowledge-base",
        "knowledge-retrieval",
        "knowledge-retrieval-v2",
        "data-source",
        "data-source-empty",
    }
)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
VERBATIM_KEYS = frozenset({"kind", "path", "source", "mode", "sha256"})
ADAPTED_KEYS = frozenset(
    {
        "kind",
        "path",
        "source",
        "mode",
        "reason",
        "upstream_sha256",
        "result_sha256",
        "patch",
        "patch_sha256",
    }
)


@dataclass(frozen=True, slots=True)
class Adaptation:
    path: str
    patch: str


@dataclass(frozen=True, slots=True)
class FrontendContract:
    local_files: tuple[str, ...]
    adaptations: dict[str, Adaptation]
    retained_routes: tuple[str, ...]
    pruned_files: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ParityReport:
    differences: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.differences


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _require_keys(value: Any, keys: frozenset[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(f"{label} must contain exactly {sorted(keys)}")
    return value


def _validate_path(value: Any, label: str = "path") -> str:
    if not isinstance(value, str) or not value or "\0" in value or "\\" in value:
        raise ValueError(f"Invalid {label}: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value:
        raise ValueError(f"Unsafe or non-canonical {label}: {value!r}")
    return value


def _is_env_path(relative: str) -> bool:
    name = PurePosixPath(relative).name
    return name == ".env" or name.startswith(".env.")


def _is_generated_path(relative: str) -> bool:
    return relative in GENERATED_FILES or any(
        part in GENERATED_DIRECTORIES for part in PurePosixPath(relative).parts
    )


def _is_route_path(relative: str) -> bool:
    path = PurePosixPath(relative)
    return len(path.parts) > 2 and path.parts[:2] == ("web-legacy", "app") and bool(ROUTE_PATTERN.fullmatch(path.name))


def _is_pruned_test_path(relative: str) -> bool:
    parts = PurePosixPath(relative).parts
    if relative in RETAINED_TEST_HELPERS or not parts or parts[0] != "web-legacy":
        return False
    is_test = (
        "__tests__" in parts
        or "__fixtures__" in parts
        or bool(TEST_FILE_PATTERN.search(PurePosixPath(relative).name))
    )
    if not is_test:
        return False
    if relative in PRUNED_TEST_FILES or EXCLUDED_TEST_SEGMENTS.intersection(parts):
        return True
    return not {"workflow", "workflow-app"}.intersection(parts)


def _is_snapshot_path(relative: str) -> bool:
    if relative.startswith(SNAPSHOT_EXCLUDED_PREFIXES):
        return False
    parts = PurePosixPath(relative).parts
    return relative in ROOT_SNAPSHOT_FILES or (len(parts) > 1 and parts[0] in SNAPSHOT_DIRECTORIES)


def _validate_local_path(value: Any) -> str:
    relative = _validate_path(value, "local path")
    parts = PurePosixPath(relative).parts
    if relative not in PROJECT_OWNED_ROOT_FILES and (len(parts) < 2 or parts[0] not in {"web-legacy", "packages"}):
        raise ValueError(f"Local frontend path must be under web-legacy, packages, or registered project roots: {relative}")
    if _is_env_path(relative) or _is_generated_path(relative):
        raise ValueError(f"Local frontend path is excluded from snapshot management: {relative}")
    return relative


def _validate_hash(value: Any, label: str) -> str:
    if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
        raise ValueError(f"Invalid {label}")
    return value


def _validate_mode(value: Any) -> str:
    if value not in {"100644", "100755"}:
        raise ValueError(f"Unsupported frontend file mode: {value!r}")
    return value


def load_frontend_contract(project_root: Path) -> FrontendContract:
    raw = _require_keys(
        _load_json(project_root / CONTRACT_PATH),
        frozenset({"schema_version", "local_files", "adaptations", "retained_routes", "pruned_files"}),
        "Frontend contract",
    )
    if raw["schema_version"] != 1:
        raise ValueError("Frontend contract schema_version must be 1")
    if (
        not isinstance(raw["local_files"], list)
        or not isinstance(raw["adaptations"], list)
        or not isinstance(raw["retained_routes"], list)
        or not isinstance(raw["pruned_files"], list)
    ):
        raise ValueError("Frontend contract local_files, adaptations and retained_routes must be arrays")

    local_files = tuple(sorted({_validate_local_path(path) for path in raw["local_files"]}))
    retained_routes = tuple(sorted({_validate_path(path, "retained route") for path in raw["retained_routes"]}))
    pruned_files = tuple(sorted({_validate_path(path, "pruned file") for path in raw["pruned_files"]}))
    if len(pruned_files) != len(raw["pruned_files"]):
        raise ValueError("Duplicate pruned frontend file")
    if any(not _is_snapshot_path(path) or _is_route_path(path) for path in pruned_files):
        raise ValueError("Pruned frontend files must be non-route snapshot paths")
    if len(retained_routes) != len(raw["retained_routes"]):
        raise ValueError("Duplicate retained frontend route")
    if any(not _is_route_path(path) for path in retained_routes):
        raise ValueError("Retained frontend route must be a page or route entry")
    if set(retained_routes) & set(local_files):
        raise ValueError("Retained frontend route cannot also be a local file")
    if len(local_files) != len(raw["local_files"]):
        raise ValueError("Duplicate frontend local path")
    adaptations: dict[str, Adaptation] = {}
    for value in raw["adaptations"]:
        entry = _require_keys(value, frozenset({"path", "reason", "patch"}), "Frontend adaptation")
        path = _validate_local_path(entry["path"])
        if entry["reason"] not in {"node-exclusion", "feature-reduction"}:
            raise ValueError("Frontend adaptation reason must be node-exclusion")
        if not isinstance(entry["patch"], str):
            raise ValueError(f"Frontend adaptation patch must be text: {path}")
        if _is_route_path(path):
            raise ValueError(f"Frontend adaptation cannot target a pruned route: {path}")
        if path in local_files or path in adaptations or path in pruned_files:
            raise ValueError(f"Duplicate or overlapping frontend adaptation: {path}")
        adaptations[path] = Adaptation(
            path=path,
            patch=_validate_path(entry["patch"], "frontend patch path"),
        )
    return FrontendContract(
        local_files=local_files,
        adaptations=adaptations,
        retained_routes=retained_routes,
        pruned_files=pruned_files,
    )


def load_manifest(project_root: Path) -> dict[str, Any]:
    raw = _require_keys(
        _load_json(project_root / MANIFEST_PATH),
        frozenset({"schema_version", "dify", "files", "pruned_routes", "pruned_files", "local_files"}),
        "Frontend manifest",
    )
    if raw["schema_version"] != 2:
        raise ValueError("Frontend manifest schema_version must be 2")
    dify = _require_keys(raw["dify"], frozenset({"commit"}), "Frontend manifest dify")
    if not isinstance(dify["commit"], str) or not dify["commit"]:
        raise ValueError("Frontend manifest Dify commit must be text")
    if not isinstance(raw["files"], list):
        raise ValueError("Frontend manifest files must be an array")

    paths: list[str] = []
    for value in raw["files"]:
        if not isinstance(value, dict):
            raise ValueError("Frontend manifest file entry must be an object")
        kind = value.get("kind")
        if kind == "verbatim":
            entry = _require_keys(value, VERBATIM_KEYS, "Verbatim manifest entry")
        elif kind == "adapted":
            entry = _require_keys(value, ADAPTED_KEYS, "Adapted manifest entry")
        else:
            raise ValueError(f"Unknown frontend manifest entry kind: {kind!r}")
        path = _validate_path(entry["path"], "manifest path")
        source = _validate_path(entry["source"], "manifest source")
        if not _is_snapshot_path(path) or source != _upstream_frontend_path(path) or _is_env_path(path):
            raise ValueError(f"Invalid frontend manifest source mapping: {path}")
        _validate_mode(entry["mode"])
        if kind == "verbatim":
            _validate_hash(entry["sha256"], f"sha256 for {path}")
        else:
            if entry["reason"] not in {"node-exclusion", "feature-reduction"} or not isinstance(entry["patch"], str):
                raise ValueError(f"Invalid adapted frontend entry: {path}")
            _validate_hash(entry["upstream_sha256"], f"upstream_sha256 for {path}")
            _validate_hash(entry["result_sha256"], f"result_sha256 for {path}")
            _validate_path(entry["patch"], "manifest patch path")
            _validate_hash(entry["patch_sha256"], f"patch_sha256 for {path}")
        paths.append(path)
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise ValueError("Frontend manifest files must be sorted and unique")

    if not isinstance(raw["pruned_routes"], list):
        raise ValueError("Frontend manifest pruned_routes must be an array")
    pruned = [_validate_path(path, "pruned route") for path in raw["pruned_routes"]]
    if any(not _is_route_path(path) for path in pruned):
        raise ValueError("Frontend manifest contains a non-route pruned path")
    if pruned != sorted(pruned) or len(pruned) != len(set(pruned)):
        raise ValueError("Frontend manifest pruned_routes must be sorted and unique")
    pruned_files = [_validate_path(path, "pruned file") for path in raw["pruned_files"]]
    if pruned_files != sorted(pruned_files) or len(pruned_files) != len(set(pruned_files)):
        raise ValueError("Frontend manifest pruned_files must be sorted and unique")
    if set(pruned_files) & set(paths):
        raise ValueError("Frontend manifest pruned_files cannot also be retained files")

    if not isinstance(raw["local_files"], list):
        raise ValueError("Frontend manifest local_files must be an array")
    local_paths: list[str] = []
    for value in raw["local_files"]:
        entry = _require_keys(value, frozenset({"path", "sha256"}), "Local manifest entry")
        path = _validate_local_path(entry["path"])
        _validate_hash(entry["sha256"], f"local sha256 for {path}")
        local_paths.append(path)
    if local_paths != sorted(local_paths) or len(local_paths) != len(set(local_paths)):
        raise ValueError("Frontend manifest local_files must be sorted and unique")
    if set(paths) & set(local_paths):
        raise ValueError("Frontend manifest local files cannot also be upstream files")
    return raw


def _locked_commit(project_root: Path) -> str:
    source_lock = _load_json(project_root / SOURCE_LOCK_PATH)
    try:
        commit = source_lock["dify"]["commit"]
    except (KeyError, TypeError) as error:
        raise ValueError("Source lock must contain dify.commit") from error
    if not isinstance(commit, str) or not commit:
        raise ValueError("Source lock dify.commit must be text")
    return commit


def _run_git(source: Path, arguments: list[str]) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        ["git", "-C", str(source), *arguments],
        input=None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"git {arguments[0]} failed: {message}")
    return result


def _archive_member_path(name: str) -> str:
    candidate = name[:-1] if name.endswith("/") else name
    if candidate in {"", "."}:
        return ""
    return _validate_path(candidate, "archive member")


def _apply_patch(path: str, upstream: bytes, patch: str) -> bytes:
    with tempfile.TemporaryDirectory(prefix="frontend-parity-patch-") as temporary:
        root = Path(temporary)
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(upstream)
        result = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", "-"],
            cwd=root,
            input=patch.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if result.returncode:
            message = result.stderr.decode("utf-8", errors="replace").strip()
            raise ValueError(f"Frontend patch does not apply to locked upstream bytes for {path}: {message}")
        files = sorted(
            item.relative_to(root).as_posix()
            for item in root.rglob("*")
            if not item.is_dir() or item.is_symlink()
        )
        if files != [path] or not target.is_file() or target.is_symlink():
            raise ValueError(f"Frontend patch must modify exactly {path}")
        return target.read_bytes()


def _ensure_destination(project_root: Path, relative: str) -> Path:
    current = project_root
    for part in PurePosixPath(relative).parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"Frontend destination crosses a symlink: {relative}")
        if current.exists() and not current.is_dir():
            raise ValueError(f"Frontend destination parent is not a directory: {relative}")
        current.mkdir(exist_ok=True)
    return project_root / relative


def _atomic_write(project_root: Path, relative: str, content: bytes, mode: str | None = None) -> None:
    destination = _ensure_destination(project_root, relative)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".frontend-parity-", dir=destination.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        if mode is not None:
            temporary.chmod(0o755 if mode == "100755" else 0o644)
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _walk_managed(project_root: Path):
    def walk(directory: Path):
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(iterator, key=lambda entry: entry.name)
        except FileNotFoundError:
            return
        for entry in entries:
            path = Path(entry.path)
            relative = path.relative_to(project_root).as_posix()
            if relative == MANIFEST_PATH or _is_env_path(relative) or _is_generated_path(relative):
                continue
            if entry.is_dir(follow_symlinks=False):
                yield from walk(path)
            else:
                yield relative, path, entry

    for root_name in ("web-legacy", "packages"):
        yield from walk(project_root / root_name)


def _cleanup_managed(project_root: Path, keep: set[str]) -> None:
    def clean(directory: Path) -> None:
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(iterator, key=lambda entry: entry.name)
        except FileNotFoundError:
            return
        for entry in entries:
            path = Path(entry.path)
            relative = path.relative_to(project_root).as_posix()
            if relative == MANIFEST_PATH or _is_env_path(relative) or _is_generated_path(relative):
                continue
            if entry.is_dir(follow_symlinks=False):
                clean(path)
                try:
                    path.rmdir()
                except OSError as error:
                    if error.errno not in {errno.ENOTEMPTY, errno.EEXIST, errno.ENOENT}:
                        raise
            elif relative not in keep:
                path.unlink()

    for root_name in ("web-legacy", "packages"):
        clean(project_root / root_name)


def _read_regular_file(path: Path) -> bytes | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(metadata.st_mode):
        return None
    return path.read_bytes()


def _mode_matches(path: Path, expected: str) -> bool:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return False
    executable = bool(mode & 0o111)
    return stat.S_ISREG(mode) and executable == (expected == "100755")


def _check_frontend(project_root: Path) -> ParityReport:
    manifest = load_manifest(project_root)
    contract = load_frontend_contract(project_root)
    if manifest["dify"]["commit"] != _locked_commit(project_root):
        raise ValueError("Frontend manifest Dify commit does not match contracts/sources.json")

    differences: set[str] = set()
    entries = {entry["path"]: entry for entry in manifest["files"]}
    manifest_locals = {entry["path"]: entry for entry in manifest["local_files"]}
    contract_locals = set(contract.local_files)

    for path in sorted(set(manifest_locals) | contract_locals):
        local_entry = manifest_locals.get(path)
        if local_entry is None or path not in contract_locals:
            differences.add(path)
            continue
        content = _read_regular_file(project_root / path)
        if content is None or _sha256(content) != local_entry["sha256"]:
            differences.add(path)

    for path, entry in entries.items():
        expected_hash = entry["sha256"] if entry["kind"] == "verbatim" else entry["result_sha256"]
        content = _read_regular_file(project_root / path)
        if content is None or _sha256(content) != expected_hash or not _mode_matches(project_root / path, entry["mode"]):
            differences.add(path)
        if path in contract_locals:
            differences.add(path)
        if _is_route_path(path) and path not in contract.retained_routes:
            differences.add(path)
        if entry["kind"] == "adapted":
            adaptation = contract.adaptations.get(path)
            if adaptation is None or adaptation.patch != entry["patch"]:
                differences.add(path)
            patch_content = _read_regular_file(project_root / entry["patch"])
            if patch_content is None or _sha256(patch_content) != entry["patch_sha256"]:
                differences.add(path)

    for path in sorted(set(contract.adaptations) - set(entries)):
        differences.add(path)
    for path in [*manifest["pruned_routes"], *manifest["pruned_files"]]:
        if path not in contract_locals and os.path.lexists(project_root / path):
            differences.add(path)

    expected = set(entries) | set(manifest_locals) | contract_locals
    for relative, _, _ in _walk_managed(project_root):
        if relative not in expected:
            differences.add(relative)
    return ParityReport(tuple(sorted(differences)))


def check_frontend(project_root: Path) -> ParityReport:
    project_root = project_root.expanduser().resolve()
    with (project_root / LOCK_PATH).open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _check_frontend(project_root)


def sync_frontend(source_checkout: Path, project_root: Path) -> ParityReport:
    source_checkout = source_checkout.expanduser().resolve()
    project_root = project_root.expanduser().resolve()
    project_root.mkdir(parents=True, exist_ok=True)
    with (project_root / LOCK_PATH).open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        contract = load_frontend_contract(project_root)
        commit = _locked_commit(project_root)
        head = _run_git(source_checkout, ["rev-parse", "HEAD"]).stdout.decode("ascii").strip()
        if head != commit:
            raise ValueError(f"Dify HEAD {head} does not equal locked commit {commit}")

        local_entries = []
        for path in contract.local_files:
            content = _read_regular_file(project_root / path)
            if content is None:
                raise ValueError(f"Registered local frontend file is missing or not regular: {path}")
            local_entries.append({"path": path, "sha256": _sha256(content)})

        with tempfile.TemporaryDirectory(prefix="frontend-parity-") as temporary:
            temporary_root = Path(temporary)
            archive = temporary_root / "source.tar"
            staged = temporary_root / "staged"
            _run_git(source_checkout, ["archive", "--format=tar", f"--output={archive}", commit])
            entries: dict[str, dict[str, Any]] = {}
            pruned: set[str] = set()
            pruned_files: set[str] = set()
            seen: set[str] = set()
            links: dict[str, str] = {}
            with tarfile.open(archive, mode="r:") as source_archive:
                for member in source_archive:
                    upstream_path = _archive_member_path(member.name)
                    if not upstream_path or not _is_snapshot_path(upstream_path):
                        continue
                    relative = _local_frontend_path(upstream_path)
                    if member.isdir():
                        continue
                    if not member.isreg() and not member.issym():
                        raise ValueError(f"Unsafe frontend archive member type: {relative}")
                    if relative in seen:
                        raise ValueError(f"Duplicate frontend archive member: {relative}")
                    seen.add(relative)
                    if _is_env_path(relative) or _is_pruned_test_path(relative):
                        continue
                    if relative in contract.pruned_files:
                        pruned_files.add(relative)
                        continue
                    if _is_route_path(relative) and relative not in contract.retained_routes:
                        pruned.add(relative)
                    if relative in contract.local_files or (
                        _is_route_path(relative) and relative not in contract.retained_routes
                    ):
                        continue
                    if member.issym():
                        target = posixpath.normpath(
                            str(PurePosixPath(upstream_path).parent / member.linkname)
                        )
                        _validate_path(target, "frontend symlink target")
                        if not _is_snapshot_path(target):
                            raise ValueError(f"Frontend symlink leaves the snapshot: {relative}")
                        links[relative] = _local_frontend_path(target)
                        continue
                    extracted = source_archive.extractfile(member)
                    if extracted is None:
                        raise ValueError(f"Frontend archive member has no bytes: {relative}")
                    content = extracted.read()
                    mode = "100755" if member.mode & 0o111 else "100644"
                    staged_path = staged / relative
                    staged_path.parent.mkdir(parents=True, exist_ok=True)
                    staged_path.write_bytes(content)
                    entries[relative] = {
                        "kind": "verbatim",
                        "path": relative,
                        "source": upstream_path,
                        "mode": mode,
                        "sha256": _sha256(content),
                    }

                for relative, target in sorted(links.items()):
                    visited = {relative}
                    while target in links:
                        if target in visited:
                            raise ValueError(f"Frontend symlink cycle: {relative}")
                        visited.add(target)
                        target = links[target]
                    member = source_archive.getmember(_upstream_frontend_path(target))
                    if not member.isreg():
                        raise ValueError(f"Frontend symlink target is not a regular file: {relative}")
                    extracted = source_archive.extractfile(member)
                    if extracted is None:
                        raise ValueError(f"Frontend symlink target has no bytes: {relative}")
                    content = extracted.read()
                    staged_path = staged / relative
                    staged_path.parent.mkdir(parents=True, exist_ok=True)
                    staged_path.write_bytes(content)
                    entries[relative] = {
                        "kind": "verbatim",
                        "path": relative,
                        "source": _upstream_frontend_path(relative),
                        "mode": "100755" if member.mode & 0o111 else "100644",
                        "sha256": _sha256(content),
                    }

            for path, adaptation in sorted(contract.adaptations.items()):
                if path not in entries:
                    raise ValueError(f"Frontend adaptation is not present in locked upstream snapshot: {path}")
                upstream = (staged / path).read_bytes()
                patch_content = (project_root / adaptation.patch).read_text(encoding="utf-8")
                result = _apply_patch(path, upstream, patch_content)
                (staged / path).write_bytes(result)
                entries[path] = {
                    "kind": "adapted",
                    "path": path,
                    "source": _upstream_frontend_path(path),
                    "mode": entries[path]["mode"],
                    "reason": "node-exclusion",
                    "upstream_sha256": _sha256(upstream),
                    "result_sha256": _sha256(result),
                    "patch": adaptation.patch,
                    "patch_sha256": _sha256(patch_content.encode("utf-8")),
                }

            for path, entry in sorted(entries.items()):
                _atomic_write(project_root, path, (staged / path).read_bytes(), entry["mode"])
            _cleanup_managed(project_root, set(entries) | set(contract.local_files))
            manifest = {
                "schema_version": 2,
                "dify": {"commit": commit},
                "files": [entries[path] for path in sorted(entries)],
                "pruned_routes": sorted(pruned),
                "pruned_files": sorted(pruned_files),
                "local_files": local_entries,
            }
            manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
            _atomic_write(project_root, MANIFEST_PATH, manifest_bytes)
        return _check_frontend(project_root)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    sync_parser = commands.add_parser("sync", help="copy the locked Dify frontend snapshot")
    sync_parser.add_argument("--source", type=Path, required=True)
    sync_parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    check_parser = commands.add_parser("check", help="verify the frontend snapshot offline")
    check_parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "sync":
            report = sync_frontend(args.source, args.project_root)
        else:
            report = check_frontend(args.project_root)
    except (OSError, UnicodeError, ValueError, tarfile.TarError, subprocess.SubprocessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(json.dumps({"ok": report.ok, "changed": list(report.differences)}, ensure_ascii=False, indent=2))
    return int(not report.ok)


if __name__ == "__main__":
    raise SystemExit(main())
