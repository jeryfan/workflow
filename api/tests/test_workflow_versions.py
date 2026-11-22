import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from auth_helpers import ADMIN, authenticated_client
from fastapi.testclient import TestClient
from test_workflow_rbac import MEMBER, create_member, csrf, login, setup_application

from api.main import DEFAULT_APP_ID
from api.models.entities import (
    NodeFinished,
    NodeStarted,
    RunIdentity,
    RunSnapshot,
    RunSucceeded,
    WorkflowFinished,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads((ROOT / "contracts/fixtures/reference-workflow.json").read_text(encoding="utf-8"))
OTHER_APP_ID = "00000000-0000-0000-0000-000000000002"
DRAFT_PATH = f"/console/api/apps/{DEFAULT_APP_ID}/workflows/draft"
VERSIONS_PATH = f"/console/api/apps/{DEFAULT_APP_ID}/workflows"
PUBLISH_PATH = f"{VERSIONS_PATH}/publish"
NOW = datetime(2026, 1, 1, tzinfo=UTC)
WORKFLOW_RESPONSE_FIELDS = {
    "id",
    "graph",
    "features",
    "hash",
    "version",
    "version_number",
    "marked_name",
    "marked_comment",
    "created_by",
    "created_at",
    "updated_by",
    "updated_at",
    "tool_published",
    "environment_variables",
    "conversation_variables",
    "rag_pipeline_variables",
}


def save_draft(client: TestClient, graph: dict, app_id: str = DEFAULT_APP_ID) -> dict:
    path = f"/console/api/apps/{app_id}/workflows/draft"
    current = client.get(path)
    hash_value = current.json()["hash"] if current.status_code == 200 else None
    response = client.post(
        path,
        json={
            "graph": graph,
            "features": {},
            "conversation_variables": [],
            "hash": hash_value,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def publish(client: TestClient, name: str = "", comment: str = "") -> str:
    response = client.post(
        PUBLISH_PATH,
        headers=csrf(client),
        json={"marked_name": name, "marked_comment": comment},
    )
    assert response.status_code == 200, response.text
    current = client.get(PUBLISH_PATH)
    assert current.status_code == 200
    return current.json()["id"]


def changed_graph(template: str) -> dict:
    graph = copy.deepcopy(FIXTURE["graph"])
    graph["nodes"][1]["data"]["template"] = template
    return graph


def create_version_history(client: TestClient) -> tuple[str, str, dict]:
    save_draft(client, FIXTURE["graph"])
    first_id = publish(client, "Initial", "Ready")
    second_graph = changed_graph("Second {{ name }}")
    save_draft(client, second_graph)
    second_id = publish(client)
    draft_graph = changed_graph("Unpublished {{ name }}")
    save_draft(client, draft_graph)
    return first_id, second_id, second_graph


def version_path(workflow_id: str, app_id: str = DEFAULT_APP_ID) -> str:
    return f"/console/api/apps/{app_id}/workflows/{workflow_id}"


def test_version_history_includes_draft_current_version_and_original_pagination(tmp_path: Path) -> None:
    with authenticated_client(tmp_path) as client:
        first_id, second_id, second_graph = create_version_history(client)
        response = client.get(VERSIONS_PATH, params={"page": 1, "limit": 10, "user_id": "", "named_only": False})
        first_page = client.get(VERSIONS_PATH, params={"page": 1, "limit": 1})
        second_page = client.get(VERSIONS_PATH, params={"page": 2, "limit": 1})
        third_page = client.get(VERSIONS_PATH, params={"page": 3, "limit": 1})
        named = client.get(VERSIONS_PATH, params={"named_only": True})
        admin_id = response.json()["items"][0]["created_by"]["id"]
        own = client.get(VERSIONS_PATH, params={"user_id": admin_id})
        other_user = client.get(VERSIONS_PATH, params={"user_id": "someone-else"})
        current = client.get(PUBLISH_PATH)

    assert set(response.json()) == {"items", "page", "limit", "has_more"}
    assert response.json()["page"] == 1
    assert response.json()["limit"] == 10
    assert response.json()["has_more"] is False
    items = response.json()["items"]
    assert [item["id"] for item in items] == [items[0]["id"], second_id, first_id]
    assert items[0]["version"] == "draft"
    assert items[0]["graph"]["nodes"][1]["data"]["template"] == "Unpublished {{ name }}"
    assert items[1]["graph"] == second_graph
    assert items[1]["version_number"] == 1
    assert items[2]["graph"] == FIXTURE["graph"]
    assert all(set(item) == WORKFLOW_RESPONSE_FIELDS for item in items)
    assert current.json()["id"] == second_id

    assert [page.json()["items"][0]["id"] for page in (first_page, second_page, third_page)] == [
        items[0]["id"],
        second_id,
        first_id,
    ]
    assert [page.json()["has_more"] for page in (first_page, second_page, third_page)] == [True, True, False]
    assert [item["id"] for item in named.json()["items"]] == [first_id]
    assert [item["id"] for item in own.json()["items"]] == [item["id"] for item in items]
    assert other_user.status_code == 403


def test_metadata_edit_changes_only_name_and_comment_on_a_real_version(tmp_path: Path) -> None:
    with authenticated_client(tmp_path) as client:
        first_id, second_id, second_graph = create_version_history(client)
        response = client.patch(
            version_path(first_id),
            json={"marked_name": "Renamed", "marked_comment": "Revised notes"},
        )
        versions = client.get(VERSIONS_PATH).json()["items"]
        current = client.get(PUBLISH_PATH)
        missing = client.patch(version_path("missing"), json={"marked_name": "Missing"})

    assert response.status_code == 200
    assert set(response.json()) == WORKFLOW_RESPONSE_FIELDS
    assert response.json()["id"] == first_id
    assert response.json()["marked_name"] == "Renamed"
    assert response.json()["marked_comment"] == "Revised notes"
    assert response.json()["graph"] == FIXTURE["graph"]
    assert response.json()["created_by"]["email"] == ADMIN["email"]
    persisted = next(item for item in versions if item["id"] == first_id)
    assert persisted["marked_name"] == "Renamed"
    assert persisted["marked_comment"] == "Revised notes"
    assert current.json()["id"] == second_id
    assert current.json()["graph"] == second_graph
    assert current.json()["marked_name"] == ""
    assert missing.status_code == 404


def test_restore_copies_the_snapshot_into_the_same_draft_without_publishing(tmp_path: Path) -> None:
    with authenticated_client(tmp_path) as client:
        save_draft(client, FIXTURE["graph"])
        source_id = publish(client, "Initial", "Ready")
        source = client.get(PUBLISH_PATH).json()
        changed = save_draft(client, changed_graph("Changed {{ name }}"))
        before = client.get(DRAFT_PATH).json()

        response = client.post(f"{version_path(source_id)}/restore")
        restored = client.get(DRAFT_PATH).json()
        current = client.get(PUBLISH_PATH).json()
        versions = client.get(VERSIONS_PATH).json()["items"]

    assert response.status_code == 200
    assert set(response.json()) == {"result", "hash", "updated_at"}
    assert response.json()["result"] == "success"
    assert response.json()["hash"] == restored["hash"]
    assert response.json()["hash"] != changed["hash"]
    assert response.json()["updated_at"] == restored["updated_at"]
    assert restored["id"] == before["id"]
    assert restored["graph"] == FIXTURE["graph"]
    assert restored["updated_by"]["email"] == ADMIN["email"]
    assert restored["updated_by"]["id"] != "local-user"
    assert restored["updated_at"] >= before["updated_at"]
    assert current["id"] == source_id
    assert current["graph"] == source["graph"]
    assert len(versions) == 2


def test_delete_removes_only_a_historical_version(tmp_path: Path) -> None:
    with authenticated_client(tmp_path) as client:
        first_id, second_id, _ = create_version_history(client)
        deleted = client.delete(version_path(first_id))
        versions = client.get(VERSIONS_PATH).json()["items"]
        current = client.get(PUBLISH_PATH)
        current_delete = client.delete(version_path(second_id))
        missing = client.delete(version_path(first_id))
        current_after_rejection = client.get(PUBLISH_PATH)

    assert deleted.status_code == 204
    assert deleted.content == b""
    assert [item["id"] for item in versions] == [versions[0]["id"], second_id]
    assert current.json()["id"] == second_id
    assert current_delete.status_code == 400
    assert "currently in use by app" in current_delete.json()["message"]
    assert missing.status_code == 404
    assert current_after_rejection.json()["id"] == second_id


def test_direct_runner_cannot_edit_restore_or_delete_versions(tmp_path: Path) -> None:
    app = setup_application(tmp_path)
    with login(app, ADMIN["email"], ADMIN["password"]) as owner:
        owner.headers.update(csrf(owner))
        source_id = publish(owner, "Original", "Do not change")
        before = owner.get(DRAFT_PATH).json()

    create_member(tmp_path, "normal")
    with login(app, MEMBER["email"], MEMBER["password"]) as runner:
        headers = csrf(runner)
        edited = runner.patch(
            version_path(source_id),
            headers=headers,
            json={"marked_name": "Denied", "marked_comment": "Denied"},
        )
        restored = runner.post(f"{version_path(source_id)}/restore", headers=headers)
        deleted = runner.delete(version_path(source_id), headers=headers)

    with login(app, ADMIN["email"], ADMIN["password"]) as owner:
        owner.headers.update(csrf(owner))
        source = owner.get(PUBLISH_PATH).json()
        draft = owner.get(DRAFT_PATH).json()

    assert [edited.status_code, restored.status_code, deleted.status_code] == [403, 403, 403]
    assert source["marked_name"] == "Original"
    assert source["marked_comment"] == "Do not change"
    assert draft["hash"] == before["hash"]


def test_versions_from_another_app_are_not_found_or_modified(tmp_path: Path) -> None:
    with authenticated_client(tmp_path) as client:
        first_id, second_id, _ = create_version_history(client)
        client.app.state.store.ensure_app(OTHER_APP_ID)
        edited = client.patch(version_path(first_id, OTHER_APP_ID), json={"marked_name": "Cross app"})
        restored = client.post(f"{version_path(first_id, OTHER_APP_ID)}/restore")
        deleted = client.delete(version_path(second_id, OTHER_APP_ID))
        source = client.get(PUBLISH_PATH).json()
        versions = client.get(VERSIONS_PATH).json()["items"]

    assert [edited.status_code, restored.status_code, deleted.status_code] == [404, 404, 404]
    assert source["id"] == second_id
    assert {item["id"] for item in versions} == {first_id, second_id, versions[0]["id"]}
    assert next(item for item in versions if item["id"] == first_id)["marked_name"] == "Initial"


def persist_template_run(
    client: TestClient,
    run_id: str,
    execution_id: str,
    output: str,
    created_at: datetime,
) -> None:
    store = client.app.state.store
    draft = store.get_draft(DEFAULT_APP_ID)
    assert draft is not None
    identity = RunIdentity(
        app_id=DEFAULT_APP_ID,
        workflow_id=draft.workflow_id,
        workflow_run_id=run_id,
        task_id=f"task-{run_id}",
    )
    store.create_run(
        RunSnapshot(
            identity=identity,
            graph=draft.graph,
            draft_hash=draft.hash,
            inputs={"name": "World"},
            actor=draft.updated_by,
            created_at=created_at,
        )
    )
    store.project_event(
        NodeStarted(
            identity=identity,
            sequence=0,
            execution_id=execution_id,
            node_id="template",
            node_type="template-transform",
            title="Template",
            index=1,
            predecessor_node_id="start",
            created_at=created_at,
        )
    )
    store.project_event(
        NodeFinished(
            identity=identity,
            sequence=1,
            execution_id=execution_id,
            node_id="template",
            node_type="template-transform",
            title="Template",
            index=1,
            predecessor_node_id="start",
            inputs={"name": "World"},
            process_data={},
            outputs={"output": output},
            status="succeeded",
            error=None,
            elapsed_time=0.01,
            created_at=created_at,
            finished_at=created_at,
        )
    )
    store.project_event(
        WorkflowFinished(
            identity=identity,
            sequence=2,
            outcome=RunSucceeded(outputs={"message": output}, exceptions_count=0),
            elapsed_time=0.02,
            total_tokens=0,
            total_steps=1,
            created_at=created_at,
            finished_at=created_at,
        )
    )


def test_last_run_returns_the_latest_app_scoped_node_execution(tmp_path: Path) -> None:
    with authenticated_client(tmp_path) as client:
        save_draft(client, FIXTURE["graph"])
        persist_template_run(client, "run-1", "execution-1", "First", NOW)
        persist_template_run(client, "run-2", "execution-2", "Second", NOW + timedelta(seconds=1))

        response = client.get(f"{DRAFT_PATH}/nodes/template/last-run")
        missing_node = client.get(f"{DRAFT_PATH}/nodes/missing/last-run")

        client.app.state.store.ensure_app(OTHER_APP_ID)
        save_draft(client, FIXTURE["graph"], OTHER_APP_ID)
        other_app = client.get(f"/console/api/apps/{OTHER_APP_ID}/workflows/draft/nodes/template/last-run")

    assert response.status_code == 200
    assert response.json()["id"] == "execution-2"
    assert response.json()["id"] != "execution-1"
    assert response.json()["node_id"] == "template"
    assert response.json()["outputs"] == {"output": "Second"}
    assert response.json()["status"] == "succeeded"
    assert missing_node.status_code == 404
    assert other_app.status_code == 404
