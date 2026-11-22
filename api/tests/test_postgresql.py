import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect
from test_auth import ADMIN, encoded_password
from test_files import create_file_workflow, input_file, run_with_files, upload

from api.app import DEFAULT_APP_ID, create_app

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads((ROOT / "contracts/fixtures/reference-workflow.json").read_text())
DATABASE_URL = os.environ.get("TEST_POSTGRESQL_URL")
pytestmark = pytest.mark.skipif(DATABASE_URL is None, reason="PostgreSQL integration URL is not configured")


def test_postgresql_full_workflow_and_file_lifecycle() -> None:
    from api.extensions.ext_storage import storage
    from api.extensions.storage.opendal_storage import AsyncOpenDALStorage

    with TestClient(create_app(database_url=DATABASE_URL)) as client:
        assert isinstance(storage._require_runner(), AsyncOpenDALStorage)
        assert client.post("/console/api/setup", json=ADMIN).status_code == 201
        login = client.post(
            "/console/api/login",
            json={"email": ADMIN["email"], "password": encoded_password(ADMIN["password"])},
        )
        assert login.status_code == 200, login.text
        client.headers["X-CSRF-Token"] = client.cookies["csrf_token"]

        async def table_names() -> set[str]:
            async with client.app.state.engine.connect() as connection:
                return set(await connection.run_sync(lambda sync: inspect(sync).get_table_names()))

        tables = client.portal.call(table_names)
        assert not {"workspaces", "workspace_memberships", "workflow_acl", "workflow_access_settings"}.intersection(
            tables
        )
        saved = client.post(
            f"/console/api/apps/{DEFAULT_APP_ID}/workflows/draft",
            json={"graph": FIXTURE["graph"], "features": {}, "conversation_variables": [], "hash": None},
        )
        assert saved.status_code == 200, saved.text
        run = client.post(
            f"/console/api/apps/{DEFAULT_APP_ID}/workflows/draft/run",
            json={"inputs": FIXTURE["inputs"], "files": []},
        )
        assert run.status_code == 200, run.text
        events = [json.loads(line[6:]) for line in run.text.splitlines() if line.startswith("data: ")]
        assert events[-1]["data"]["status"] == "succeeded"
        assert events[-1]["data"]["outputs"] == FIXTURE["expected_outputs"]
        assert client.post(f"/console/api/apps/{DEFAULT_APP_ID}/workflows/publish", json={}).status_code == 200

        file_app_id = create_file_workflow(client)
        uploaded = upload(client, "postgres.txt", b"PostgreSQL file")
        outputs = run_with_files(client, file_app_id, input_file(uploaded))
        assert outputs["documents"]["filename"] == "postgres.txt"
        run_id = client.get(f"/console/api/apps/{file_app_id}/workflow-runs").json()["data"][0]["id"]
        detail = client.get(f"/console/api/apps/{file_app_id}/workflow-runs/{run_id}")
        assert detail.status_code == 200
        assert client.get(detail.json()["outputs"]["documents"]["url"]).content == b"PostgreSQL file"
        assert client.delete(f"/console/api/apps/{file_app_id}").status_code == 204
        assert client.get(f"/console/api/apps/{file_app_id}").status_code == 404


def test_postgresql_deleting_a_paused_human_input_workflow_cascades() -> None:
    from test_human_input import create_human_input_workflow, events

    with TestClient(create_app(database_url=DATABASE_URL)) as client:
        if client.get("/console/api/setup").json()["step"] == "not_started":
            assert client.post("/console/api/setup", json=ADMIN).status_code == 201
        login_response = client.post(
            "/console/api/login",
            json={"email": ADMIN["email"], "password": encoded_password(ADMIN["password"])},
        )
        assert login_response.status_code == 200, login_response.text
        client.headers["X-CSRF-Token"] = client.cookies["csrf_token"]
        app_id = create_human_input_workflow(client)
        response = client.post(
            f"/console/api/apps/{app_id}/workflows/draft/run",
            json={"inputs": {"name": "PostgreSQL"}, "files": []},
        )
        assert response.status_code == 200, response.text
        assert any(item["event"] == "human_input_required" for item in events(response))
        deleted = client.delete(f"/console/api/apps/{app_id}")
        assert deleted.status_code == 204, deleted.text
        assert client.get(f"/console/api/apps/{app_id}").status_code == 404
