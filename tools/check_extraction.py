#!/usr/bin/env python3
"""Check extracted sources and generated registries against their contracts."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re
import sys

try:
    from .extract_upstream import MANIFEST_PATH, VENDOR_ROOT
    from .generate_node_registry import (
        PYTHON_REGISTRY,
        RegistryError,
        compare_registries,
        load_nodes,
        render_registries,
    )
except ImportError:
    from extract_upstream import MANIFEST_PATH, VENDOR_ROOT
    from generate_node_registry import (
        PYTHON_REGISTRY,
        RegistryError,
        compare_registries,
        load_nodes,
        render_registries,
    )


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_DEPENDENCIES = (
    "flask",
    "transformers",
    "unstructured",
)
DEPENDENCY_FILES = {
    "pyproject.toml",
    "uv.lock",
    "poetry.lock",
    "pipfile",
    "pipfile.lock",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "setup.py",
    "setup.cfg",
}
SKIPPED_DIRECTORIES = {
    ".audit",
    ".git",
    ".mypy_cache",
    ".next",
    ".pytest_cache",
    ".ruff_cache",
    ".tools",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
}
ABSOLUTE_PATH_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_@])(?:/(?:Users|home|tmp|private|var|mnt|opt|root|workspace|workspaces)/[^\s\"'`<>]*|(?<![A-Za-z0-9])[A-Za-z]:[\\/][^\s\"'`<>]*)",
    re.IGNORECASE,
)
def check_registries(project_root: Path) -> list[str]:
    contract = project_root / "contracts" / "nodes.json"
    try:
        nodes = load_nodes(contract)
        expected = render_registries(contract)
    except RegistryError as error:
        return [str(error)]

    errors = [
        f"Generated registry is stale or missing: {relative}"
        for relative in compare_registries(project_root, expected)
    ]
    python_path = project_root / PYTHON_REGISTRY
    allowed_imports = {
        (node.execution_module, node.execution_name)
        for node in nodes
        if node.executable
    }
    if python_path.is_file():
        try:
            tree = ast.parse(python_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, SyntaxError) as error:
            errors.append(f"Cannot parse generated Python registry {python_path}: {error}")
        else:
            for imported in ast.walk(tree):
                if isinstance(imported, ast.ImportFrom):
                    for alias in imported.names:
                        if (imported.module, alias.name) not in allowed_imports:
                            errors.append(
                                "Python builder import is not whitelisted in generated registry: "
                                f"{imported.module}.{alias.name}"
                            )
    return errors


def check_manifest(project_root: Path) -> list[str]:
    manifest_path = project_root / MANIFEST_PATH
    if not manifest_path.is_file():
        return [f"Extraction manifest does not exist: {MANIFEST_PATH}"]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return [f"Cannot read extraction manifest {MANIFEST_PATH}: {error}"]
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list):
        return [f"Extraction manifest must contain a files array: {MANIFEST_PATH}"]

    errors = []
    for entry in manifest["files"]:
        if not isinstance(entry, dict):
            errors.append("Extraction manifest contains a non-object file entry")
            continue
        relative_value = entry.get("path")
        source = entry.get("source")
        digest = entry.get("sha256")
        if not isinstance(relative_value, str):
            errors.append("Extraction manifest file entry has no relative path")
            continue
        relative = Path(relative_value)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or not relative.is_relative_to(VENDOR_ROOT)
        ):
            errors.append(f"Unsafe extraction manifest path: {relative_value}")
            continue
        if (
            not isinstance(source, str)
            or Path(source).is_absolute()
            or PureWindowsPath(source).is_absolute()
            or ".." in Path(source).parts
        ):
            errors.append(f"Extraction manifest source must be relative: {relative_value}")
        target = project_root / relative
        if not target.is_file():
            errors.append(f"Extracted file is missing: {relative_value}")
            continue
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        if not isinstance(digest, str) or actual != digest:
            errors.append(f"Extracted file SHA256 mismatch: {relative_value}")
    return errors


def is_dependency_declaration(path: Path) -> bool:
    name = path.name.lower()
    return (
        name in DEPENDENCY_FILES
        or (name.startswith("requirements") and name.endswith(".txt"))
        or (name.startswith("constraints") and name.endswith(".txt"))
    )


def production_files(project_root: Path):
    for directory_name in ("api", "web", "web-legacy"):
        directory = project_root / directory_name
        if not directory.is_dir():
            continue
        for path in directory.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(project_root)
            if any(part in SKIPPED_DIRECTORIES for part in relative.parts):
                continue
            yield path


def check_production_files(project_root: Path) -> list[str]:
    errors = []
    for path in production_files(project_root):
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeError, OSError):
            continue
        relative = path.relative_to(project_root).as_posix()
        for match in ABSOLUTE_PATH_PATTERN.finditer(content):
            value = match.group(0)
            if value == "/home/dify":
                continue
            if "dify" in value.lower() or "graphon" in value.lower():
                errors.append(f"Production file contains an absolute checkout path: {relative}")
                break
        if is_dependency_declaration(path):
            for dependency in FORBIDDEN_DEPENDENCIES:
                pattern = re.compile(
                    rf"(?<![a-z0-9_-]){re.escape(dependency)}(?![a-z0-9_-])",
                    re.IGNORECASE,
                )
                if pattern.search(content):
                    errors.append(
                        f"Forbidden {dependency} dependency declaration in {relative}"
                    )
    return errors


def check_project(project_root: Path) -> list[str]:
    errors = []
    errors.extend(check_registries(project_root))
    errors.extend(check_manifest(project_root))
    errors.extend(check_production_files(project_root))
    return errors


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    project_root = args.project_root.expanduser().resolve()
    errors = check_project(project_root)
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 1
    print(json.dumps({"status": "ok", "project_root": str(project_root)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
