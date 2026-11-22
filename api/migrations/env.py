from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config
target_metadata = None


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        sqlite = connection.dialect.name == "sqlite"
        if sqlite:
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            connection.commit()
        try:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
            if sqlite:
                violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
                if violations:
                    raise RuntimeError(f"Migration produced invalid foreign keys: {violations}")
        finally:
            if sqlite:
                connection.exec_driver_sql("PRAGMA foreign_keys=ON")
                connection.commit()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
