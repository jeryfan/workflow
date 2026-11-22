import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import pytest
import redis
from auth_helpers import authenticated_client


def create_human_input_workflow(client) -> str:
    app = client.post(
        "/console/api/apps",
        json={"name": "Human Approval", "mode": "workflow", "description": ""},
    ).json()
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
                            "variable": "name",
                            "label": "Name",
                            "type": "text-input",
                            "required": True,
                            "options": [],
                        }
                    ],
                },
            },
            {
                "id": "approval",
                "type": "custom",
                "position": {"x": 380, "y": 160},
                "data": {
                    "title": "Approval",
                    "type": "human-input",
                    "delivery_methods": [{"id": "webapp", "type": "webapp", "enabled": True}],
                    "form_content": "Approve {{#start.name#}}",
                    "inputs": [
                        {
                            "type": "paragraph",
                            "output_variable_name": "comment",
                            "default": {"type": "constant", "selector": [], "value": ""},
                        }
                    ],
                    "user_actions": [
                        {"id": "approve", "title": "Approve", "button_style": "primary"},
                        {"id": "reject", "title": "Reject", "button_style": "default"},
                    ],
                    "timeout": 1,
                    "timeout_unit": "day",
                },
            },
            {
                "id": "end",
                "type": "custom",
                "position": {"x": 680, "y": 160},
                "data": {
                    "title": "End",
                    "type": "end",
                    "outputs": [
                        {"variable": "comment", "value_selector": ["approval", "comment"]},
                        {"variable": "action", "value_selector": ["approval", "__action_value"]},
                    ],
                },
            },
        ],
        "edges": [
            {
                "id": "start-approval",
                "source": "start",
                "target": "approval",
                "sourceHandle": "source",
                "targetHandle": "target",
                "data": {"sourceType": "start", "targetType": "human-input"},
            },
            {
                "id": "approval-end",
                "source": "approval",
                "target": "end",
                "sourceHandle": "approve",
                "targetHandle": "target",
                "data": {"sourceType": "human-input", "targetType": "end"},
            },
            {
                "id": "approval-timeout-end",
                "source": "approval",
                "target": "end",
                "sourceHandle": "__timeout",
                "targetHandle": "target",
                "data": {"sourceType": "human-input", "targetType": "end"},
            },
        ],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }
    saved = client.post(
        f"/console/api/apps/{app['id']}/workflows/draft",
        json={"graph": graph, "features": {}, "conversation_variables": [], "hash": None},
    )
    assert saved.status_code == 200, saved.text
    return app["id"]


def events(response) -> list[dict]:
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


def test_human_input_pauses_submits_and_resumes_the_same_run(tmp_path: Path) -> None:
    with authenticated_client(tmp_path) as client:
        app_id = create_human_input_workflow(client)
        response = client.post(
            f"/console/api/apps/{app_id}/workflows/draft/run",
            json={"inputs": {"name": "World"}, "files": []},
        )
        assert response.status_code == 200, response.text
        initial = events(response)
        required = next(item for item in initial if item["event"] == "human_input_required")
        paused = next(item for item in initial if item["event"] == "workflow_paused")
        assert paused["data"]["status"] == "paused"
        form = required["data"]
        assert form["form_content"] == "Approve World"
        assert form["actions"][0]["id"] == "approve"
        run_id = required["workflow_run_id"]

        detail = client.get(f"/console/api/apps/{app_id}/workflow-runs/{run_id}")
        assert detail.status_code == 200
        assert detail.json()["status"] == "paused"
        loaded = client.get(f"/console/api/form/human_input/{form['form_token']}")
        assert loaded.status_code == 200, loaded.text
        assert loaded.json()["form_id"] == form["form_id"]

        submitted = client.post(
            f"/console/api/form/human_input/{form['form_token']}",
            json={"inputs": {"comment": "Looks good"}, "action": "approve"},
        )
        assert submitted.status_code == 200, submitted.text
        duplicate = client.post(
            f"/console/api/form/human_input/{form['form_token']}",
            json={"inputs": {"comment": "Looks good"}, "action": "approve"},
        )
        assert duplicate.status_code == 409

        resumed_response = client.get(
            f"/console/api/workflow/{run_id}/events?include_state_snapshot=true&continue_on_pause=true"
        )
        assert resumed_response.status_code == 200, resumed_response.text
        resumed = events(resumed_response)
        assert any(item["event"] == "human_input_form_filled" for item in resumed)
        assert resumed[-1]["event"] == "workflow_finished"
        assert resumed[-1]["data"]["status"] == "succeeded", resumed[-1]
        assert resumed[-1]["data"]["outputs"] == {"comment": "Looks good", "action": "Approve"}

        detail = client.get(f"/console/api/apps/{app_id}/workflow-runs/{run_id}")
        assert detail.json()["status"] == "succeeded"
        assert detail.json()["outputs"]["comment"] == "Looks good"


