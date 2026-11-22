import contextlib
import hashlib
import io
import json
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest

from tools import frontend_parity


ADAPTED_PATH = "web-legacy/app/components/workflow/node-catalog.ts"
UPSTREAM_ADAPTED = b'export const nodes = ["start", "tool", "end"]\n'
RESULT_ADAPTED = b'export const nodes = ["start", "end"]\n'
ADAPTATION_PATCH = f"""diff --git a/{ADAPTED_PATH} b/{ADAPTED_PATH}
--- a/{ADAPTED_PATH}
+++ b/{ADAPTED_PATH}
@@ -1 +1 @@
-export const nodes = [\"start\", \"tool\", \"end\"]
+export const nodes = [\"start\", \"end\"]
"""
ADAPTATION_PATCH_PATH = "contracts/frontend-patches/node-catalog.patch"


class FrontendParityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.source = self.root / "dify"
        self.project = self.root / "project"
        self.source.mkdir()
        (self.project / "contracts").mkdir(parents=True)
        subprocess.run(
            ["git", "init", "--quiet", str(self.source)],
            check=True,
            capture_output=True,
        )
        self.write_upstream("web-legacy/app/components/workflow/index.tsx", b"export const Workflow = () => null\n")
        self.write_upstream(ADAPTED_PATH, UPSTREAM_ADAPTED)
        self.write_upstream("web-legacy/app/layout.tsx", b"export default function Layout() { return null }\n")
        self.write_upstream("web-legacy/app/page.tsx", b"export default function Home() { return null }\n")
        self.write_upstream("web-legacy/app/workflow/page.tsx", b"export default function WorkflowPage() { return null }\n")
        self.write_upstream("web-legacy/app/@slot/page.tsx", b"export default function Slot() { return null }\n")
        self.write_upstream("web-legacy/app/api/health/route.ts", b"export const GET = () => Response.json({})\n")
        self.write_upstream("web-legacy/app/api/public/route.jsx", b"export const GET = () => Response.json({})\n")
        self.write_upstream("web-legacy/app/workflow/layout.tsx", b"export default function WorkflowLayout() { return null }\n")
        self.write_upstream("web-legacy/app/workflow/page.test.tsx", b"export const test = true\n")
        self.write_upstream(
            "web-legacy/app/(auth)/signin/__tests__/page.spec.tsx",
            b"import Page from '../page'\n",
        )
        self.write_upstream("web-legacy/app/(commonLayout)/plugins/page.spec.tsx", b"import './page'\n")
        self.write_upstream("web-legacy/app/(commonLayout)/snippets/[snippetId]/page.spec.ts", b"import './page'\n")
        self.write_upstream(
            "web-legacy/app/components/base/prompt-editor/plugins/__tests__/test-helpers.ts",
            b"export const helper = true\n",
        )
        self.write_upstream(
            "web-legacy/app/components/base/prompt-editor/plugins/__tests__/test-utils.tsx",
            b"export const CaptureEditorPlugin = () => null\n",
        )
        self.write_upstream(
            "web-legacy/app/components/rag-pipeline/__tests__/datasource-fixtures.ts",
            b"export const fixture = true\n",
        )
        self.write_upstream(
            "web-legacy/app/components/workflow/__tests__/node-catalog.spec.ts",
            b"export const excludedCatalog = true\n",
        )
        self.write_upstream(
            "web-legacy/app/components/workflow/nodes/agent/__tests__/panel.spec.ts",
            b"export const excludedAgent = true\n",
        )
        self.write_upstream(
            "web-legacy/app/components/workflow/nodes/_base/components/__tests__/agent-strategy-selector.spec.tsx",
            b"export const excludedAgentStrategy = true\n",
        )
        self.write_upstream(
            "web-legacy/app/components/workflow/nodes/_base/components/__tests__/agent-strategy.spec.tsx",
            b"export const excludedAgentStrategy = true\n",
        )
        self.write_upstream(
            "web-legacy/app/components/workflow/block-selector/__tests__/tool-picker.spec.tsx",
            b"export const excludedToolPicker = true\n",
        )
        self.write_upstream("web-legacy/scripts/run.sh", b"#!/bin/sh\nexit 0\n")
        self.upstream_path("web-legacy/scripts/run.sh").chmod(0o755)
        self.write_upstream("packages/shared/index.ts", b"export const shared = true\n")
        self.write_upstream("patches/upstream.patch", b"diff --git a/example b/example\n")
        self.write_upstream("package.json", b'{"name":"fixture","private":true}\n')
        self.write_upstream("pnpm-lock.yaml", b"lockfileVersion: '9.0'\n")
        self.write_upstream("pnpm-workspace.yaml", b"packages:\n  - web\n  - packages/*\n")
        self.write_upstream("LICENSE", b"fixture license\n")
        self.write_upstream("api/index.py", b"not_frontend = True\n")
        subprocess.run(["git", "-C", str(self.source), "add", "-A"], check=True, capture_output=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.source),
                "-c",
                "user.name=Frontend Parity Test",
                "-c",
                "user.email=frontend-parity@example.invalid",
                "commit",
                "--quiet",
                "-m",
                "fixture",
            ],
            check=True,
            capture_output=True,
        )
        self.commit = self.git("rev-parse", "HEAD")
        self.write_json(
            self.project / "contracts/sources.json",
            {"schema_version": 1, "dify": {"commit": self.commit}},
        )
        self.write_contract()

    def git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(self.source), *args],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    def upstream_path(self, relative: str) -> Path:
        return self.source.joinpath(relative.replace("web-legacy/", "web/", 1))

    def write_upstream(self, relative: str, content: bytes) -> None:
        path = self.upstream_path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def write_project(self, relative: str, content: bytes) -> None:
        path = self.project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def write_json(self, path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def write_contract(
        self,
        local_files: list[str] | None = None,
        adaptations: list[dict] | None = None,
        retained_routes: list[str] | None = None,
        pruned_files: list[str] | None = None,
    ) -> None:
        self.write_json(
            self.project / "contracts/frontend.json",
            {
                "schema_version": 1,
                "local_files": local_files or [],
                "adaptations": adaptations or [],
                "retained_routes": retained_routes or [],
                "pruned_files": pruned_files or [],
            },
        )

    def write_adaptation_contract(self) -> None:
        self.write_project(ADAPTATION_PATCH_PATH, ADAPTATION_PATCH.encode())
        self.write_contract(
            adaptations=[
                {"path": ADAPTED_PATH, "reason": "node-exclusion", "patch": ADAPTATION_PATCH_PATH}
            ]
        )

    def sync(self) -> frontend_parity.ParityReport:
        return frontend_parity.sync_frontend(self.source, self.project)

    def manifest(self) -> dict:
        return json.loads((self.project / "web-legacy/upstream-manifest.json").read_text(encoding="utf-8"))

    def entry(self, path: str) -> dict:
        return next(entry for entry in self.manifest()["files"] if entry["path"] == path)

    def test_sync_copies_the_snapshot_and_check_needs_no_source(self) -> None:
        self.assertTrue(self.sync().ok)

        for relative in (
            "web-legacy/app/components/workflow/index.tsx",
            "packages/shared/index.ts",
            "patches/upstream.patch",
            "package.json",
            "pnpm-lock.yaml",
            "pnpm-workspace.yaml",
            "LICENSE",
        ):
            self.assertEqual((self.project / relative).read_bytes(), (self.upstream_path(relative)).read_bytes())
        self.assertFalse((self.project / "api/index.py").exists())
        self.source.rename(self.root / "source-unavailable")

        self.assertTrue(frontend_parity.check_frontend(self.project).ok)
        self.assertEqual(self.manifest()["schema_version"], 2)
        self.assertEqual(self.manifest()["dify"], {"commit": self.commit})

    def test_check_reports_verbatim_tampering(self) -> None:
        relative = "web-legacy/app/components/workflow/index.tsx"
        self.sync()
        self.write_project(relative, b"rewritten\n")

        report = frontend_parity.check_frontend(self.project)

        self.assertFalse(report.ok)
        self.assertIn(relative, report.differences)

    def test_sync_applies_only_the_registered_adaptation(self) -> None:
        self.write_adaptation_contract()

        self.assertTrue(self.sync().ok)

        self.assertEqual((self.project / ADAPTED_PATH).read_bytes(), RESULT_ADAPTED)
        entry = self.entry(ADAPTED_PATH)
        self.assertEqual(entry["kind"], "adapted")
        self.assertEqual(entry["source"], ADAPTED_PATH.replace("web-legacy/", "web/", 1))
        self.assertEqual(entry["mode"], "100644")
        self.assertEqual(entry["reason"], "node-exclusion")
        self.assertEqual(entry["upstream_sha256"], hashlib.sha256(UPSTREAM_ADAPTED).hexdigest())
        self.assertEqual(entry["result_sha256"], hashlib.sha256(RESULT_ADAPTED).hexdigest())
        self.assertEqual(entry["patch"], ADAPTATION_PATCH_PATH)
        self.assertEqual(entry["patch_sha256"], hashlib.sha256(ADAPTATION_PATCH.encode()).hexdigest())
        self.assertTrue(frontend_parity.check_frontend(self.project).ok)

    def test_check_reports_adapted_result_tampering(self) -> None:
        self.write_adaptation_contract()
        self.sync()
        self.write_project(ADAPTED_PATH, UPSTREAM_ADAPTED)

        report = frontend_parity.check_frontend(self.project)

        self.assertFalse(report.ok)
        self.assertIn(ADAPTED_PATH, report.differences)

    def test_check_reports_patch_tampering(self) -> None:
        self.write_adaptation_contract()
        self.sync()
        self.write_project(
            ADAPTATION_PATCH_PATH,
            ADAPTATION_PATCH.replace("tool", "agent").encode(),
        )

        report = frontend_parity.check_frontend(self.project)

        self.assertFalse(report.ok)
        self.assertIn(ADAPTED_PATH, report.differences)

    def test_sync_prunes_all_route_entries_including_parallel_slots(self) -> None:
        self.sync()

        expected = [
            "web-legacy/app/@slot/page.tsx",
            "web-legacy/app/api/health/route.ts",
            "web-legacy/app/api/public/route.jsx",
            "web-legacy/app/page.tsx",
            "web-legacy/app/workflow/page.tsx",
        ]
        self.assertEqual(self.manifest()["pruned_routes"], expected)
        for relative in expected:
            self.assertFalse((self.project / relative).exists())
        self.assertTrue((self.project / "web-legacy/app/layout.tsx").is_file())
        self.assertTrue((self.project / "web-legacy/app/workflow/layout.tsx").is_file())
        self.assertTrue((self.project / "web-legacy/app/workflow/page.test.tsx").is_file())
        self.assertFalse((self.project / "web-legacy/app/(auth)/signin/__tests__/page.spec.tsx").exists())
        self.assertFalse((self.project / "web-legacy/app/(commonLayout)/plugins/page.spec.tsx").exists())
        self.assertFalse((self.project / "web-legacy/app/(commonLayout)/snippets/[snippetId]/page.spec.ts").exists())
        self.assertTrue(
            (self.project / "web-legacy/app/components/base/prompt-editor/plugins/__tests__/test-helpers.ts").is_file()
        )
        self.assertTrue(
            (self.project / "web-legacy/app/components/base/prompt-editor/plugins/__tests__/test-utils.tsx").is_file()
        )
        self.assertTrue(
            (self.project / "web-legacy/app/components/rag-pipeline/__tests__/datasource-fixtures.ts").is_file()
        )
        self.assertFalse((self.project / "web-legacy/app/components/workflow/__tests__/node-catalog.spec.ts").exists())
        self.assertFalse((self.project / "web-legacy/app/components/workflow/nodes/agent/__tests__/panel.spec.ts").exists())
        self.assertFalse(
            (self.project / "web-legacy/app/components/workflow/nodes/_base/components/__tests__/agent-strategy-selector.spec.tsx").exists()
        )
        self.assertFalse(
            (self.project / "web-legacy/app/components/workflow/nodes/_base/components/__tests__/agent-strategy.spec.tsx").exists()
        )
        self.assertTrue((self.project / "web-legacy/app/components/workflow/block-selector/__tests__/tool-picker.spec.tsx").exists())
        self.assertTrue(frontend_parity.check_frontend(self.project).ok)

    def test_sync_preserves_project_owned_root_files(self) -> None:
        self.write_contract(local_files=["package.json"])
        content = b'{"name":"local-project","private":true}\n'
        self.write_project("package.json", content)

        self.assertTrue(self.sync().ok)
        self.assertEqual((self.project / "package.json").read_bytes(), content)
        self.assertEqual(
            self.manifest()["local_files"],
            [{"path": "package.json", "sha256": hashlib.sha256(content).hexdigest()}],
        )

    def test_sync_prunes_registered_source_files_and_detects_recreation(self) -> None:
        relative = "web-legacy/app/components/workflow/index.tsx"
        self.write_contract(pruned_files=[relative])

        self.assertTrue(self.sync().ok)
        self.assertFalse((self.project / relative).exists())
        self.assertEqual(self.manifest()["pruned_files"], [relative])

        self.write_project(relative, b"restored = true\n")
        self.assertFalse(frontend_parity.check_frontend(self.project).ok)

    def test_sync_retains_an_allowed_upstream_route_as_verbatim(self) -> None:
        retained = "web-legacy/app/workflow/page.tsx"
        self.write_contract(retained_routes=[retained])

        result = self.sync()

        self.assertTrue(result.ok)
        self.assertEqual(self.entry(retained)["kind"], "verbatim")
        self.assertNotIn(retained, self.manifest()["pruned_routes"])
        self.assertEqual(
            (self.project / retained).read_bytes(),
            (self.upstream_path(retained)).read_bytes(),
        )

    def test_sync_preserves_and_hashes_registered_local_files(self) -> None:
        local_route = "web-legacy/app/workflow/page.tsx"
        local_shell = "web-legacy/standalone/shell.ts"
        route_bytes = b"export default function LocalWorkflow() { return null }\n"
        shell_bytes = b"export const standalone = true\n"
        self.write_project(local_route, route_bytes)
        self.write_project(local_shell, shell_bytes)
        self.write_contract(local_files=[local_shell, local_route])

        self.assertTrue(self.sync().ok)

        self.assertEqual((self.project / local_route).read_bytes(), route_bytes)
        self.assertEqual((self.project / local_shell).read_bytes(), shell_bytes)
        self.assertNotIn(local_route, {entry["path"] for entry in self.manifest()["files"]})
        self.assertEqual(
            self.manifest()["local_files"],
            [
                {"path": local_route, "sha256": hashlib.sha256(route_bytes).hexdigest()},
                {"path": local_shell, "sha256": hashlib.sha256(shell_bytes).hexdigest()},
            ],
        )
        self.assertIn(local_route, self.manifest()["pruned_routes"])
        self.assertTrue(frontend_parity.check_frontend(self.project).ok)

        self.write_project(local_shell, b"tampered\n")
        self.assertIn(local_shell, frontend_parity.check_frontend(self.project).differences)

    def test_check_rejects_a_recreated_pruned_route(self) -> None:
        self.sync()
        self.write_project("web-legacy/app/page.tsx", b"unexpected route\n")

        report = frontend_parity.check_frontend(self.project)

        self.assertIn("web-legacy/app/page.tsx", report.differences)

    def test_sync_uses_locked_commit_bytes_despite_a_dirty_worktree(self) -> None:
        relative = "web-legacy/app/components/workflow/index.tsx"
        committed = (self.upstream_path(relative)).read_bytes()
        self.write_upstream(relative, b"dirty worktree\n")
        self.write_upstream("web-legacy/app/untracked.ts", b"untracked\n")

        self.sync()

        self.assertEqual((self.project / relative).read_bytes(), committed)
        self.assertFalse((self.project / "web-legacy/app/untracked.ts").exists())

    def test_sync_rejects_a_head_that_differs_from_the_lock(self) -> None:
        self.write_upstream("web-legacy/app/components/workflow/index.tsx", b"second commit\n")
        self.git("add", "-A")
        self.git(
            "-c",
            "user.name=Frontend Parity Test",
            "-c",
            "user.email=frontend-parity@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "second",
        )

        with self.assertRaisesRegex(ValueError, "HEAD"):
            self.sync()
        self.assertFalse((self.project / "web-legacy/upstream-manifest.json").exists())

    def test_sync_preserves_executable_modes(self) -> None:
        self.sync()

        self.assertTrue((self.project / "web-legacy/scripts/run.sh").stat().st_mode & stat.S_IXUSR)
        self.assertEqual(self.entry("web-legacy/scripts/run.sh")["mode"], "100755")
        self.assertEqual(self.entry("web-legacy/app/layout.tsx")["mode"], "100644")

    def test_sync_removes_stale_sources_and_preserves_generated_and_env_files(self) -> None:
        preserved = {
            "web-legacy/node_modules/fixture/index.js": b"dependency\n",
            "web-legacy/.next/cache": b"build\n",
            "web-legacy/.pytest_cache/state": b"cache\n",
            "packages/shared/dist/index.js": b"build\n",
            "packages/coverage/out.txt": b"coverage\n",
            "web-legacy/.env": b"secret=do-not-read\n",
            "web-legacy/.env.local": b"secret=do-not-read\n",
            "packages/.env.production": b"secret=do-not-read\n",
        }
        for relative, content in preserved.items():
            self.write_project(relative, content)
        self.write_project("web-legacy/stale.ts", b"stale\n")
        self.write_project("packages/stale.ts", b"stale\n")

        self.sync()

        for relative, content in preserved.items():
            self.assertEqual((self.project / relative).read_bytes(), content)
        self.assertFalse((self.project / "web-legacy/stale.ts").exists())
        self.assertFalse((self.project / "packages/stale.ts").exists())
        self.assertTrue(frontend_parity.check_frontend(self.project).ok)

    def test_check_reports_unregistered_files_under_managed_roots(self) -> None:
        self.sync()
        self.write_project("web-legacy/unregistered.ts", b"unexpected\n")
        self.write_project("packages/unregistered.ts", b"unexpected\n")
        self.write_project("other/unregistered.ts", b"outside snapshot scan\n")

        report = frontend_parity.check_frontend(self.project)

        self.assertIn("web-legacy/unregistered.ts", report.differences)
        self.assertIn("packages/unregistered.ts", report.differences)
        self.assertNotIn("other/unregistered.ts", report.differences)

    def test_sync_rerun_converges_and_preserves_local_files(self) -> None:
        local = "web-legacy/app/workflow/page.tsx"
        local_bytes = b"local route\n"
        self.write_project(local, local_bytes)
        self.write_contract(local_files=[local])
        self.sync()
        first_manifest = (self.project / "web-legacy/upstream-manifest.json").read_bytes()
        self.write_project("web-legacy/app/layout.tsx", b"tampered\n")
        self.write_project("packages/stale.ts", b"stale\n")

        self.assertTrue(self.sync().ok)

        self.assertEqual((self.project / local).read_bytes(), local_bytes)
        self.assertEqual((self.project / "web-legacy/app/layout.tsx").read_bytes(), (self.upstream_path("web-legacy/app/layout.tsx")).read_bytes())
        self.assertFalse((self.project / "packages/stale.ts").exists())
        self.assertEqual((self.project / "web-legacy/upstream-manifest.json").read_bytes(), first_manifest)
        self.assertTrue(frontend_parity.check_frontend(self.project).ok)

    def test_contract_rejects_non_node_exclusion_adaptations(self) -> None:
        self.write_contract(adaptations=[{"path": ADAPTED_PATH, "reason": "account", "patch": ADAPTATION_PATCH}])

        with self.assertRaisesRegex(ValueError, "node-exclusion"):
            self.sync()

    def test_check_rejects_a_changed_source_lock(self) -> None:
        self.sync()
        self.write_json(
            self.project / "contracts/sources.json",
            {"schema_version": 1, "dify": {"commit": "0" * 40}},
        )

        with self.assertRaisesRegex(ValueError, "commit"):
            frontend_parity.check_frontend(self.project)

    def test_sync_materializes_a_safe_internal_symlink_as_regular_content(self) -> None:
        self.write_upstream("packages/shared/pets.json", b'{"pets": []}\n')
        link = self.upstream_path("packages/shared/pets.openapi")
        link.symlink_to("pets.json")
        self.write_upstream("api/knowledge-fs-contract/knowledge-fs.openapi.json", b"{}\n")
        external_link = self.upstream_path("packages/openapi-content-mapper/examples/repository/knowledge-fs.openapi")
        external_link.parent.mkdir(parents=True, exist_ok=True)
        external_link.symlink_to("../../../../api/knowledge-fs-contract/knowledge-fs.openapi.json")
        self.git("add", "-A")
        self.git(
            "-c",
            "user.name=Frontend Parity Test",
            "-c",
            "user.email=frontend-parity@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "internal symlink",
        )
        self.commit = self.git("rev-parse", "HEAD")
        self.write_json(
            self.project / "contracts/sources.json",
            {"schema_version": 1, "dify": {"commit": self.commit}},
        )

        result = self.sync()

        self.assertTrue(result.ok)
        target = self.project / "packages/shared/pets.openapi"
        self.assertFalse(target.is_symlink())
        self.assertEqual(target.read_bytes(), b'{"pets": []}\n')
        self.assertFalse(
            (self.project / "packages/openapi-content-mapper/examples/repository/knowledge-fs.openapi").exists()
        )
        self.assertFalse((self.project / "api/knowledge-fs-contract/knowledge-fs.openapi.json").exists())

    def test_public_cli_supports_sync_and_check(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            sync_result = frontend_parity.main(
                ["sync", "--source", str(self.source), "--project-root", str(self.project)]
            )
            check_result = frontend_parity.main(["check", "--project-root", str(self.project)])

        self.assertEqual(sync_result, 0)
        self.assertEqual(check_result, 0)

    def test_cli_check_fails_when_the_manifest_is_missing(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            result = frontend_parity.main(["check", "--project-root", str(self.project)])

        self.assertEqual(result, 1)
        self.assertIn("upstream-manifest.json", output.getvalue())


if __name__ == "__main__":
    unittest.main()
