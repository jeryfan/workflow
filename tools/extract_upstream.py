#!/usr/bin/env python3
"""Vendor the locked Graphon Python import closure."""

from __future__ import annotations

import argparse
import ast
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from collections import deque
from contextlib import contextmanager
from typing import Iterable, Iterator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VENDOR_ROOT = Path("api/src/api/core/graphon")
MANIFEST_PATH = VENDOR_ROOT / "upstream-manifest.json"
ENTRY_MODULES = (
    "graphon.graph",
    "graphon.runtime",
    "graphon.variables",
    "graphon.engine",
    "graphon.engine_events",
    "graphon.node_events",
    "graphon.nodes.base",
    "graphon.nodes.base.node",
    "graphon.nodes.start",
    "graphon.nodes.start.start_node",
    "graphon.nodes.template_transform",
    "graphon.nodes.template_transform.template_transform_node",
    "graphon.nodes.if_else",
    "graphon.nodes.if_else.if_else_node",
    "graphon.nodes.variable_aggregator",
    "graphon.nodes.variable_aggregator.variable_aggregator_node",
    "graphon.nodes.list_operator",
    "graphon.nodes.list_operator.node",
    "graphon.nodes.end",
    "graphon.nodes.end.end_node",
    "graphon.nodes.iteration.iteration_node",
    "graphon.nodes.iteration.iteration_start_node",
    "graphon.nodes.loop.loop_node",
    "graphon.nodes.loop.loop_start_node",
    "graphon.nodes.loop.loop_end_node",
    "graphon.nodes.variable_assigner.v2.node",
    "graphon.nodes.code.code_node",
    "graphon.dsl.code_runtime",
    "graphon.nodes.http_request.node",
    "graphon.template_rendering",
)
FRONTEND_EXTRACTION_OWNER = "tools/frontend_parity.py"


