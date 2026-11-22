import asyncio
import json
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from auth_helpers import authenticated_client

from api.configs import app_config
from api.models import UploadedFile


def create_file_workflow(client, *, file_list: bool = False) -> str:
    app = client.post(
        "/console/api/apps",
        json={"name": "File Acceptance", "mode": "workflow", "description": ""},
    ).json()
    variable_type = "file-list" if file_list else "file"
    graph = {
        "nodes": [
            {
                "id": "start",
                "type": "custom",
                "position": {"x": 80, "y": 160},
                "data": {
                    "title": "Start",
                    "type": "start",
                    "variables": [
                        {
                            "variable": "documents",
                            "label": "Documents",
                            "type": variable_type,
                            "required": True,
                            "options": [],
                        }
                    ],
                },
            },
            {
                "id": "end",
                "type": "custom",
                "position": {"x": 400, "y": 160},
                "data": {
                    "title": "End",
                    "type": "end",
                    "outputs": [{"variable": "documents", "value_selector": ["start", "documents"]}],
                },
            },
        ],
        "edges": [
            {
                "id": "start-end",
                "source": "start",
                "target": "end",
                "sourceHandle": "source",
                "targetHandle": "target",
                "data": {"sourceType": "start", "targetType": "end"},
            }
        ],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }
    saved = client.post(
        f"/console/api/apps/{app['id']}/workflows/draft",
        json={"graph": graph, "features": {}, "conversation_variables": [], "hash": None},
    )
    assert saved.status_code == 200, saved.text
    return app["id"]


