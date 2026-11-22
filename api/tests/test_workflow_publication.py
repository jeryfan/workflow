import copy
import json
import sqlite3
from pathlib import Path

from auth_helpers import authenticated_client
from test_workflow_rbac import MEMBER, create_member, csrf, login, setup_application

from api.app import DEFAULT_APP_ID

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads((ROOT / "contracts/fixtures/reference-workflow.json").read_text(encoding="utf-8"))
DRAFT_PATH = f"/console/api/apps/{DEFAULT_APP_ID}/workflows/draft"
PUBLISH_PATH = f"/console/api/apps/{DEFAULT_APP_ID}/workflows/publish"


def test_publication_preserves_versions_and_returns_the_current_snapshot(tmp_path: Path) -> None:
    with authenticated_client(tmp_path) as client:
        saved = client.post(
            DRAFT_PATH,
            json={"graph": FIXTURE["graph"], "features": {}, "conversation_variables": [], "hash": None},
        )
        first = client.post(PUBLISH_PATH, json={"marked_name": "Initial", "marked_comment": "Ready"})
        assert first.status_code == 200
        changed_graph = copy.deepcopy(FIXTURE["graph"])
        changed_graph["nodes"][1]["data"]["template"] = "Changed {{ name }}"
        client.post(
            DRAFT_PATH,
            json={"graph": changed_graph, "features": {}, "conversation_variables": [], "hash": saved.json()["hash"]},
        )
        second = client.post(PUBLISH_PATH, json={})
        current = client.get(PUBLISH_PATH)

    assert first.json()["result"] == "success"
    assert second.json()["created_at"] >= first.json()["created_at"]
    assert current.json()["version_number"] == 1
    assert current.json()["graph"] == changed_graph
    assert current.json()["created_by"]["email"] == "admin@example.com"
    with sqlite3.connect(tmp_path / "workflow.db") as connection:
        versions = connection.execute(
            "SELECT version_number, graph_json FROM workflow_versions ORDER BY version_number"
        ).fetchall()
    assert len(versions) == 2
    assert json.loads(versions[0][1]) == FIXTURE["graph"]
    assert json.loads(versions[1][1]) == changed_graph


def test_workflow_runner_cannot_publish(tmp_path: Path) -> None:
    app = setup_application(tmp_path)
    create_member(tmp_path, "normal")
    with login(app, MEMBER["email"], MEMBER["password"]) as client:
        response = client.post(PUBLISH_PATH, headers=csrf(client), json={})
    assert response.status_code == 403
    with sqlite3.connect(tmp_path / "workflow.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM workflow_versions").fetchone()[0] == 0
