from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from api.config import PROJECT_ROOT, get_settings

BASELINE = "0001"
HEAD = "0010"
API_ROOT = PROJECT_ROOT / "api"

LEGACY_COLUMNS = {
    "apps": (
        ("id", "TEXT", 1, None, 1),
        ("workspace_id", "TEXT", 1, None, 0),
        ("name", "TEXT", 1, None, 0),
        ("mode", "TEXT", 1, None, 0),
        ("created_at", "INTEGER", 1, None, 0),
    ),
    "workflow_drafts": (
        ("app_id", "TEXT", 1, None, 1),
        ("workflow_id", "TEXT", 1, None, 0),
        ("graph_json", "TEXT", 1, None, 0),
        ("features_json", "TEXT", 1, None, 0),
        ("environment_variables_json", "TEXT", 1, None, 0),
        ("conversation_variables_json", "TEXT", 1, None, 0),
        ("hash", "TEXT", 1, None, 0),
        ("created_at", "INTEGER", 1, None, 0),
        ("updated_at", "INTEGER", 1, None, 0),
        ("created_by_json", "TEXT", 1, None, 0),
        ("updated_by_json", "TEXT", 1, None, 0),
    ),
    "workflow_runs": (
        ("id", "TEXT", 1, None, 1),
        ("task_id", "TEXT", 1, None, 0),
        ("app_id", "TEXT", 1, None, 0),
        ("workflow_id", "TEXT", 1, None, 0),
        ("draft_hash", "TEXT", 1, None, 0),
        ("graph_snapshot_json", "TEXT", 1, None, 0),
        ("inputs_json", "TEXT", 1, None, 0),
        ("status", "TEXT", 1, None, 0),
        ("outputs_json", "TEXT", 1, None, 0),
        ("error", "TEXT", 0, None, 0),
        ("elapsed_time", "FLOAT", 1, None, 0),
        ("total_tokens", "INTEGER", 1, None, 0),
        ("total_steps", "INTEGER", 1, None, 0),
        ("exceptions_count", "INTEGER", 1, None, 0),
        ("created_by_json", "TEXT", 1, None, 0),
        ("created_at", "INTEGER", 1, None, 0),
        ("finished_at", "INTEGER", 0, None, 0),
    ),
    "node_executions": (
        ("id", "TEXT", 1, None, 1),
        ("run_id", "TEXT", 1, None, 0),
        ("sequence", "INTEGER", 1, None, 0),
        ("node_id", "TEXT", 1, None, 0),
        ("node_type", "TEXT", 1, None, 0),
        ("title", "TEXT", 1, None, 0),
        ("index", "INTEGER", 1, None, 0),
        ("predecessor_node_id", "TEXT", 0, None, 0),
        ("inputs_json", "TEXT", 1, None, 0),
        ("process_data_json", "TEXT", 1, None, 0),
        ("outputs_json", "TEXT", 1, None, 0),
        ("status", "TEXT", 1, None, 0),
        ("error", "TEXT", 0, None, 0),
        ("elapsed_time", "FLOAT", 1, None, 0),
        ("created_at", "INTEGER", 1, None, 0),
        ("finished_at", "INTEGER", 0, None, 0),
    ),
}
LEGACY_FOREIGN_KEYS = {
    "apps": (),
    "workflow_drafts": (("apps", "app_id", "id", "NO ACTION", "NO ACTION", "NONE"),),
    "workflow_runs": (("apps", "app_id", "id", "NO ACTION", "NO ACTION", "NONE"),),
    "node_executions": (("workflow_runs", "run_id", "id", "NO ACTION", "NO ACTION", "NONE"),),
}
LEGACY_INDEXES = {
    "apps": (("pk", ("id",)),),
    "workflow_drafts": (("pk", ("app_id",)), ("u", ("workflow_id",))),
    "workflow_runs": (("pk", ("id",)), ("u", ("task_id",))),
    "node_executions": (("pk", ("id",)), ("u", ("run_id", "sequence"))),
}


class UnknownSchemaError(RuntimeError):
    pass


def alembic_config(database_url: str) -> Config:
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _columns(connection, table: str) -> tuple:
    return tuple(tuple(row[1:]) for row in connection.exec_driver_sql(f'PRAGMA table_info("{table}")'))


def _foreign_keys(connection, table: str) -> tuple:
    rows = connection.exec_driver_sql(f'PRAGMA foreign_key_list("{table}")')
    return tuple(sorted(tuple(row[2:]) for row in rows))


def _indexes(connection, table: str) -> tuple:
    indexes = []
    for row in connection.exec_driver_sql(f'PRAGMA index_list("{table}")'):
        name, origin = row[1], row[3]
        columns = tuple(item[2] for item in connection.exec_driver_sql(f'PRAGMA index_info("{name}")'))
        indexes.append((origin, columns))
    return tuple(sorted(indexes))


def _is_exact_legacy_schema(connection) -> bool:
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    if tables != set(LEGACY_COLUMNS):
        return False
    if (
        inspector.get_view_names()
        or connection.exec_driver_sql(
            "SELECT 1 FROM sqlite_master WHERE type = 'trigger' AND name NOT LIKE 'sqlite_%' LIMIT 1"
        ).first()
    ):
        return False
    return all(
        _columns(connection, table) == LEGACY_COLUMNS[table]
        and _foreign_keys(connection, table) == LEGACY_FOREIGN_KEYS[table]
        and _indexes(connection, table) == LEGACY_INDEXES[table]
        for table in LEGACY_COLUMNS
    )


def upgrade(database_url: str) -> None:
    if database_url.startswith("sqlite:///"):
        database_path = database_url.removeprefix("sqlite:///")
        if database_path != ":memory:":
            Path(database_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            has_version = inspect(connection).has_table("alembic_version")
            has_tables = bool(inspect(connection).get_table_names())
        config = alembic_config(database_url)
        if not has_version and has_tables:
            with engine.connect() as connection:
                if not _is_exact_legacy_schema(connection):
                    raise UnknownSchemaError("Database does not match the known legacy workflow schema")
            command.stamp(config, BASELINE)
        command.upgrade(config, HEAD)
    finally:
        engine.dispose()


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["upgrade"])
    parser.add_argument("--database-url")
    arguments = parser.parse_args(argv)
    database_url = arguments.database_url or get_settings().database_url
    if arguments.command == "upgrade":
        upgrade(database_url)


if __name__ == "__main__":
    main()
