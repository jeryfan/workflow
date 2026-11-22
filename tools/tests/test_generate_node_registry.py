import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "tools" / "generate_node_registry.py"
PYTHON_REGISTRY = Path("api/src/api/services/node_registry.py")
TYPESCRIPT_EXCLUSIONS = Path("web-legacy/standalone/node-exclusions.generated.ts")
LEGACY_TYPESCRIPT_REGISTRY = Path("web-legacy/app/components/workflow/nodes/registry.ts")


class GenerateNodeRegistryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.project = Path(self.temporary_directory.name) / "project"
        self.contract = self.project / "contracts" / "nodes.json"
        self.contract.parent.mkdir(parents=True)
        self.contract.write_bytes((PROJECT_ROOT / "contracts" / "nodes.json").read_bytes())

    def run_tool(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--output-root", str(self.project), *arguments],
            cwd=self.project,
            capture_output=True,
            text=True,
        )

    def test_generates_backend_builders_and_frontend_allowlists(self) -> None:
        result = self.run_tool()
        self.assertEqual(result.returncode, 0, result.stderr)

        python = (self.project / PYTHON_REGISTRY).read_text(encoding="utf-8")
        self.assertIn("from graphon.nodes.start import StartNode", python)
        self.assertIn(
            "from graphon.nodes.template_transform.template_transform_node import TemplateTransformNode",
            python,
        )
        self.assertIn('"start": StartNode', python)
        self.assertIn('"template-transform": TemplateTransformNode', python)
        self.assertIn('"end": EndNode', python)
        self.assertNotIn("start-placeholder", python)
        self.assertNotIn("knowledge-retrieval", python)

        typescript = (self.project / TYPESCRIPT_EXCLUSIONS).read_text(encoding="utf-8")
        supported = typescript.split("const supportedNodeTypes", 1)[1].split("const catalogNodeTypes", 1)[0]
        catalog = typescript.split("const catalogNodeTypes", 1)[1].split("const excludedSelectorPanels", 1)[0]
        for node_type in (
            "knowledge-retrieval",
            "knowledge-retrieval-v2",
            "knowledge-index",
            "datasource",
            "datasource-empty",
            "trigger-plugin",
            "agent",
            "agent-v2",
        ):
            self.assertNotIn(f'"{node_type}"', typescript)
        for retained in ("start", "template-transform", "end", "start-placeholder", "assigner", "loop-end"):
            self.assertIn(f'"{retained}"', supported)
        for retained in ("start", "loop", "tool", "trigger-schedule", "trigger-webhook"):
            self.assertIn(f'"{retained}"', catalog)
        for internal in ("start-placeholder", "assigner", "loop-end", "iteration-start"):
            self.assertNotIn(f'"{internal}"', catalog)
        self.assertIn("=> !supportedNodeTypes.has(type)", typescript)
        self.assertIn("=> !catalogNodeTypes.has(type)", typescript)
        self.assertIn("export const isExcludedCatalogNodeType", typescript)
        self.assertIn("export const isExcludedSelectorPanel", typescript)
        self.assertNotIn('"tools"', typescript)
        self.assertIn('"sources"', typescript)
        self.assertFalse((self.project / LEGACY_TYPESCRIPT_REGISTRY).exists())

    def test_check_detects_stale_exclusions_without_rewriting_them(self) -> None:
        self.assertEqual(self.run_tool().returncode, 0)
        target = self.project / TYPESCRIPT_EXCLUSIONS
        target.write_text("export const stale = true\n", encoding="utf-8")
        python_before = (self.project / PYTHON_REGISTRY).read_bytes()

        result = self.run_tool("--check")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("stale", result.stderr.lower())
        self.assertIn("stale", target.read_text(encoding="utf-8"))
        self.assertEqual(python_before, (self.project / PYTHON_REGISTRY).read_bytes())

    def test_check_reports_missing_outputs(self) -> None:
        result = self.run_tool("--check")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(str(PYTHON_REGISTRY), result.stderr)
        self.assertIn(str(TYPESCRIPT_EXCLUSIONS), result.stderr)
        self.assertFalse((self.project / PYTHON_REGISTRY).exists())

    def test_unresolvable_execution_class_fails_without_outputs(self) -> None:
        contract = json.loads(self.contract.read_text(encoding="utf-8"))
        contract["nodes"][0]["execution_class"] = "StartNode"
        self.contract.write_text(json.dumps(contract), encoding="utf-8")

        result = self.run_tool()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("execution_class", result.stderr)
        self.assertFalse((self.project / PYTHON_REGISTRY).exists())
        self.assertFalse((self.project / TYPESCRIPT_EXCLUSIONS).exists())

    def test_excluded_node_cannot_have_an_executor(self) -> None:
        contract = json.loads(self.contract.read_text(encoding="utf-8"))
        excluded = next(node for node in contract["nodes"] if node["type"] == "agent")
        excluded["execution_class"] = "graphon.nodes.agent.agent_node.AgentNode"
        self.contract.write_text(json.dumps(contract), encoding="utf-8")

        result = self.run_tool()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("excluded", result.stderr.lower())
        self.assertFalse((self.project / PYTHON_REGISTRY).exists())

    def test_missing_contract_fails_clearly(self) -> None:
        self.contract.unlink()

        result = self.run_tool()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("nodes.json", result.stderr)
        self.assertIn("does not exist", result.stderr)


if __name__ == "__main__":
    unittest.main()
