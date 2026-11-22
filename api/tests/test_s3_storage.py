import asyncio
import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_auth import ADMIN, encoded_password
from test_files import create_file_workflow, input_file, run_with_files, upload

from api.app import create_app
from api.configs import app_config
from api.extensions.ext_storage import storage
from api.extensions.storage.opendal_storage import AsyncOpenDALStorage
from api.services.files import FileService

ENDPOINT = os.environ.get("TEST_S3_ENDPOINT_URL")
pytestmark = pytest.mark.skipif(ENDPOINT is None, reason="S3 integration endpoint is not configured")


def configure_s3(monkeypatch, bucket: str) -> None:
    monkeypatch.setattr(app_config, "STORAGE_TYPE", "opendal")
    monkeypatch.setattr(app_config, "OPENDAL_SCHEME", "s3")
    monkeypatch.setattr(app_config, "OPENDAL_S3_ENDPOINT", ENDPOINT)
    monkeypatch.setattr(app_config, "OPENDAL_S3_BUCKET", bucket)
    monkeypatch.setattr(app_config, "OPENDAL_S3_ACCESS_KEY_ID", "workflow-test")
    monkeypatch.setattr(app_config, "OPENDAL_S3_SECRET_ACCESS_KEY", "workflow-test-secret")
    monkeypatch.setattr(app_config, "FILE_SIGNING_KEY", "test-signing-key-that-is-at-least-32-bytes")


def test_s3_files_support_run_history_download_and_delete(tmp_path: Path, monkeypatch) -> None:
    bucket = f"workflow-{uuid4()}"
    configure_s3(monkeypatch, bucket)
    database_url = f"sqlite:///{tmp_path / 'workflow.db'}"
    with TestClient(create_app(database_url=database_url)) as client:
        assert client.post("/console/api/setup", json=ADMIN).status_code == 201
        login = client.post(
            "/console/api/login",
            json={"email": ADMIN["email"], "password": encoded_password(ADMIN["password"])},
        )
        assert login.status_code == 200
        client.headers["X-CSRF-Token"] = client.cookies["csrf_token"]
        app_id = create_file_workflow(client)
        uploaded = upload(client, "object.txt", b"S3 object")
        outputs = run_with_files(client, app_id, input_file(uploaded))
        assert outputs["documents"]["filename"] == "object.txt"
        run_id = client.get(f"/console/api/apps/{app_id}/workflow-runs").json()["data"][0]["id"]
        detail = client.get(f"/console/api/apps/{app_id}/workflow-runs/{run_id}").json()
        assert client.get(detail["outputs"]["documents"]["url"]).content == b"S3 object"
        runner = storage._require_runner()
        assert isinstance(runner, AsyncOpenDALStorage)
        storage_key = f"{uploaded['id']}.txt"
        assert asyncio.run(storage.load_once(storage_key)) == b"S3 object"
        assert client.delete(f"/console/api/apps/{app_id}").status_code == 204

        # Console uploads remain account-owned rather than app-owned and are retained.
        assert asyncio.run(storage.load_once(storage_key)) == b"S3 object"


def test_s3_signing_key_is_shared_between_application_instances(tmp_path: Path, monkeypatch) -> None:
    bucket = f"workflow-{uuid4()}"
    configure_s3(monkeypatch, bucket)
    database_url = f"sqlite:///{tmp_path / 'workflow.db'}"
    with TestClient(create_app(database_url=database_url)) as client:
        assert client.post("/console/api/setup", json=ADMIN).status_code == 201
        assert (
            client.post(
                "/console/api/login",
                json={"email": ADMIN["email"], "password": encoded_password(ADMIN["password"])},
            ).status_code
            == 200
        )
        client.headers["X-CSRF-Token"] = client.cookies["csrf_token"]
        uploaded = upload(client, "shared.txt", b"Shared signature")
        parsed = urlparse(uploaded["url"])
        parameters = parse_qs(parsed.query)

    async def read_shared_file(app) -> None:
        async with app.state.session_factory() as db:
            file, content = await FileService(db).content(
                uploaded["id"],
                expires=int(parameters["expires"][0]),
                signature=parameters["signature"][0],
            )
            assert file.name == "shared.txt"
            assert content == b"Shared signature"

    with TestClient(create_app(database_url=database_url)) as second:
        asyncio.run(read_shared_file(second.app))