def upload(client, name: str, content: bytes, *, public_token: str | None = None) -> dict:
    path = "/api/files/upload" if public_token else "/console/api/files/upload"
    headers = {"X-App-Code": public_token} if public_token else {}
    response = client.post(
        path,
        files={"file": (name, content, "text/plain")},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def input_file(file: dict) -> dict:
    return {
        "type": "document",
        "transfer_method": "local_file",
        "upload_file_id": file["id"],
        "url": "",
    }


def run_with_files(client, app_id: str, files: list[dict] | dict, *, public_token: str | None = None) -> dict:
    path = f"/console/api/apps/{app_id}/workflows/draft/run"
    headers = {}
    if public_token:
        path = "/api/workflows/run"
        headers = {"X-App-Code": public_token}
    payload = {"inputs": {"documents": files}, "files": []}
    if public_token:
        payload["response_mode"] = "streaming"
    response = client.post(
        path,
        headers=headers,
        json=payload,
    )
    assert response.status_code == 200, response.text
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    assert events[-1]["event"] == "workflow_finished"
    assert events[-1]["data"]["status"] == "succeeded"
    return events[-1]["data"]["outputs"]


def test_uploaded_file_runs_as_file_variable_and_has_signed_content_url(tmp_path: Path) -> None:
    with authenticated_client(tmp_path) as client:
        app_id = create_file_workflow(client)
        uploaded = upload(client, "hello.txt", b"Hello file")
        preview = client.get(f"/console/api/files/{uploaded['id']}/preview")
        outputs = run_with_files(client, app_id, input_file(uploaded))

        output = outputs["documents"]
        assert output["dify_model_identity"] == "__dify__file__", outputs
        assert output["filename"] == "hello.txt"
        assert output["size"] == 10
        assert output["url"]
        downloaded = client.get(output["url"])
        assert downloaded.status_code == 200
        assert downloaded.content == b"Hello file"

    assert preview.status_code == 200
    assert preview.json() == {"content": "Hello file"}
    assert uploaded["name"] == "hello.txt"
    assert uploaded["size"] == 10


def test_file_list_and_public_upload_run_with_real_files(tmp_path: Path) -> None:
    with authenticated_client(tmp_path) as client:
        app_id = create_file_workflow(client, file_list=True)
        published = client.post(f"/console/api/apps/{app_id}/workflows/publish", json={})
        assert published.status_code == 200, published.text
        token = client.get(f"/console/api/apps/{app_id}").json()["site"]["access_token"]
        first = upload(client, "one.txt", b"one", public_token=token)
        second = upload(client, "two.txt", b"two", public_token=token)
        public_preview = client.get(first["url"].replace("/content?", "/preview?"))
        assert public_preview.status_code == 200, public_preview.text
        assert public_preview.json() == {"content": "one"}
        outputs = run_with_files(
            client,
            app_id,
            [input_file(first), input_file(second)],
            public_token=token,
        )
        assert [item["filename"] for item in outputs["documents"]] == ["one.txt", "two.txt"], outputs
        for item in outputs["documents"]:
            downloaded = client.get(item["url"])
            assert downloaded.status_code == 200
            assert downloaded.content in {b"one", b"two"}


def test_file_rejects_executable_and_oversized_content(tmp_path: Path) -> None:
    with authenticated_client(tmp_path) as client:
        blocked = client.post(
            "/console/api/files/upload",
            files={"file": ("tool.exe", b"binary", "application/octet-stream")},
        )
        too_large = client.post(
            "/console/api/files/upload",
            files={"file": ("large.txt", b"x" * (20 * 1024 * 1024 + 1), "text/plain")},
        )
    assert blocked.status_code == 400
    assert too_large.status_code == 400


def test_remote_file_upload_uses_downloaded_content(tmp_path: Path, monkeypatch) -> None:
    remote_root = tmp_path / "remote"
    remote_root.mkdir()
    (remote_root / "remote.txt").write_bytes(b"Downloaded file")
    handler = partial(SimpleHTTPRequestHandler, directory=str(remote_root))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("HTTP_REQUEST_PROXY", "")
    try:
        with authenticated_client(tmp_path) as client:
            response = client.post(
                "/console/api/remote-files/upload",
                json={"url": f"http://127.0.0.1:{server.server_port}/remote.txt"},
            )
            assert response.status_code == 200, response.text
            uploaded = response.json()
            downloaded = client.get(uploaded["url"])
            preview = client.get(f"/console/api/files/{uploaded['id']}/preview")
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

    assert uploaded["name"] == "remote.txt"
    assert uploaded["source_url"].endswith("/remote.txt")
    assert downloaded.status_code == 200
    assert downloaded.content == b"Downloaded file"
    assert preview.json() == {"content": "Downloaded file"}


def test_run_history_and_last_node_run_refresh_file_urls(tmp_path: Path) -> None:
    with authenticated_client(tmp_path) as client:
        app_id = create_file_workflow(client)
        uploaded = upload(client, "history.txt", b"History file")
        run_with_files(client, app_id, input_file(uploaded))
        run_id = client.get(f"/console/api/apps/{app_id}/workflow-runs").json()["data"][0]["id"]

        detail = client.get(f"/console/api/apps/{app_id}/workflow-runs/{run_id}")
        nodes = client.get(f"/console/api/apps/{app_id}/workflow-runs/{run_id}/node-executions")
        last_start = client.get(f"/console/api/apps/{app_id}/workflows/draft/nodes/start/last-run")

        assert detail.status_code == 200, detail.text
        output_file = detail.json()["outputs"]["documents"]
        assert output_file["url"]
        assert client.get(output_file["url"]).content == b"History file"
        assert detail.json()["inputs"]["documents"]["url"]

        node_items = nodes.json()["data"]
        assert nodes.status_code == 200
        assert node_items[-1]["outputs"]["documents"]["url"]
        assert client.get(node_items[-1]["outputs"]["documents"]["url"]).content == b"History file"
        assert last_start.status_code == 200, last_start.text
        assert client.get(last_start.json()["outputs"]["documents"]["url"]).content == b"History file"


def test_deleting_app_removes_its_public_file_records_and_content(tmp_path: Path) -> None:
    with authenticated_client(tmp_path) as client:
        app_id = create_file_workflow(client)
        published = client.post(f"/console/api/apps/{app_id}/workflows/publish", json={})
        assert published.status_code == 200, published.text
        token = client.get(f"/console/api/apps/{app_id}").json()["site"]["access_token"]
        uploaded = upload(client, "delete.txt", b"Delete me", public_token=token)

        async def stored_file():
            async with client.app.state.session_factory() as db:
                return await db.get(UploadedFile, uploaded["id"])

        stored = asyncio.run(stored_file())
        assert stored is not None
        content_path = Path(app_config.STORAGE_LOCAL_PATH) / stored.storage_key
        assert content_path.read_bytes() == b"Delete me"

        deleted = client.delete(f"/console/api/apps/{app_id}")
        old_download = client.get(uploaded["url"])

        assert deleted.status_code == 204, deleted.text
        assert client.get(f"/console/api/apps/{app_id}").status_code == 404
        assert asyncio.run(stored_file()) is None
        assert not content_path.exists()
        assert old_download.status_code == 403
