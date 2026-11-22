from __future__ import annotations

import argparse
import json
import sqlite3
import tempfile
from pathlib import Path
from urllib.parse import quote

from sqlalchemy import MetaData, create_engine, func, select

from api.config import get_settings
from api.db.migrations import upgrade
from api.services.file_storage import S3ObjectStorage


def _copy_and_upgrade(source: Path, destination: Path) -> None:
    uri = f"file:{quote(source.resolve().as_posix(), safe='/')}?mode=ro&immutable=1"
    with sqlite3.connect(uri, uri=True) as source_connection, sqlite3.connect(destination) as destination_connection:
        source_connection.backup(destination_connection)
    upgrade(f"sqlite:///{destination}")


def _ordered_tables(metadata: MetaData):
    remaining = dict(metadata.tables)
    ordered = []
    while remaining:
        ready = []
        for name, table in remaining.items():
            dependencies = {
                foreign_key.column.table.name
                for foreign_key in table.foreign_keys
                if foreign_key.column.table.name != name and not foreign_key.parent.nullable
            }
            if not dependencies.intersection(remaining):
                ready.append(name)
        if not ready:
            raise ValueError("Database foreign-key dependency cycle prevents import")
        for name in sorted(ready):
            ordered.append(remaining.pop(name))
    return ordered


def _migrate_objects(source, uploads_root: Path, storage: S3ObjectStorage) -> list[str]:
    rows = source.exec_driver_sql("SELECT storage_key FROM uploaded_files").scalars().all()
    root = uploads_root.resolve()
    written = []
    try:
        for key in rows:
            path = root / key
            if not path.is_file() or path.resolve().parent != root:
                raise FileNotFoundError(f"Legacy uploaded file is missing: {key}")
            storage.write(key, path.read_bytes())
            written.append(key)
    except Exception:
        for key in written:
            storage.delete(key)
        raise
    return written


def import_sqlite(source_path: Path, database_url: str, uploads_root: Path | None = None) -> dict:
    if not source_path.is_file():
        raise FileNotFoundError(f"SQLite source does not exist: {source_path}")
    target_engine = create_engine(database_url)
    if target_engine.dialect.name != "postgresql":
        target_engine.dispose()
        raise ValueError("Import target must be PostgreSQL")
    upgrade(database_url)

    settings = get_settings()
    if settings.storage_backend != "s3":
        target_engine.dispose()
        raise ValueError("SQLite import requires S3 storage configuration")
    storage = S3ObjectStorage(
        bucket=settings.s3_bucket,
        region=settings.s3_region,
        endpoint_url=settings.s3_endpoint_url,
        access_key=settings.s3_access_key,
        secret_key=settings.s3_secret_key,
    )

    with tempfile.TemporaryDirectory(prefix="workflow-sqlite-import-") as directory:
        snapshot = Path(directory) / "workflow.db"
        _copy_and_upgrade(source_path, snapshot)
        source_engine = create_engine(f"sqlite:///{snapshot}")
        try:
            source_metadata = MetaData()
            target_metadata = MetaData()
            source_metadata.reflect(bind=source_engine)
            target_metadata.reflect(bind=target_engine)
            if source_metadata.tables.keys() != target_metadata.tables.keys():
                raise ValueError("Source and target schemas differ after migration")

            with target_engine.connect() as connection:
                for table in target_metadata.tables.values():
                    if table.name == "alembic_version":
                        continue
                    count = connection.execute(select(func.count()).select_from(table)).scalar_one()
                    if count:
                        raise ValueError(f"Import target table is not empty: {table.name}")

            with source_engine.connect() as object_source:
                files = _migrate_objects(
                    object_source,
                    uploads_root or source_path.parent / "uploads",
                    storage,
                )
            counts = {}
            deferred = []
            try:
                with target_engine.begin() as target, source_engine.connect() as source:
                    for table in _ordered_tables(target_metadata):
                        if table.name == "alembic_version":
                            continue
                        source_table = source_metadata.tables[table.name]
                        rows = [dict(row._mapping) for row in source.execute(select(source_table))]
                        for row in rows:
                            for column in table.columns:
                                if not column.nullable or not column.foreign_keys or row[column.name] is None:
                                    continue
                                deferred.append(
                                    (
                                        table,
                                        column.name,
                                        {key.name: row[key.name] for key in table.primary_key.columns},
                                        row[column.name],
                                    )
                                )
                                row[column.name] = None
                        if rows:
                            target.execute(table.insert(), rows)
                        counts[table.name] = len(rows)
                    for table, column, primary_key, value in deferred:
                        predicates = [table.c[key] == item for key, item in primary_key.items()]
                        target.execute(table.update().where(*predicates).values({column: value}))
            except Exception:
                for key in files:
                    storage.delete(key)
                raise
            return {"tables": counts, "files": len(files)}
        finally:
            source_engine.dispose()
            target_engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Import a legacy SQLite deployment into empty PostgreSQL")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--uploads-root", type=Path)
    parser.add_argument("--database-url", required=True)
    arguments = parser.parse_args()
    result = import_sqlite(arguments.source, arguments.database_url, arguments.uploads_root)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
