import json
from pathlib import Path

from auth_helpers import ADMIN, authenticated_client
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads((ROOT / "contracts/fixtures/reference-workflow.json").read_text())
APP_ID = FIXTURE["app_id"]


def run_reference(client: TestClient) -> str:
    draft_url = f"/console/api/apps/{APP_ID}/workflows/draft"
    current = client.get(draft_url)
    current_hash = current.json().get("hash") if current.status_code == 200 else None
    saved = client.post(
        draft_url,
        json={
            "graph": FIXTURE["graph"],
            "features": {},
            "conversation_variables": [],
            "hash": current_hash,
        },
    )
    assert saved.status_code == 200
    with client.stream(
        "POST",
        f"/console/api/apps/{APP_ID}/workflows/draft/run",
        json={"inputs": FIXTURE["inputs"], "files": []},
    ) as response:
        events = [json.loads(line[6:]) for line in response.iter_lines() if line.startswith("data: ")]
    assert events[-1]["data"]["status"] == "succeeded"
    return events[0]["data"]["id"]


def test_run_history_detail_and_node_executions_read_the_persisted_run(tmp_path: Path) -> None:
    with authenticated_client(tmp_path) as client:
        run_id = run_reference(client)
        history = client.get(f"/console/api/apps/{APP_ID}/workflow-runs?limit=20")
        detail = client.get(f"/console/api/apps/{APP_ID}/workflow-runs/{run_id}")
        nodes = client.get(f"/console/api/apps/{APP_ID}/workflow-runs/{run_id}/node-executions")

    assert history.status_code == 200
    assert history.json()["limit"] == 20
    assert history.json()["has_more"] is False
    assert [item["id"] for item in history.json()["data"]] == [run_id]
    item = history.json()["data"][0]
    assert item["status"] == "succeeded"
    assert item["created_by_account"]["email"] == ADMIN["email"]
    assert item["total_steps"] == 3

    assert detail.status_code == 200
    assert detail.json()["id"] == run_id
    assert detail.json()["graph"] == FIXTURE["graph"]
    assert detail.json()["inputs"] == FIXTURE["inputs"]
    assert detail.json()["outputs"] == FIXTURE["expected_outputs"]
    assert detail.json()["created_by_role"] == "account"

    assert nodes.status_code == 200
    assert len(nodes.json()["data"]) == 3
    assert [item["node_id"] for item in nodes.json()["data"]] == ["start", "template", "end"]
    assert all(item["status"] == "succeeded" for item in nodes.json()["data"])
    assert nodes.json()["data"][1]["outputs"]["output"] == "Hello World"


def test_run_history_uses_limit_status_and_cursor(tmp_path: Path) -> None:
    with authenticated_client(tmp_path) as client:
        first_id = run_reference(client)
        second_id = run_reference(client)
        first_page = client.get(f"/console/api/apps/{APP_ID}/workflow-runs?limit=1")
        second_page = client.get(
            f"/console/api/apps/{APP_ID}/workflow-runs",
            params={"limit": 1, "last_id": second_id},
        )
        failed = client.get(
            f"/console/api/apps/{APP_ID}/workflow-runs",
            params={"limit": 20, "status": "failed"},
        )

    assert first_page.json()["has_more"] is True
    assert first_page.json()["data"][0]["id"] == second_id
    assert second_page.json()["has_more"] is False
    assert second_page.json()["data"][0]["id"] == first_id
    assert failed.json()["data"] == []