class ExtractionError(Exception):
    pass


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def load_json(path: Path) -> dict:
    if not path.is_file():
        raise ExtractionError(f"Required input does not exist: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ExtractionError(f"Cannot read JSON input {path}: {error}") from error
    if not isinstance(value, dict):
        raise ExtractionError(f"JSON input must be an object: {path}")
    return value


def git_head(source: Path, label: str) -> str | None:
    if not source.is_dir():
        raise ExtractionError(f"{label} source does not exist: {source}")
    result = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    head = result.stdout.strip()
    if not head:
        raise ExtractionError(f"Git returned an empty HEAD for {label} source: {source}")
    return head


def validate_dify_source(source: Path, expected_commit: str) -> str:
    actual_commit = git_head(source, "Dify")
    if actual_commit is None:
        raise ExtractionError(f"Dify source is not a Git checkout: {source}")
    if actual_commit != expected_commit:
        raise ExtractionError(
            f"Dify commit mismatch: expected {expected_commit}, got {actual_commit}"
        )
    return actual_commit


def validate_archive_layout(source: Path) -> None:
    package = source / "src" / "graphon"
    if not package.is_dir():
        raise ExtractionError(
            f"Graphon archive source must contain src/graphon: {source}"
        )
    if "graphon" in source.name.lower():
        return
    pyproject = source / "pyproject.toml"
    if not pyproject.is_file():
        raise ExtractionError(
            f"Archive directory name does not identify Graphon and pyproject.toml is missing: {source}"
        )
    try:
        project_name = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["name"]
    except (OSError, UnicodeError, tomllib.TOMLDecodeError, KeyError, TypeError) as error:
        raise ExtractionError(f"Cannot identify Graphon archive layout: {source}") from error
    if project_name != "graphon":
        raise ExtractionError(f"Archive source is not Graphon: {source}")


def extract_archive_safely(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True)
    try:
        with tarfile.open(archive, mode="r:*") as bundle:
            members = bundle.getmembers()
            for member in members:
                relative = PurePosixPath(member.name)
                if (
                    relative.is_absolute()
                    or ".." in relative.parts
                    or not (member.isfile() or member.isdir())
                    or (member.isfile() and not relative.parts)
                ):
                    raise ExtractionError(
                        f"Unsafe Graphon archive member: {member.name}"
                    )
            for member in members:
                target = destination.joinpath(*PurePosixPath(member.name).parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                source = bundle.extractfile(member)
                if source is None:
                    raise ExtractionError(
                        f"Cannot read Graphon archive member: {member.name}"
                    )
                with source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
    except tarfile.TarError as error:
        raise ExtractionError(f"Cannot read Graphon archive {archive}: {error}") from error


def locate_graphon_snapshot(extracted: Path) -> Path:
    candidates = [extracted]
    candidates.extend(
        sorted(path for path in extracted.iterdir() if path.is_dir())
    )
    for candidate in candidates:
        if (candidate / "src" / "graphon").is_dir():
            return candidate
    raise ExtractionError("Graphon archive snapshot must contain src/graphon")


def materialize_graphon_source(
    source: Path, contract: dict, temporary_root: Path
) -> tuple[Path, str]:
    expected_commit = str(contract["commit"])
    actual_commit = git_head(source, "Graphon")
    if actual_commit is not None:
        if actual_commit != expected_commit:
            raise ExtractionError(
                f"Graphon commit mismatch: expected {expected_commit}, got {actual_commit}"
            )
        archive = temporary_root / "graphon-git.tar"
        with archive.open("wb") as output:
            result = subprocess.run(
                ["git", "-C", str(source), "archive", expected_commit],
                stdout=output,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
        if result.returncode != 0:
            raise ExtractionError(
                f"Cannot archive locked Graphon commit {expected_commit}: "
                f"{result.stderr.strip()}"
            )
    else:
        archive = source.parent / "graphon.tar.gz"
        if not archive.is_file():
            raise ExtractionError(
                f"Graphon archive checksum input does not exist beside the source: {archive}"
            )
        expected_sha256 = str(contract.get("archive_sha256", ""))
        actual_sha256 = sha256_file(archive)
        if actual_sha256 != expected_sha256:
            raise ExtractionError(
                f"Graphon archive SHA256 mismatch: expected {expected_sha256}, got {actual_sha256}"
            )

    extracted = temporary_root / "snapshot"
    extract_archive_safely(archive, extracted)
    snapshot = locate_graphon_snapshot(extracted)
    if actual_commit is None:
        validate_archive_layout(snapshot)
    return snapshot, expected_commit


def module_path(package_root: Path, module: str) -> Path | None:
    if module != "graphon" and not module.startswith("graphon."):
        return None
    relative_parts = module.split(".")[1:]
    base = package_root.joinpath(*relative_parts)
    candidates = []
    if not relative_parts:
        candidates.append(package_root / "__init__.py")
    else:
        candidates.extend((base.with_suffix(".py"), base / "__init__.py"))
    existing = [candidate for candidate in candidates if candidate.is_file()]
    if not existing:
        return None
    if len(existing) > 1:
        raise ExtractionError(f"Ambiguous Graphon module layout for {module}")
    resolved = existing[0].resolve()
    if not resolved.is_relative_to(package_root):
        raise ExtractionError(f"Module {module} resolves outside graphon: {resolved}")
    return resolved


def is_package_module(package_root: Path, module: str) -> bool:
    path = module_path(package_root, module)
    return path is not None and path.name == "__init__.py"


def resolve_relative_module(
    package_root: Path, current_module: str, level: int, imported_module: str | None
) -> str:
    parts = current_module.split(".")
    if not is_package_module(package_root, current_module):
        parts = parts[:-1]
    parents_to_remove = level - 1
    if parents_to_remove >= len(parts):
        raise ExtractionError(
            f"Relative import in {current_module} resolves outside graphon"
        )
    base = parts[: len(parts) - parents_to_remove] if parents_to_remove else parts
    if not base or base[0] != "graphon":
        raise ExtractionError(
            f"Relative import in {current_module} resolves outside graphon"
        )
    if imported_module:
        base.extend(imported_module.split("."))
    return ".".join(base)


def is_statically_false(test: ast.expr) -> bool:
    if isinstance(test, ast.Constant):
        return not bool(test.value)
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    return (
        isinstance(test, ast.Attribute)
        and test.attr == "TYPE_CHECKING"
        and isinstance(test.value, ast.Name)
        and test.value.id == "typing"
    )


def runtime_import_nodes(tree: ast.AST) -> Iterable[ast.Import | ast.ImportFrom]:
    if isinstance(tree, (ast.Import, ast.ImportFrom)):
        yield tree
        return
    if isinstance(tree, ast.If) and is_statically_false(tree.test):
        children = tree.orelse
    else:
        children = ast.iter_child_nodes(tree)
    for child in children:
        yield from runtime_import_nodes(child)


def imported_modules(package_root: Path, module: str, path: Path) -> Iterable[tuple[str, bool]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeError, SyntaxError) as error:
        raise ExtractionError(f"Cannot parse Graphon module {path}: {error}") from error

    for node in runtime_import_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "graphon" or alias.name.startswith("graphon."):
                    yield alias.name, True
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = resolve_relative_module(package_root, module, node.level, node.module)
            else:
                base = node.module or ""
            if base != "graphon" and not base.startswith("graphon."):
                continue
            yield base, True
            for alias in node.names:
                if alias.name != "*":
                    yield f"{base}.{alias.name}", False


def scan_import_closure(graphon_source: Path) -> dict[str, Path]:
    package_root = (graphon_source / "src" / "graphon").resolve()
    if not package_root.is_dir():
        raise ExtractionError(
            f"Graphon source must contain src/graphon: {graphon_source}"
        )
    pending = deque(ENTRY_MODULES)
    resolved_modules: dict[str, Path] = {}
    scanned: set[Path] = set()

    def require(module: str, required: bool) -> None:
        path = module_path(package_root, module)
        if path is None:
            if required:
                raise ExtractionError(f"Graphon import cannot be resolved: {module}")
            return
        if module in resolved_modules:
            return
        resolved_modules[module] = path
        pending.append(module)
        parts = module.split(".")
        for length in range(1, len(parts)):
            parent = ".".join(parts[:length])
            if parent not in resolved_modules:
                parent_path = module_path(package_root, parent)
                if parent_path is not None:
                    resolved_modules[parent] = parent_path
                    pending.append(parent)

    while pending:
        module = pending.popleft()
        path = resolved_modules.get(module)
        if path is None:
            require(module, True)
            path = resolved_modules[module]
        if path in scanned:
            continue
        scanned.add(path)
        for imported, required in imported_modules(package_root, module, path):
            require(imported, required)

    return resolved_modules


def build_extraction(project_root: Path, dify_source: Path, graphon_source: Path) -> tuple[dict, dict[str, bytes]]:
    contract = load_json(project_root / "contracts" / "sources.json")
    try:
        dify_contract = contract["dify"]
        graphon_contract = contract["graphon"]
        dify_commit = validate_dify_source(
            dify_source, str(dify_contract["commit"])
        )
        with tempfile.TemporaryDirectory(prefix="extract-upstream-source-") as temporary:
            snapshot, graphon_commit = materialize_graphon_source(
                graphon_source, graphon_contract, Path(temporary)
            )
            modules = scan_import_closure(snapshot)
            package_root = (snapshot / "src" / "graphon").resolve()
            files: dict[str, bytes] = {}
            entries = []
            for _, source_path in sorted(
                modules.items(),
                key=lambda item: item[1].relative_to(package_root).as_posix(),
            ):
                relative = source_path.relative_to(package_root)
                destination = (VENDOR_ROOT / relative).as_posix()
                content = source_path.read_bytes().replace(
                    b"from graphon", b"from api.core.graphon"
                ).replace(b"import graphon", b"import api.core.graphon")
                files[destination] = content
                entries.append(
                    {
                        "path": destination,
                        "source": (Path("src/graphon") / relative).as_posix(),
                        "sha256": sha256_bytes(content),
                    }
                )

            license_path = snapshot / "LICENSE"
            if not license_path.is_file():
                raise ExtractionError(f"Graphon LICENSE is missing: {license_path}")
            license_content = license_path.read_bytes()
            license_destination = (VENDOR_ROOT / "LICENSE").as_posix()
            files[license_destination] = license_content
            entries.append(
                {
                    "path": license_destination,
                    "source": "LICENSE",
                    "sha256": sha256_bytes(license_content),
                }
            )
    except KeyError as error:
        raise ExtractionError(f"Missing sources.json field: {error}") from error

    entries.sort(key=lambda entry: entry["path"])
    manifest = {
        "schema_version": 1,
        "dify": {
            "commit": dify_commit,
            "repository": contract["dify"].get("repository"),
        },
        "graphon": {
            "commit": graphon_commit,
            "repository": graphon_contract.get("repository"),
        },
        "unsupported": {"dify_typescript": []},
        "files": entries,
    }
    files[MANIFEST_PATH.as_posix()] = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    return manifest, files


@contextmanager
def locked_output_root(project_root: Path) -> Iterator[None]:
    descriptor = os.open(project_root, os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def actual_vendor_paths(project_root: Path) -> set[str]:
    vendor_root = project_root / VENDOR_ROOT
    if not vendor_root.is_dir():
        return set()
    return {
        path.relative_to(project_root).as_posix()
        for path in vendor_root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }


def atomic_write(destination: Path, content: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and destination.read_bytes() == content:
        return
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def write_extraction(project_root: Path, files: dict[str, bytes]) -> None:
    for stale in sorted(actual_vendor_paths(project_root) - files.keys()):
        (project_root / stale).unlink(missing_ok=True)
    for relative, content in sorted(files.items()):
        atomic_write(project_root / relative, content)


def compare_extraction(project_root: Path, files: dict[str, bytes]) -> list[str]:
    differences = []
    for relative, content in sorted(files.items()):
        actual = project_root / relative
        if not actual.is_file() or actual.read_bytes() != content:
            differences.append(relative)
    for stale in sorted(actual_vendor_paths(project_root) - files.keys()):
        differences.append(f"{stale} (unexpected)")
    return differences


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dify-source", required=True, type=Path)
    parser.add_argument("--graphon-source", required=True, type=Path)
    parser.add_argument(
        "--output-root",
        "--target-root",
        dest="output_root",
        type=Path,
        default=PROJECT_ROOT,
    )
    parser.add_argument("--check", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        project_root = args.output_root.expanduser().resolve()
        with locked_output_root(project_root):
            manifest, files = build_extraction(
                project_root,
                args.dify_source.expanduser().resolve(),
                args.graphon_source.expanduser().resolve(),
            )
            if args.check:
                differences = compare_extraction(project_root, files)
                if differences:
                    raise ExtractionError(
                        "Stale managed extraction files: " + ", ".join(differences)
                    )
            else:
                write_extraction(project_root, files)
        print(
            "Dify TypeScript snapshot is owned by " + FRONTEND_EXTRACTION_OWNER,
            file=sys.stderr,
        )
        print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (ExtractionError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
