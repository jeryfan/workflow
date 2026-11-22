import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

from tools import extract_upstream
from tools.extract_upstream import write_extraction


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "tools" / "extract_upstream.py"
MANIFEST = Path("api/src/api/core/graphon/upstream-manifest.json")
VENDOR = Path("api/src/api/core/graphon")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExtractUpstreamTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.dify = self.root / "dify"
        self.dify.mkdir()
        self._git(self.dify, "init", "--quiet")
        self._git(self.dify, "config", "user.email", "tests@example.invalid")
        self._git(self.dify, "config", "user.name", "Tests")
        (self.dify / "README.md").write_text("fixture\n", encoding="utf-8")
        self._git(self.dify, "add", "README.md")
        self._git(self.dify, "commit", "--quiet", "-m", "fixture")
        self.dify_commit = self._git(self.dify, "rev-parse", "HEAD").stdout.strip()
        self.graphon = self.project / ".tools" / "sources" / "graphon"
        self._write_graphon_fixture()
        archive = self._archive_graphon()
        self._write_sources(sha256(archive))

    @staticmethod
    def _git(cwd: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *arguments], cwd=cwd, check=True, capture_output=True, text=True
        )

    def _archive_graphon(self) -> Path:
        archive = self.project / ".tools" / "sources" / "graphon.tar.gz"
        with tarfile.open(archive, "w:gz") as bundle:
            bundle.add(self.graphon, arcname="graphon")
        return archive

    def _write_sources(
        self,
        archive_sha256: str,
        graphon_commit: str = "4fa22141774dff80e90e4dfa98ea3e6fbf0421bb",
    ) -> None:
        contract = {
            "schema_version": 1,
            "dify": {
                "repository": "https://github.com/langgenius/dify",
                "commit": self.dify_commit,
            },
            "graphon": {
                "repository": "https://github.com/langgenius/graphon",
                "commit": graphon_commit,
                "archive_sha256": archive_sha256,
            },
        }
        path = self.project / "contracts" / "sources.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(contract), encoding="utf-8")

    def _write_graphon_fixture(self) -> None:
        package = self.graphon / "src" / "graphon"
        modules = {
            "__init__.py": "",
            "graph/__init__.py": "from .graph import Graph\n",
            "graph/graph.py": "from graphon.runtime import RuntimeState\n",
            "runtime/__init__.py": "from .runtime_state import RuntimeState\n",
            "runtime/runtime_state.py": (
                "from typing import TYPE_CHECKING\n"
                "from graphon.variables.types import SegmentType\n"
                "from graphon.model_runtime.used import Used\n"
                "if TYPE_CHECKING:\n"
                "    from graphon.model_runtime.heavy import Heavy\n"
                "if False:\n"
                "    from graphon.dsl.importer import loads\n"
                "def runtime_import(condition):\n"
                "    if condition:\n"
                "        from graphon.model_runtime.conditional import Conditional\n"
            ),
            "variables/__init__.py": "from .types import SegmentType\n",
            "variables/types.py": "class SegmentType: pass\n",
            "engine/__init__.py": "from .engine import Engine\n",
            "engine/engine.py": "from graphon.node_events import NodeRunResult\n",
            "engine_events/__init__.py": "class GraphRunSucceededEvent: pass\n",
            "node_events/__init__.py": "class NodeRunResult: pass\n",
            "nodes/__init__.py": "",
            "nodes/base/__init__.py": "",
            "nodes/base/node.py": "from ...template_rendering import Renderer\n",
            "nodes/start/__init__.py": "",
            "nodes/start/start_node.py": "from graphon.nodes.base import Node\n",
            "nodes/template_transform/__init__.py": "",
            "nodes/template_transform/template_transform_node.py": (
                "from graphon.nodes.base import Node\n"
            ),
            "nodes/if_else/__init__.py": "from .if_else_node import IfElseNode\n",
            "nodes/if_else/if_else_node.py": "from graphon.nodes.base import Node\n",
            "nodes/variable_aggregator/__init__.py": "from .variable_aggregator_node import VariableAggregatorNode\n",
            "nodes/variable_aggregator/variable_aggregator_node.py": "from graphon.nodes.base import Node\n",
            "nodes/list_operator/__init__.py": "",
            "nodes/list_operator/node.py": "from graphon.nodes.base import Node\n",
            "nodes/end/__init__.py": "",
            "nodes/end/end_node.py": "from graphon.nodes.base import Node\n",
            "nodes/iteration/iteration_node.py": "from graphon.nodes.base import Node\n",
            "nodes/iteration/iteration_start_node.py": "from graphon.nodes.base import Node\n",
            "nodes/loop/loop_node.py": "from graphon.nodes.base import Node\n",
            "nodes/loop/loop_start_node.py": "from graphon.nodes.base import Node\n",
            "nodes/loop/loop_end_node.py": "from graphon.nodes.base import Node\n",
            "nodes/variable_assigner/v2/node.py": "from graphon.nodes.base import Node\n",
            "nodes/code/code_node.py": "from graphon.nodes.base import Node\n",
            "nodes/http_request/node.py": "from graphon.nodes.base import Node\n",
            "dsl/code_runtime.py": "class SandboxCodeExecutor: pass\n",
            "template_rendering.py": "class Renderer: pass\n",
            "model_runtime/__init__.py": "",
            "model_runtime/used.py": "class Used: pass\n",
            "model_runtime/conditional.py": "class Conditional: pass\n",
            "model_runtime/heavy.py": "import transformers\n",
            "dsl/__init__.py": "from .importer import loads\n",
            "dsl/importer.py": "import pandas\n",
            "nodes/tool/__init__.py": "from .tool_node import ToolNode\n",
            "nodes/tool/tool_node.py": "class ToolNode: pass\n",
            "nodes/document_extractor/node.py": "import unstructured\n",
        }
        for relative, content in modules.items():
            path = package / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        (self.graphon / "LICENSE").write_text("Apache fixture\n", encoding="utf-8")
        (self.graphon / "pyproject.toml").write_text(
            '[project]\nname = "graphon"\n', encoding="utf-8"
        )

    def run_tool(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--dify-source",
                str(self.dify),
                "--graphon-source",
                str(self.graphon),
                "--output-root",
                str(self.project),
                *arguments,
            ],
            cwd=self.project,
            capture_output=True,
            text=True,
        )

    def test_extracts_import_closure_with_repeatable_manifest_and_license(self) -> None:
        result = self.run_tool()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("frontend_parity.py", result.stderr)

        manifest_path = self.project / MANIFEST
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        paths = {entry["path"] for entry in manifest["files"]}
        self.assertIn(str(VENDOR / "runtime/runtime_state.py"), paths)
        self.assertIn(str(VENDOR / "model_runtime/used.py"), paths)
        self.assertIn(str(VENDOR / "LICENSE"), paths)
        self.assertNotIn(str(VENDOR / "model_runtime/heavy.py"), paths)
        self.assertIn(str(VENDOR / "dsl/importer.py"), paths)
        self.assertNotIn(str(VENDOR / "nodes/tool/tool_node.py"), paths)
        self.assertNotIn(str(VENDOR / "nodes/document_extractor/node.py"), paths)
        for entry in manifest["files"]:
            extracted = self.project / entry["path"]
            self.assertEqual(sha256(extracted), entry["sha256"])
            self.assertFalse(Path(entry["source"]).is_absolute())
        self.assertNotIn(str(self.graphon), manifest_path.read_text(encoding="utf-8"))

        first_manifest = manifest_path.read_bytes()
        first_files = {
            path.relative_to(self.project).as_posix(): path.read_bytes()
            for path in (self.project / VENDOR).rglob("*")
            if path.is_file()
        }
        repeated = self.run_tool()
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        self.assertEqual(first_manifest, manifest_path.read_bytes())
        second_files = {
            path.relative_to(self.project).as_posix(): path.read_bytes()
            for path in (self.project / VENDOR).rglob("*")
            if path.is_file()
        }
        self.assertEqual(first_files, second_files)

    def test_real_node_implementation_entries_are_extracted(self) -> None:
        result = self.run_tool()
        self.assertEqual(result.returncode, 0, result.stderr)

        manifest = json.loads((self.project / MANIFEST).read_text(encoding="utf-8"))
        paths = {entry["path"] for entry in manifest["files"]}
        for relative in (
            "nodes/base/node.py",
            "nodes/start/start_node.py",
            "nodes/template_transform/template_transform_node.py",
            "nodes/if_else/if_else_node.py",
            "nodes/variable_aggregator/variable_aggregator_node.py",
            "nodes/list_operator/node.py",
            "nodes/end/end_node.py",
        ):
            self.assertIn((VENDOR / relative).as_posix(), paths)

    def test_type_only_and_unreachable_imports_do_not_enter_closure(self) -> None:
        result = self.run_tool()
        self.assertEqual(result.returncode, 0, result.stderr)

        manifest = json.loads((self.project / MANIFEST).read_text(encoding="utf-8"))
        paths = {entry["path"] for entry in manifest["files"]}
        self.assertNotIn((VENDOR / "model_runtime/heavy.py").as_posix(), paths)
        self.assertNotIn((VENDOR / "nodes/tool/tool_node.py").as_posix(), paths)
        self.assertIn((VENDOR / "model_runtime/conditional.py").as_posix(), paths)

    def test_check_detects_stale_file_without_modifying_work_tree(self) -> None:
        self.assertEqual(self.run_tool().returncode, 0)
        target = self.project / VENDOR / "variables/types.py"
        target.write_text("class SegmentType: stale = True\n", encoding="utf-8")
        manifest_before = (self.project / MANIFEST).read_bytes()

        result = self.run_tool("--check")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("stale", result.stderr.lower())
        self.assertIn("stale = True", target.read_text(encoding="utf-8"))
        self.assertEqual(manifest_before, (self.project / MANIFEST).read_bytes())

    def test_archive_source_rejects_wrong_sha256(self) -> None:
        archive = self.project / ".tools" / "sources" / "graphon.tar.gz"
        archive.write_bytes(b"tampered")

        result = self.run_tool()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SHA256", result.stderr)
        self.assertFalse((self.project / MANIFEST).exists())

    def test_archive_source_does_not_read_mutable_sibling_directory(self) -> None:
        source_path = self.graphon / "src/graphon/variables/types.py"
        committed_content = source_path.read_bytes()
        source_path.write_text("class SegmentType: tampered = True\n", encoding="utf-8")

        result = self.run_tool()

        self.assertEqual(result.returncode, 0, result.stderr)
        extracted = self.project / VENDOR / "variables/types.py"
        self.assertEqual(committed_content, extracted.read_bytes())
        self.assertNotIn(b"tampered", extracted.read_bytes())

    def test_git_source_uses_locked_commit_instead_of_dirty_worktree(self) -> None:
        self._git(self.graphon, "init", "--quiet")
        self._git(self.graphon, "config", "user.email", "tests@example.invalid")
        self._git(self.graphon, "config", "user.name", "Tests")
        self._git(self.graphon, "add", ".")
        self._git(self.graphon, "commit", "--quiet", "-m", "fixture")
        graphon_commit = self._git(self.graphon, "rev-parse", "HEAD").stdout.strip()
        archive = self.project / ".tools/sources/graphon.tar.gz"
        self._write_sources(sha256(archive), graphon_commit=graphon_commit)
        source_path = self.graphon / "src/graphon/variables/types.py"
        committed_content = source_path.read_bytes()
        source_path.write_text("class SegmentType: dirty = True\n", encoding="utf-8")

        result = self.run_tool()

        self.assertEqual(result.returncode, 0, result.stderr)
        extracted = self.project / VENDOR / "variables/types.py"
        self.assertEqual(committed_content, extracted.read_bytes())
        self.assertNotIn(b"dirty", extracted.read_bytes())

    def test_archive_rejects_paths_outside_temporary_extraction(self) -> None:
        archive = self.project / ".tools/sources/graphon.tar.gz"
        payload = b"escaped\n"
        with tarfile.open(archive, "w:gz") as bundle:
            member = tarfile.TarInfo("../escaped.py")
            member.size = len(payload)
            bundle.addfile(member, io.BytesIO(payload))
        self._write_sources(sha256(archive))

        result = self.run_tool()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsafe", result.stderr.lower())
        self.assertFalse((self.root / "escaped.py").exists())
        self.assertFalse((self.project / MANIFEST).exists())

    def test_dify_checkout_must_match_locked_commit(self) -> None:
        (self.dify / "README.md").write_text("changed\n", encoding="utf-8")
        self._git(self.dify, "add", "README.md")
        self._git(self.dify, "commit", "--quiet", "-m", "changed")

        result = self.run_tool()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Dify commit mismatch", result.stderr)

    def test_check_detects_orphans_and_next_write_removes_them(self) -> None:
        self.assertEqual(self.run_tool().returncode, 0)
        orphan = self.project / VENDOR / "obsolete.py"
        orphan.write_text("obsolete = True\n", encoding="utf-8")

        checked = self.run_tool("--check")

        self.assertNotEqual(checked.returncode, 0)
        self.assertIn("obsolete.py", checked.stderr)
        self.assertTrue(orphan.is_file())
        written = self.run_tool()
        self.assertEqual(written.returncode, 0, written.stderr)
        self.assertFalse(orphan.exists())

    def test_python_cache_files_are_not_managed_orphans(self) -> None:
        self.assertEqual(self.run_tool().returncode, 0)
        cache = self.project / VENDOR / "__pycache__" / "enums.cpython-313.pyc"
        cache.parent.mkdir()
        cache.write_bytes(b"runtime cache")

        checked = self.run_tool("--check")

        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertEqual(cache.read_bytes(), b"runtime cache")

    def test_output_root_directory_lock_excludes_another_process(self) -> None:
        probe = (
            "import fcntl, os, sys\n"
            "fd = os.open(sys.argv[1], os.O_RDONLY)\n"
            "try:\n"
            "    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
            "except BlockingIOError:\n"
            "    print('locked')\n"
            "else:\n"
            "    print('unlocked')\n"
        )

        with extract_upstream.locked_output_root(self.project):
            result = subprocess.run(
                [sys.executable, "-c", probe, str(self.project)],
                capture_output=True,
                text=True,
                check=True,
            )

        self.assertEqual("locked", result.stdout.strip())

    def test_write_atomically_replaces_files_from_the_same_directory(self) -> None:
        output = self.root / "atomic-project"
        relative = VENDOR / "variables/types.py"
        destination = output / relative
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"old\n")
        replacements = []
        real_replace = os.replace

        def checked_replace(source: Path, target: Path) -> None:
            source = Path(source)
            target = Path(target)
            self.assertEqual(destination.parent, source.parent)
            self.assertEqual(destination, target)
            self.assertEqual(b"new\n", source.read_bytes())
            self.assertEqual(b"old\n", target.read_bytes())
            replacements.append((source, target))
            real_replace(source, target)

        with mock.patch("os.replace", side_effect=checked_replace):
            write_extraction(output, {relative.as_posix(): b"new\n"})

        self.assertTrue(replacements)
        self.assertEqual(b"new\n", destination.read_bytes())

    def test_import_cannot_escape_graphon_package(self) -> None:
        target = self.graphon / "src" / "graphon" / "graph" / "graph.py"
        target.write_text("from ...outside import Secret\n", encoding="utf-8")
        archive = self._archive_graphon()
        self._write_sources(sha256(archive))

        result = self.run_tool()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("outside graphon", result.stderr)
        self.assertFalse((self.project / VENDOR / "outside.py").exists())

    def test_missing_source_fails_clearly(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--dify-source",
                str(self.root / "missing-dify"),
                "--graphon-source",
                str(self.graphon),
                "--output-root",
                str(self.project),
            ],
            capture_output=True,
            text=True,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Dify source does not exist", result.stderr)


if __name__ == "__main__":
    unittest.main()
