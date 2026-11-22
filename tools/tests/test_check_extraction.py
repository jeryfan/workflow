import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "tools" / "check_extraction.py"
GENERATOR = PROJECT_ROOT / "tools" / "generate_node_registry.py"
MANIFEST = Path("api/src/api/core/graphon/upstream-manifest.json")
PYTHON_REGISTRY = Path("api/src/api/services/node_registry.py")


class CheckExtractionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.project = Path(self.temporary_directory.name) / "project"
        contract = self.project / "contracts" / "nodes.json"
        contract.parent.mkdir(parents=True)
        contract.write_bytes((PROJECT_ROOT / "contracts" / "nodes.json").read_bytes())
        generated = subprocess.run(
            [
                sys.executable,
                str(GENERATOR),
                "--output-root",
                str(self.project),
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(generated.returncode, 0, generated.stderr)
        (self.project / "api/src/api/routes").mkdir(parents=True)
        vendored = self.project / MANIFEST.parent / "runtime.py"
        vendored.parent.mkdir(parents=True)
        vendored.write_text("class Runtime: pass\n", encoding="utf-8")
        manifest = {
            "schema_version": 1,
            "files": [
                {
                    "path": vendored.relative_to(self.project).as_posix(),
                    "source": "src/graphon/runtime.py",
                    "sha256": hashlib.sha256(vendored.read_bytes()).hexdigest(),
                }
            ],
        }
        (self.project / MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")

    def run_tool(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--project-root", str(self.project)],
            cwd=self.project,
            capture_output=True,
            text=True,
        )

    def test_valid_project_allows_compatibility_fields(self) -> None:
        console = self.project / "api/src/api/routes/workflows.py"
        console.write_text(
            'RESPONSE = {"tool_published": False, "rag_pipeline_variables": []}\n',
            encoding="utf-8",
        )

        result = self.run_tool()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "ok")

    def test_manifest_hash_mismatch_is_reported(self) -> None:
        vendored = self.project / MANIFEST.parent / "runtime.py"
        vendored.write_text("class Runtime: tampered = True\n", encoding="utf-8")

        result = self.run_tool()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SHA256", result.stderr)
        self.assertIn("runtime.py", result.stderr)

    def test_unwhitelisted_executor_is_rejected_in_python_registry(self) -> None:
        registry = self.project / PYTHON_REGISTRY
        registry.write_text(
            "from graphon.nodes.agent.agent_node import AgentNode\n"
            + registry.read_text(encoding="utf-8"),
            encoding="utf-8",
        )

        result = self.run_tool()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("AgentNode", result.stderr)
        self.assertIn("not whitelisted", result.stderr)

    def test_https_source_url_is_not_a_windows_absolute_path(self) -> None:
        source = self.project / "api/src/api/routes/workflows.py"
        source.write_text('SOURCE = "https://github.com/langgenius/graphon"\n', encoding="utf-8")

        result = self.run_tool()

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_runtime_home_and_package_workspace_urls_are_allowed(self) -> None:
        source = self.project / "api/src/api/routes/workflows.py"
        source.write_text(
            'DOCKER = "adduser -h /home/dify dify"\n'
            'IMPORT = "@dify/contracts/api/console/workspaces/types.gen"\n'
            'DOC = "/use-dify/workspace/subscription-management#dify-for-education"\n',
            encoding="utf-8",
        )

        result = self.run_tool()

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_original_checkout_absolute_path_is_rejected(self) -> None:
        source = self.project / "api/src/graphon_adapter.py"
        source.write_text(
            'UPSTREAM = "/tmp/checkouts/graphon/src/graphon"\n', encoding="utf-8"
        )

        result = self.run_tool()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("absolute checkout path", result.stderr)
        self.assertIn("graphon_adapter.py", result.stderr)

    def test_forbidden_dependency_declarations_are_rejected(self) -> None:
        for dependency in (
            "Flask",
            "Transformers",
            "Unstructured",
        ):
            with self.subTest(dependency=dependency):
                pyproject = self.project / "api/pyproject.toml"
                pyproject.write_text(
                    f'[project]\ndependencies = ["{dependency}>=1"]\n',
                    encoding="utf-8",
                )

                result = self.run_tool()

                self.assertNotEqual(result.returncode, 0)
                self.assertIn(dependency.lower(), result.stderr.lower())
                self.assertIn("dependency declaration", result.stderr)

    def test_dependency_and_tool_directories_are_skipped(self) -> None:
        for directory in (".venv", ".tools", ".audit", ".pytest_cache", ".ruff_cache"):
            declaration = self.project / "api" / directory / "pyproject.toml"
            declaration.parent.mkdir(parents=True)
            declaration.write_text(
                '[project]\ndependencies = ["transformers>=1"]\n'
                'SOURCE = "/tmp/checkouts/graphon/src/graphon"\n',
                encoding="utf-8",
            )

        result = self.run_tool()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "ok")

    def test_missing_node_contract_fails_clearly(self) -> None:
        (self.project / "contracts/nodes.json").unlink()

        result = self.run_tool()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("nodes.json", result.stderr)
        self.assertIn("does not exist", result.stderr)

    def test_absolute_manifest_source_is_rejected(self) -> None:
        manifest_path = self.project / MANIFEST
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"][0]["source"] = "/tmp/checkouts/graphon/src/graphon/runtime.py"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        result = self.run_tool()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("manifest source must be relative", result.stderr)


if __name__ == "__main__":
    unittest.main()
