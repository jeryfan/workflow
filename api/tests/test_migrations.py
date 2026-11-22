import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from alembic.config import Config
from sqlalchemy.exc import OperationalError

from alembic import command
from api.configs import app_config

API_ROOT = Path(__file__).resolve().parents[1]
HEAD = "0011"


def upgrade(database_url: str) -> None:
    async_url = database_url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
    config = Config(API_ROOT / "alembic.ini")
    with patch.object(type(app_config), "SQLALCHEMY_DATABASE_URI", new=property(lambda _self: async_url)):
        command.upgrade(config, "head")


LEGACY_DDL = """
CREATE TABLE apps (
    id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    name TEXT NOT NULL,
    mode TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (id)
);
CREATE TABLE workflow_drafts (
    app_id TEXT NOT NULL,
    workflow_id TEXT NOT NULL,
    graph_json TEXT NOT NULL,
    features_json TEXT NOT NULL,
    environment_variables_json TEXT NOT NULL,
    conversation_variables_json TEXT NOT NULL,
    hash TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    created_by_json TEXT NOT NULL,
    updated_by_json TEXT NOT NULL,
    PRIMARY KEY (app_id),
    FOREIGN KEY(app_id) REFERENCES apps (id),
    UNIQUE (workflow_id)
);
CREATE TABLE workflow_runs (
    id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    app_id TEXT NOT NULL,
    workflow_id TEXT NOT NULL,
    draft_hash TEXT NOT NULL,
    graph_snapshot_json TEXT NOT NULL,
    inputs_json TEXT NOT NULL,
    status TEXT NOT NULL,
    outputs_json TEXT NOT NULL,
    error TEXT,
    elapsed_time FLOAT NOT NULL,
    total_tokens INTEGER NOT NULL,
    total_steps INTEGER NOT NULL,
    exceptions_count INTEGER NOT NULL,
    created_by_json TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    finished_at INTEGER,
    PRIMARY KEY (id),
    FOREIGN KEY(app_id) REFERENCES apps (id),
    UNIQUE (task_id)
);
CREATE TABLE node_executions (
    id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    node_id TEXT NOT NULL,
    node_type TEXT NOT NULL,
    title TEXT NOT NULL,
    "index" INTEGER NOT NULL,
    predecessor_node_id TEXT,
    inputs_json TEXT NOT NULL,
    process_data_json TEXT NOT NULL,
    outputs_json TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT,
    elapsed_time FLOAT NOT NULL,
    created_at INTEGER NOT NULL,
    finished_at INTEGER,
    PRIMARY KEY (id),
    FOREIGN KEY(run_id) REFERENCES workflow_runs (id),
    UNIQUE (run_id, sequence)
);
"""

EXPECTED_TABLES = {
    "accounts",
    "alembic_version",
    "app_sites",
    "apps",
    "installation_state",
    "node_executions",
    "workflow_drafts",
    "workflow_runs",
    "workflow_versions",
    "workflow_tools",
    "workflow_triggers",
    "uploaded_files",
}


def create_legacy_database(path: Path, with_rows: bool = False) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(LEGACY_DDL)
        if not with_rows:
            return
        connection.execute(
            "INSERT INTO apps VALUES (?, ?, ?, ?, ?)",
            ("app-1", "local-workspace", "Legacy workflow", "workflow", 10),
        )
        connection.execute(
            "INSERT INTO workflow_drafts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "app-1",
                "workflow-1",
                '{"nodes": [{"id": "legacy-node"}], "edges": []}',
                "{}",
                "[]",
                "[]",
                "legacy-hash",
                11,
                12,
                '{"id": "local-user", "name": "Local User"}',
                '{"id": "local-user", "name": "Local User"}',
            ),
        )
        connection.execute(
            "INSERT INTO workflow_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "run-1",
                "task-1",
                "app-1",
                "workflow-1",
                "legacy-hash",
                '{"nodes": [{"id": "legacy-node"}], "edges": []}',
                '{"name": "before"}',
                "succeeded",
                '{"message": "preserved"}',
                None,
                0.25,
                3,
                1,
                0,
                '{"id": "local-user", "name": "Local User"}',
                13,
                14,
            ),
        )
        connection.execute(
            "INSERT INTO node_executions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "execution-1",
                "run-1",
                1,
                "legacy-node",
                "template-transform",
                "Legacy node",
                1,
                None,
                '{"name": "before"}',
                "{}",
                '{"message": "preserved"}',
                "succeeded",
                None,
                0.25,
                13,
                14,
            ),
        )


def workflow_rows(path: Path) -> tuple:
    with sqlite3.connect(path) as connection:
        return (
            connection.execute("SELECT id, name, mode, created_at FROM apps ORDER BY id").fetchall(),
            connection.execute("SELECT * FROM workflow_drafts ORDER BY app_id").fetchall(),
            connection.execute(
                "SELECT id, task_id, app_id, workflow_id, draft_hash, graph_snapshot_json, inputs_json, "
                "status, outputs_json, error, elapsed_time, total_tokens, total_steps, exceptions_count, "
                "created_by_json, created_at, finished_at FROM workflow_runs ORDER BY id"
            ).fetchall(),
            connection.execute("SELECT * FROM node_executions ORDER BY id").fetchall(),
        )


def schema_snapshot(path: Path) -> tuple:
    with sqlite3.connect(path) as connection:
        tables = tuple(
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        )
        return tuple(
            (
                table,
                tuple(connection.execute(f'PRAGMA table_info("{table}")').fetchall()),
                tuple(sorted(row[2:] for row in connection.execute(f'PRAGMA foreign_key_list("{table}")').fetchall())),
                tuple(connection.execute(f'PRAGMA index_list("{table}")').fetchall()),
            )
            for table in tables
        )


def test_legacy_upgrade_preserves_workflow_data_and_rerun_is_idempotent(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy.db"
    create_legacy_database(database_path, with_rows=True)
    before = workflow_rows(database_path)

    upgrade(f"sqlite:///{database_path}")
    after_first_upgrade = workflow_rows(database_path)
    schema_after_first_upgrade = schema_snapshot(database_path)
    upgrade(f"sqlite:///{database_path}")

    assert after_first_upgrade == before
    assert workflow_rows(database_path) == before
    assert schema_snapshot(database_path) == schema_after_first_upgrade
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (HEAD,)
        assert connection.execute("SELECT maintainer_id, created_by_id, updated_by_id FROM apps").fetchone() == (
            None,
            None,
            None,
        )


def test_fresh_and_legacy_databases_converge_on_the_same_schema(tmp_path: Path) -> None:
    fresh_path = tmp_path / "fresh.db"
    legacy_path = tmp_path / "legacy.db"
    create_legacy_database(legacy_path)

    upgrade(f"sqlite:///{fresh_path}")
    with sqlite3.connect(fresh_path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (HEAD,)
    assert tables == EXPECTED_TABLES

    upgrade(f"sqlite:///{legacy_path}")
    assert schema_snapshot(fresh_path) == schema_snapshot(legacy_path)


def test_unknown_partial_schema_fails_before_any_mutation(tmp_path: Path) -> None:
    database_path = tmp_path / "unknown.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE apps (id TEXT PRIMARY KEY)")
        connection.execute("INSERT INTO apps VALUES ('do-not-change')")
        before = connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
        ).fetchall()

    with pytest.raises(OperationalError):
        upgrade(f"sqlite:///{database_path}")

    with sqlite3.connect(database_path) as connection:
        after = connection.execute("SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name").fetchall()
        assert connection.execute("SELECT * FROM apps").fetchall() == [("do-not-change",)]
    assert after == before