def test_paused_human_input_workflow_can_be_deleted(tmp_path: Path) -> None:
    with authenticated_client(tmp_path) as client:
        app_id = create_human_input_workflow(client)
        response = client.post(
            f"/console/api/apps/{app_id}/workflows/draft/run",
            json={"inputs": {"name": "World"}, "files": []},
        )
        assert response.status_code == 200, response.text
        assert any(item["event"] == "human_input_required" for item in events(response))
        deleted = client.delete(f"/console/api/apps/{app_id}")
        assert deleted.status_code == 204, deleted.text
        assert client.get(f"/console/api/apps/{app_id}").status_code == 404


def test_human_input_timeout_is_dispatched_by_the_scheduler(tmp_path: Path) -> None:
    with authenticated_client(tmp_path) as client:
        app_id = create_human_input_workflow(client)
        response = client.post(
            f"/console/api/apps/{app_id}/workflows/draft/run",
            json={"inputs": {"name": "World"}, "files": []},
        )
        initial = events(response)
        required = next(item for item in initial if item["event"] == "human_input_required")
        run_id = required["workflow_run_id"]
        with sqlite3.connect(tmp_path / "workflow.db") as connection:
            connection.execute("UPDATE human_input_forms SET expiration_time = 0")

        async def expire() -> int:
            from api.services.run_service import RunService
            from api.services.triggers import TriggerScheduler

            scheduler = TriggerScheduler(
                client.app.state.session_factory,
                lambda db: RunService(
                    db,
                    session_factory=client.app.state.session_factory,
                    coordinator=client.app.state.run_coordinator,
                ),
            )
            return await scheduler.poll_once()

        assert client.portal.call(expire) == 1
        resumed_response = client.get(f"/console/api/workflow/{run_id}/events")
        resumed = events(resumed_response)
        assert resumed[-1]["data"]["status"] == "succeeded"
        assert resumed[-1]["data"]["outputs"]["action"] == ""
        with sqlite3.connect(tmp_path / "workflow.db") as connection:
            assert connection.execute(
                "SELECT status FROM human_input_forms WHERE workflow_run_id = ?", (run_id,)
            ).fetchone() == ("timeout",)


def test_public_human_input_submission_resumes_the_published_workflow(tmp_path: Path) -> None:
    with authenticated_client(tmp_path) as client:
        app_id = create_human_input_workflow(client)
        published = client.post(f"/console/api/apps/{app_id}/workflows/publish", json={})
        assert published.status_code == 200, published.text
        site_token = client.get(f"/console/api/apps/{app_id}").json()["site"]["access_token"]
        response = client.post(
            "/api/workflows/run",
            headers={"X-App-Code": site_token},
            json={"inputs": {"name": "Public"}, "files": [], "response_mode": "streaming"},
        )
        initial = events(response)
        required = next(item for item in initial if item["event"] == "human_input_required")
        form = required["data"]
        run_id = required["workflow_run_id"]
        submitted = client.post(
            f"/api/form/human_input/{form['form_token']}",
            json={"inputs": {"comment": "Public approval"}, "action": "approve"},
        )
        assert submitted.status_code == 200, submitted.text
        for _ in range(100):
            detail = client.get(f"/console/api/apps/{app_id}/workflow-runs/{run_id}").json()
            if detail["status"] == "succeeded":
                break
            time.sleep(0.02)
        assert detail["status"] == "succeeded", detail
        assert detail["outputs"] == {"comment": "Public approval", "action": "Approve"}


