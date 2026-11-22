import os
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_auth import ADMIN, encoded_password
from test_files import create_file_workflow, upload

from api.config import get_settings
from api.db.sqlite_import import import_sqlite
from api.main import create_app

DATABASE_URL = os.environ.get("TEST_POSTGRESQL_IMPORT_URL")
ENDPOINT = os.environ.get("TEST_S3_ENDPOINT_URL")
pytestmark = pytest.mark.skipif(
    DATABASE_URL is None or ENDPOINT is None,
    reason="PostgreSQL import and S3 integration URLs are not configured",
)


def test_legacy_sqlite_account_and_file_import(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "legacy.db"
    with TestClient(create_app(database_url=f"sqlite:///{source}")) as client:
        assert client.post("/console/api/setup", json=ADMIN).status_code == 201
        response = client.post(
            "/console/api/login",
            json={"email": ADMIN["email"], "password": encoded_password(ADMIN["password"])},
        )
        assert response.status_code == 200
        client.headers["X-CSRF-Token"] = client.cookies["csrf_token"]
        app_id = create_file_workflow(client)
        assert client.post(f"/console/api/apps/{app_id}/workflows/publish", json={}).status_code == 200
        uploaded = upload(client, "legacy.txt", b"Legacy object")

    bucket = f"workflow-import-{uuid4()}"
    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    monkeypatch.setenv("S3_ENDPOINT_URL", ENDPOINT)
    monkeypatch.setenv("S3_BUCKET", bucket)
    monkeypatch.setenv("S3_ACCESS_KEY", "workflow-test")
    monkeypatch.setenv("S3_SECRET_KEY", "workflow-test-secret")
    monkeypatch.setenv("FILE_SIGNING_KEY", "test-signing-key-that-is-at-least-32-bytes")
    get_settings.cache_clear()
    result = import_sqlite(source, DATABASE_URL)
    assert result["tables"]["accounts"] == 1
    assert result["files"] == 1

    with TestClient(create_app(database_url=DATABASE_URL)) as client:
        response = client.post(
            "/console/api/login",
            json={"email": ADMIN["email"], "password": encoded_password(ADMIN["password"])},
        )
        assert response.status_code == 200
        client.headers["X-CSRF-Token"] = client.cookies["csrf_token"]
        preview = client.get(f"/console/api/files/{uploaded['id']}/preview")
        assert preview.status_code == 200
        assert preview.json()["content"] == "Legacy object"
        published = client.get(f"/console/api/apps/{app_id}/workflows/publish")
        assert published.status_code == 200
