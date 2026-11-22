#!/usr/bin/env python3

import os
from pathlib import Path
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="workflow-clean-api-") as temporary:
        target = Path(temporary)
        ignore = shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache", ".ruff_cache")
        shutil.copytree(ROOT / "api", target / "api", ignore=ignore)
        shutil.copytree(ROOT / "contracts", target / "contracts", ignore=ignore)
        api = target / "api"
        subprocess.run(
            [
                "uv",
                "sync",
                "--project",
                str(api),
                "--directory",
                str(api),
                "--frozen",
                "--no-dev",
                "--no-editable",
            ],
            check=True,
            cwd=ROOT,
        )
        subprocess.run(
            [
                "uv",
                "run",
                "--project",
                str(api),
                "--directory",
                str(api),
                "--no-sync",
                "python",
                "-c",
                "import api.app; from api.configs import PROJECT_ROOT; "
                "assert (PROJECT_ROOT / 'contracts' / 'nodes.json').is_file()",
            ],
            check=True,
            cwd=ROOT,
            env={
                **os.environ,
                "APP_ENV": "test",
                "WORKFLOW_PROJECT_ROOT": str(target),
                "SECRET_KEY": "clean-import-secret-key-at-least-32-bytes",
                "FILE_SIGNING_KEY": "clean-import-signing-key-at-least-32-bytes",
                "DB_PASSWORD": "clean-import-only",
            },
        )
        subprocess.run(
            ["uv", "run", "--project", str(api), "--directory", str(api), "--group", "dev", "pytest"],
            check=True,
            cwd=ROOT,
        )
        print("Clean-directory API verification passed without source checkouts or an existing virtual environment")


if __name__ == "__main__":
    main()
