import copy
import json
from pathlib import Path

import yaml
from auth_helpers import authenticated_client
from fastapi.testclient import TestClient
from test_workflow_rbac import MEMBER, create_member, csrf, login, setup_application

from api.app import DEFAULT_APP_ID

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads((ROOT / "contracts/fixtures/reference-workflow.json").read_text(encoding="utf-8"))
APPS_PATH = "/console/api/apps"


def create_workflow(client: TestClient, name: str, **metadata) -> dict:
    response = client.post(
        APPS_PATH,
        json={
            "name": name,
            "mode": "workflow",
            "description": "Second workflow",
            "icon_type": "emoji",
            "icon": "🧪",
            "icon_background": "#FFFFFF",
            **metadata,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def save_draft(client: TestClient, app_id: str, graph: dict | None = None) -> None:
    response = client.post(
        f"{APPS_PATH}/{app_id}/workflows/draft",
        json={
            "graph": graph or copy.deepcopy(FIXTURE["graph"]),
            "features": {},
            "conversation_variables": [],
            "hash": None,
        },
    )
    assert response.status_code == 200, response.text


def test_workflow_list_create_update_copy_delete_and_execution_are_real(tmp_path: Path) -> None:
    with authenticated_client(tmp_path) as client:
        initial = client.get(APPS_PATH, params={"limit": 30})
        assert initial.status_code == 200
        assert [item["id"] for item in initial.json()["data"]] == [DEFAULT_APP_ID]

        created = create_workflow(client, "Second workflow")
        app_id = created["id"]
        assert app_id != DEFAULT_APP_ID
        assert created["mode"] == "workflow"
        assert created["name"] == "Second workflow"
        assert created["description"] == "Second workflow"
        assert created["icon"] == "🧪"
        assert created["maintainer"]
        assert "app.acl.edit" in created["permission_keys"]

        listed = client.get(APPS_PATH, params={"limit": 30, "sort_by": "last_modified"})
        searched = client.get(APPS_PATH, params={"name": "Second", "limit": 30})
        wrong_mode = client.get(APPS_PATH, params={"mode": "advanced-chat", "limit": 30})
        assert {item["id"] for item in listed.json()["data"]} == {DEFAULT_APP_ID, app_id}
        assert [item["id"] for item in searched.json()["data"]] == [app_id]
        assert wrong_mode.json()["data"] == []

        save_draft(client, app_id)
        with client.stream(
            "POST",
            f"{APPS_PATH}/{app_id}/workflows/draft/run",
            json={"inputs": FIXTURE["inputs"], "files": []},
        ) as response:
            events = [json.loads(line[6:]) for line in response.iter_lines() if line.startswith("data: ")]
        assert events[-1]["data"]["status"] == "succeeded"
        assert events[-1]["data"]["outputs"] == FIXTURE["expected_outputs"]

        updated = client.put(
            f"{APPS_PATH}/{app_id}",
            json={
                "name": "Renamed workflow",
                "description": "Updated",
                "icon_type": "emoji",
                "icon": "🚀",
                "icon_background": "#000000",
            },
        )
        assert updated.status_code == 200
        assert updated.json()["name"] == "Renamed workflow"
        assert updated.json()["description"] == "Updated"
        assert updated.json()["icon"] == "🚀"

        copied_response = client.post(
            f"{APPS_PATH}/{app_id}/copy",
            json={"name": "Copied workflow", "icon_type": "emoji", "icon": "📋", "icon_background": "#EEEEEE"},
        )
        assert copied_response.status_code == 201, copied_response.text
        copied = copied_response.json()
        copied_draft = client.get(f"{APPS_PATH}/{copied['id']}/workflows/draft")
        assert copied_draft.status_code == 200
        assert copied_draft.json()["graph"] == FIXTURE["graph"]

        deleted_source = client.delete(f"{APPS_PATH}/{app_id}")
        copied_after_source_delete = client.get(f"{APPS_PATH}/{copied['id']}/workflows/draft")
        deleted_copy = client.delete(f"{APPS_PATH}/{copied['id']}")
        missing_copy = client.get(f"{APPS_PATH}/{copied['id']}")
        final_list = client.get(APPS_PATH, params={"limit": 30})

    assert deleted_source.status_code == 204
    assert copied_after_source_delete.status_code == 200
    assert deleted_copy.status_code == 204
    assert missing_copy.status_code == 404
    assert [item["id"] for item in final_list.json()["data"]] == [DEFAULT_APP_ID]


def test_app_list_and_management_enforce_global_account_roles(tmp_path: Path) -> None:
    app = setup_application(tmp_path)
    create_member(tmp_path, "normal")
    with login(app, MEMBER["email"], MEMBER["password"]) as member:
        member.headers.update(csrf(member))
        visible = member.get(APPS_PATH)
        denied_create = member.post(APPS_PATH, json={"name": "Denied", "mode": "workflow"})
        denied_copy = member.post(f"{APPS_PATH}/{DEFAULT_APP_ID}/copy", json={})
        denied_edit = member.put(f"{APPS_PATH}/{DEFAULT_APP_ID}", json={"name": "Denied"})
        denied_delete = member.delete(f"{APPS_PATH}/{DEFAULT_APP_ID}")

    assert [item["id"] for item in visible.json()["data"]] == [DEFAULT_APP_ID]
    assert denied_create.status_code == 403
    assert denied_copy.status_code == 403
    assert denied_edit.status_code == 403
    assert denied_delete.status_code == 403


def test_yaml_import_can_create_a_new_workflow_from_the_original_list_flow(tmp_path: Path) -> None:
    with authenticated_client(tmp_path) as client:
        save_draft(client, DEFAULT_APP_ID)
        exported = client.get(f"{APPS_PATH}/{DEFAULT_APP_ID}/export")
        document = yaml.safe_load(exported.json()["data"])
        document["app"]["name"] = "Imported from list"

        imported = client.post(
            f"{APPS_PATH}/imports",
            json={"mode": "yaml-content", "yaml_content": yaml.safe_dump(document), "name": "Imported from list"},
        )
        assert imported.status_code == 200, imported.text
        assert imported.json()["status"] == "completed"
        app_id = imported.json()["app_id"]
        assert app_id != DEFAULT_APP_ID

        draft = client.get(f"{APPS_PATH}/{app_id}/workflows/draft")
        listed = client.get(APPS_PATH, params={"name": "Imported from list"})

    assert draft.status_code == 200
    assert draft.json()["graph"] == FIXTURE["graph"]
    assert [item["id"] for item in listed.json()["data"]] == [app_id]
