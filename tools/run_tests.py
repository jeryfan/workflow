#!/usr/bin/env python3
"""Run the supported project test suite without skipping required behavior."""

from __future__ import annotations

import os
import socket
import subprocess
import time
import urllib.request
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
NODE_BIN = ROOT / ".tools/fnm/node-versions/v24.22.3/installation/bin"
ENV = {
    **os.environ,
    "PATH": f"{NODE_BIN}{os.pathsep}{os.environ.get('PATH', '')}",
    "PYTHONPATH": str(ROOT),
}


def run(label: str, command: list[str], *, env: dict[str, str] | None = None) -> None:
    print(f"\n===== {label} =====", flush=True)
    subprocess.run(command, cwd=ROOT, env=env or ENV, check=True)


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def wait_for(description: str, probe, *, attempts: int = 60) -> None:
    for attempt in range(attempts):
        try:
            probe()
            return
        except (subprocess.CalledProcessError, OSError):
            if attempt == attempts - 1:
                raise RuntimeError(f"{description} did not become ready")
            time.sleep(1)


def backend_integration_tests() -> None:
    suffix = uuid4().hex[:10]
    postgres = f"workflow-test-postgres-{suffix}"
    redis = f"workflow-test-redis-{suffix}"
    rustfs = f"workflow-test-rustfs-{suffix}"
    containers = [postgres, redis, rustfs]
    ports: set[int] = set()
    while len(ports) < 3:
        ports.add(free_port())
    pg_port, redis_port, s3_port = sorted(ports)
    try:
        run(
            "Start isolated PostgreSQL",
            [
                "docker",
                "run",
                "--rm",
                "-d",
                "--name",
                postgres,
                "-e",
                "POSTGRES_USER=workflow",
                "-e",
                "POSTGRES_PASSWORD=workflow-test",
                "-e",
                "POSTGRES_DB=workflow",
                "-p",
                f"127.0.0.1:{pg_port}:5432",
                "postgres:17-alpine",
            ],
        )
        run(
            "Start isolated Redis",
            [
                "docker",
                "run",
                "--rm",
                "-d",
                "--name",
                redis,
                "-p",
                f"127.0.0.1:{redis_port}:6379",
                "redis:8-alpine",
            ],
        )
        run(
            "Start isolated RustFS",
            [
                "docker",
                "run",
                "--rm",
                "-d",
                "--name",
                rustfs,
                "-e",
                "RUSTFS_ACCESS_KEY=workflow-test",
                "-e",
                "RUSTFS_SECRET_KEY=workflow-test-secret",
                "-e",
                "RUSTFS_VOLUMES=/data",
                "-p",
                f"127.0.0.1:{s3_port}:9000",
                "rustfs/rustfs:1.0.5",
            ],
        )
        wait_for(
            "PostgreSQL",
            lambda: subprocess.run(
                [
                    "docker",
                    "exec",
                    postgres,
                    "pg_isready",
                    "-U",
                    "workflow",
                    "-d",
                    "workflow",
                ],
                check=True,
                capture_output=True,
            ),
        )
        wait_for(
            "Redis",
            lambda: subprocess.run(
                ["docker", "exec", redis, "redis-cli", "ping"],
                check=True,
                capture_output=True,
            ),
        )
        wait_for(
            "RustFS",
            lambda: urllib.request.urlopen(
                f"http://127.0.0.1:{s3_port}/health", timeout=2
            ).close(),
        )
        integration_env = {
            **ENV,
            "APP_ENV": "test",
            "DB_HOST": "127.0.0.1",
            "DB_PORT": str(pg_port),
            "DB_USERNAME": "workflow",
            "DB_PASSWORD": "workflow-test",
            "DB_DATABASE": "workflow",
            "SECRET_KEY": "test-secret-key-that-is-at-least-32-bytes",
            "FILE_SIGNING_KEY": "test-signing-key-that-is-at-least-32-bytes",
            "TEST_POSTGRESQL_URL": f"postgresql+asyncpg://workflow:workflow-test@127.0.0.1:{pg_port}/workflow",
            "TEST_REDIS_URL": f"redis://127.0.0.1:{redis_port}/0",
            "TEST_S3_ENDPOINT_URL": f"http://127.0.0.1:{s3_port}",
        }
        run(
            "Apply real PostgreSQL migrations",
            [
                "uv",
                "run",
                "--project",
                "api",
                "--no-sync",
                "alembic",
                "-c",
                "api/alembic.ini",
                "upgrade",
                "head",
            ],
            env=integration_env,
        )
        run(
            "Backend tests with real external services",
            [
                "uv",
                "run",
                "--project",
                "api",
                "--no-sync",
                "pytest",
                "api/tests",
            ],
            env=integration_env,
        )
    finally:
        for container in containers:
            subprocess.run(
                ["docker", "stop", container], check=False, capture_output=True
            )


def main() -> None:
    run(
        "Install locked backend dependencies",
        ["uv", "sync", "--project", "api", "--group", "dev", "--frozen"],
    )
    run(
        "Install locked frontend dependencies",
        ["pnpm", "install", "--frozen-lockfile", "--ignore-scripts"],
    )
    run(
        "Backend lint",
        [
            "uv",
            "run",
            "--project",
            "api",
            "--no-sync",
            "ruff",
            "check",
            "api/src",
            "api/tests",
        ],
    )
    run(
        "Backend formatting",
        [
            "uv",
            "run",
            "--project",
            "api",
            "--no-sync",
            "ruff",
            "format",
            "--check",
            "api/src",
            "api/tests",
        ],
    )
    run(
        "Project tool tests",
        [
            "uv",
            "run",
            "--project",
            "api",
            "--no-sync",
            "python",
            "-m",
            "pytest",
            "tools/tests",
        ],
    )
    run(
        "Frontend reuse contracts",
        ["python3", "tools/frontend_parity.py", "check", "--project-root", "."],
    )
    run(
        "Node registry contracts",
        ["python3", "tools/generate_node_registry.py", "--check"],
    )
    run("Extraction contracts", ["python3", "tools/check_extraction.py"])
    backend_integration_tests()
    run("Legacy frontend types", ["pnpm", "-C", "web-legacy", "type-check"])
    run("Legacy TypeScript rules", ["pnpm", "-C", "web-legacy", "lint:tss"])
    run("Locale consistency", ["pnpm", "-C", "web-legacy", "i18n:check"])
    run(
        "Legacy frontend unit tests",
        [
            "pnpm",
            "-C",
            "web-legacy",
            "exec",
            "vp",
            "test",
            "run",
            "--project",
            "unit",
            "--maxWorkers=4",
        ],
    )
    run("Legacy production build", ["pnpm", "-C", "web-legacy", "build"])
    run("Legacy generated route types", ["pnpm", "-C", "web-legacy", "type-check"])
    run("Vite type check and production build", ["pnpm", "-C", "web", "build"])
    run("Isolated browser component tests", ["node", "tools/test-browser.mjs"])
    print("\nAll supported project tests passed.", flush=True)


if __name__ == "__main__":
    main()
