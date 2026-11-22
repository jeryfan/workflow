import asyncio
from logging.config import fileConfig

from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, pool
from sqlalchemy.engine import Connection
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_engine_from_config

import api.models  # noqa: F401
from alembic import context
from api.configs import app_config
from api.models.base import Base

config = context.config

config.set_main_option("sqlalchemy.url", app_config.SQLALCHEMY_DATABASE_URI.replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

LEGACY_BASELINE_COLUMNS = {
    "apps": {"id", "workspace_id", "name", "mode", "created_at"},
    "workflow_drafts": {
        "app_id",
        "workflow_id",
        "graph_json",
        "features_json",
        "environment_variables_json",
        "conversation_variables_json",
        "hash",
        "created_at",
        "updated_at",
        "created_by_json",
        "updated_by_json",
    },
    "workflow_runs": {
        "id",
        "task_id",
        "app_id",
        "workflow_id",
        "draft_hash",
        "graph_snapshot_json",
        "inputs_json",
        "status",
        "outputs_json",
        "error",
        "elapsed_time",
        "total_tokens",
        "total_steps",
        "exceptions_count",
        "created_by_json",
        "created_at",
        "finished_at",
    },
    "node_executions": {
        "id",
        "run_id",
        "sequence",
        "node_id",
        "node_type",
        "title",
        "index",
        "predecessor_node_id",
        "inputs_json",
        "process_data_json",
        "outputs_json",
        "status",
        "error",
        "elapsed_time",
        "created_at",
        "finished_at",
    },
}


def prepare_existing_schema(connection: Connection) -> bool:
    """Stamp only the exact pre-Alembic baseline; reject every other unversioned schema."""
    inspector = inspect(connection)
    tables = {name for name in inspector.get_table_names() if not name.startswith("sqlite_")}
    if not tables or "alembic_version" in tables:
        return False
    matches_baseline = set(tables) == set(LEGACY_BASELINE_COLUMNS) and all(
        {column["name"] for column in inspector.get_columns(table)} == columns
        for table, columns in LEGACY_BASELINE_COLUMNS.items()
    )
    if not matches_baseline:
        raise OperationalError(
            "Alembic schema preflight",
            None,
            RuntimeError("Unknown existing database schema; refusing to mutate it"),
        )
    MigrationContext.configure(connection).stamp(ScriptDirectory.from_config(config), "0001")
    return True


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    def process_revision_directives(migration_context, revision, directives):
        if getattr(config.cmd_opts, "autogenerate", False):
            script = directives[0]
            if script.upgrade_ops.is_empty():
                directives[:] = []
                print("No changes in schema detected.")

    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        process_revision_directives=process_revision_directives,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        stamped = await connection.run_sync(prepare_existing_schema)
        if stamped:
            await connection.commit()
        await connection.run_sync(do_run_migrations)
        await connection.commit()

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