def test_approved_file_flows_into_document_extraction(tmp_path: Path) -> None:
    from test_files import input_file

    with authenticated_client(tmp_path) as client:
        app_id = create_human_input_workflow(client)
        draft_path = f"/console/api/apps/{app_id}/workflows/draft"
        current = client.get(draft_path).json()
        graph = current["graph"]
        approval = next(node for node in graph["nodes"] if node["id"] == "approval")
        approval["data"]["inputs"] = [
            {
                "type": "file",
                "output_variable_name": "document",
                "allowed_file_types": ["document"],
                "allowed_file_extensions": [],
                "allowed_file_upload_methods": ["local_file"],
            }
        ]
        graph["nodes"].append(
            {
                "id": "extract",
                "type": "custom",
                "position": {"x": 600, "y": 300},
                "data": {
                    "title": "Extract",
                    "type": "document-extractor",
                    "variable_selector": ["approval", "document"],
                    "is_array_file": False,
                },
            }
        )
        end = next(node for node in graph["nodes"] if node["id"] == "end")
        end["data"]["outputs"] = [{"variable": "text", "value_selector": ["extract", "text"]}]
        approval_edge = next(edge for edge in graph["edges"] if edge["id"] == "approval-end")
        approval_edge["target"] = "extract"
        graph["edges"].append(
            {
                "id": "extract-end",
                "source": "extract",
                "target": "end",
                "sourceHandle": "source",
                "targetHandle": "target",
                "data": {"sourceType": "document-extractor", "targetType": "end"},
            }
        )
        saved = client.post(
            draft_path,
            json={
                "graph": graph,
                "features": {},
                "conversation_variables": [],
                "hash": current["hash"],
            },
        )
        assert saved.status_code == 200, saved.text
        response = client.post(
            f"{draft_path}/run",
            json={"inputs": {"name": "World"}, "files": []},
        )
        required = next(item for item in events(response) if item["event"] == "human_input_required")
        form = required["data"]
        run_id = required["workflow_run_id"]
        token_response = client.post(f"/api/form/human_input/{form['form_token']}/upload-token")
        assert token_response.status_code == 200, token_response.text
        upload_token = token_response.json()["upload_token"]
        rejected = client.post(
            "/api/human-input-forms/files",
            headers={"Authorization": f"bearer {upload_token}"},
            files={"file": ("image.png", b"not-a-document", "image/png")},
        )
        assert rejected.status_code == 400, rejected.text
        uploaded_response = client.post(
            "/api/human-input-forms/files",
            headers={"Authorization": f"bearer {upload_token}"},
            files={"file": ("approval.txt", b"Approved document", "text/plain")},
        )
        assert uploaded_response.status_code == 201, uploaded_response.text
        uploaded = uploaded_response.json()
        submitted = client.post(
            f"/console/api/form/human_input/{form['form_token']}",
            json={"inputs": {"document": input_file(uploaded)}, "action": "approve"},
        )
        assert submitted.status_code == 200, submitted.text
        resumed = events(client.get(f"/console/api/workflow/{run_id}/events"))
        assert resumed[-1]["data"]["status"] == "succeeded", resumed[-1]
        assert resumed[-1]["data"]["outputs"] == {"text": "Approved document"}


REDIS_URL = os.environ.get("TEST_REDIS_URL")


@pytest.mark.skipif(REDIS_URL is None, reason="Redis integration URL is not configured")
def test_human_input_pause_and_resume_use_real_celery_and_redis(tmp_path: Path, monkeypatch) -> None:
    from api import tasks
    from api.configs import app_config

    parsed = urlparse(REDIS_URL)
    monkeypatch.setattr(app_config, "EXECUTION_BACKEND", "celery")
    monkeypatch.setattr(app_config, "REDIS_HOST", parsed.hostname or "127.0.0.1")
    monkeypatch.setattr(app_config, "REDIS_PORT", parsed.port or 6379)
    monkeypatch.setattr(app_config, "REDIS_PASSWORD", parsed.password or "")
    monkeypatch.setattr(app_config, "REDIS_USERNAME", parsed.username or "")
    monkeypatch.setattr(app_config, "REDIS_DB", 0)
    client = redis.Redis.from_url(REDIS_URL)
    client.flushdb()
    monkeypatch.setattr(
        "api.extensions.ext_redis.create_sync_client",
        lambda: redis.Redis.from_url(REDIS_URL),
    )

    def start_worker(run_id: str):
        worker = threading.Thread(target=tasks.execute_workflow.run, args=[run_id])
        worker.start()
        return worker

    try:
        with authenticated_client(tmp_path) as console:
            monkeypatch.setattr(tasks, "AsyncSessionLocal", console.app.state.session_factory)
            monkeypatch.setattr(tasks, "create_sync_client", lambda: redis.Redis.from_url(REDIS_URL))
            app_id = create_human_input_workflow(console)
            with console.stream(
                "POST",
                f"/console/api/apps/{app_id}/workflows/draft/run",
                json={"inputs": {"name": "Celery"}, "files": []},
            ) as response:
                with sqlite3.connect(tmp_path / "workflow.db") as connection:
                    run_id = connection.execute(
                        "SELECT id FROM workflow_runs ORDER BY created_at DESC, id DESC LIMIT 1"
                    ).fetchone()[0]
                worker = start_worker(run_id)
                initial = events_from_bytes(response.read())
                worker.join(10)
                assert not worker.is_alive()
            required = next(item for item in initial if item["event"] == "human_input_required")
            form = required["data"]
            submitted = console.post(
                f"/console/api/form/human_input/{form['form_token']}",
                json={"inputs": {"comment": "Distributed approval"}, "action": "approve"},
            )
            assert submitted.status_code == 200, submitted.text
            with console.stream("GET", f"/console/api/workflow/{run_id}/events") as response:
                worker = start_worker(run_id)
                resumed = events_from_bytes(response.read())
                worker.join(10)
                assert not worker.is_alive()
            assert resumed[-1]["event"] == "workflow_finished"
            assert resumed[-1]["data"]["status"] == "succeeded", resumed[-1]
            assert resumed[-1]["data"]["outputs"] == {
                "comment": "Distributed approval",
                "action": "Approve",
            }
    finally:
        client.flushdb()
        client.close()


def events_from_bytes(content: bytes) -> list[dict]:
    return [json.loads(line[6:]) for line in content.decode().splitlines() if line.startswith("data: ")]
