import json
import sqlite3
from copy import deepcopy
from pathlib import Path

from auth_helpers import authenticated_database
from fastapi.testclient import TestClient

from api.configs import app_config

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads((ROOT / "contracts/fixtures/reference-workflow.json").read_text())
APP_ID = FIXTURE["app_id"]
DRAFT_URL = f"/console/api/apps/{APP_ID}/workflows/draft"
RUN_URL = f"/console/api/apps/{APP_ID}/workflows/draft/run"
EXPECTED_HASH = json.loads((ROOT / "contracts/fixtures/reference-hash.json").read_text())["expected_hash"]


def make_client(database_path: Path) -> TestClient:
    return authenticated_database(database_path)


def save_draft(client: TestClient, graph: dict | None = None, hash_value: str | None = None):
    return client.post(
        DRAFT_URL,
        json={
            "graph": graph or deepcopy(FIXTURE["graph"]),
            "features": {},
            "conversation_variables": [],
            "hash": hash_value,
        },
    )


def read_sse_events(response) -> list[dict]:
    return [json.loads(line[6:]) for line in response.iter_lines() if line.startswith("data: ")]


def test_missing_draft_uses_the_dify_error_contract(tmp_path: Path) -> None:
    with make_client(tmp_path / "workflow.db") as client:
        response = client.get(DRAFT_URL)

    assert response.status_code == 404
    assert response.json()["code"] == "draft_workflow_not_exist"


def test_reference_graph_saves_reloads_runs_and_persists(tmp_path: Path) -> None:
    database_path = tmp_path / "workflow.db"
    with make_client(database_path) as client:
        saved = save_draft(client)

        assert saved.status_code == 200
        assert saved.json()["result"] == "success"
        assert saved.json()["hash"] == EXPECTED_HASH
        assert isinstance(saved.json()["updated_at"], int)

    with make_client(database_path) as client:
        reloaded = client.get(DRAFT_URL)
        assert reloaded.status_code == 200
        assert reloaded.json()["graph"] == FIXTURE["graph"]
        assert reloaded.json()["hash"] == EXPECTED_HASH
        assert reloaded.json()["tool_published"] is False
        assert reloaded.json()["rag_pipeline_variables"] == []

        with client.stream("POST", RUN_URL, json={"inputs": FIXTURE["inputs"], "files": []}) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            events = read_sse_events(response)

    assert [event["event"] for event in events] == FIXTURE["expected_events"]
    assert events[-1]["data"]["status"] == "succeeded"
    assert events[-1]["data"]["outputs"] == FIXTURE["expected_outputs"]
    assert len({event["task_id"] for event in events}) == 1
    assert len({event["workflow_run_id"] for event in events}) == 1

    with sqlite3.connect(database_path) as connection:
        run = connection.execute("SELECT status, outputs_json FROM workflow_runs").fetchone()
        node_count = connection.execute("SELECT COUNT(*) FROM node_executions").fetchone()[0]
    assert run is not None
    assert run[0] == "succeeded"
    assert json.loads(run[1]) == FIXTURE["expected_outputs"]
    assert node_count == 3


def test_stale_hash_does_not_replace_the_saved_graph(tmp_path: Path) -> None:
    with make_client(tmp_path / "workflow.db") as client:
        first_save = save_draft(client)
        changed_graph = deepcopy(FIXTURE["graph"])
        changed_graph["nodes"][1]["data"]["template"] = "Changed {{ name }}"

        conflict = save_draft(client, graph=changed_graph, hash_value="stale")
        reloaded = client.get(DRAFT_URL)

    assert first_save.status_code == 200
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "draft_workflow_not_sync"
    assert reloaded.json()["graph"] == FIXTURE["graph"]


def test_unknown_node_is_rejected_before_a_run_exists(tmp_path: Path) -> None:
    database_path = tmp_path / "workflow.db"
    graph = deepcopy(FIXTURE["graph"])
    graph["nodes"][1]["data"]["type"] = "unknown-node"

    with make_client(database_path) as client:
        response = save_draft(client, graph=graph)

    assert response.status_code == 400
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM workflow_runs").fetchone()[0] == 0


def test_template_failure_has_one_failed_terminal_event(tmp_path: Path) -> None:
    graph = deepcopy(FIXTURE["graph"])
    graph["nodes"][1]["data"]["template"] = "{{ missing }}"

    with make_client(tmp_path / "workflow.db") as client:
        assert save_draft(client, graph=graph).status_code == 200
        with client.stream("POST", RUN_URL, json={"inputs": FIXTURE["inputs"]}) as response:
            events = read_sse_events(response)

    terminal_events = [event for event in events if event["event"] == "workflow_finished"]
    assert len(terminal_events) == 1
    assert terminal_events[0]["data"]["status"] == "failed"
    assert any(
        event["event"] == "node_finished"
        and event["data"]["node_id"] == "template"
        and event["data"]["status"] == "failed"
        for event in events
    )


def test_local_editor_origin_can_call_the_api(tmp_path: Path) -> None:
    with make_client(tmp_path / "workflow.db") as client:
        response = client.options(
            DRAFT_URL,
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_configured_editor_origin_can_call_the_api(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(app_config, "CORS_ALLOWED_ORIGINS", ["http://127.0.0.1:3100"])
    with make_client(tmp_path / "workflow.db") as client:
        response = client.options(
            DRAFT_URL,
            headers={
                "Origin": "http://127.0.0.1:3100",
                "Access-Control-Request-Method": "POST",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:3100"


def test_application_and_node_defaults_contain_only_the_workflow_whitelist(tmp_path: Path) -> None:
    with make_client(tmp_path / "workflow.db") as client:
        app = client.get(f"/console/api/apps/{APP_ID}")
        defaults = client.get(f"/console/api/apps/{APP_ID}/workflows/default-workflow-block-configs")
        template = client.get(
            f"/console/api/apps/{APP_ID}/workflows/default-workflow-block-configs/template-transform",
            params={"q": "{}"},
        )

    assert app.status_code == 200
    assert app.json()["mode"] == "workflow"
    assert {item["type"] for item in defaults.json()} == {
        "start",
        "llm",
        "template-transform",
        "code",
        "if-else",
        "variable-aggregator",
        "list-operator",
        "http-request",
        "iteration",
        "iteration-start",
        "loop",
        "loop-start",
        "loop-end",
        "assigner",
        "end",
        "document-extractor",
        "human-input",
        "tool",
        "trigger-schedule",
        "trigger-webhook",
    }
    assert template.json()["template"] == "{{ arg1 }}"
