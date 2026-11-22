import copy
import json
from pathlib import Path

import yaml
from auth_helpers import authenticated_client
from fastapi.testclient import TestClient
from test_workflow_rbac import MEMBER, create_member, csrf, login, setup_application

from api.main import DEFAULT_APP_ID

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads((ROOT / "contracts/fixtures/reference-workflow.json").read_text(encoding="utf-8"))
DRAFT_PATH = f"/console/api/apps/{DEFAULT_APP_ID}/workflows/draft"
EXPORT_PATH = f"/console/api/apps/{DEFAULT_APP_ID}/export"
IMPORT_PATH = "/console/api/apps/imports"


def save_draft(client: TestClient, graph: dict | None = None, features: dict | None = None) -> dict:
    current = client.get(DRAFT_PATH)
    response = client.post(
        DRAFT_PATH,
        json={
            "graph": graph or copy.deepcopy(FIXTURE["graph"]),
            "features": features or {},
            "conversation_variables": [],
            "hash": current.json().get("hash") if current.status_code == 200 else None,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def export_document(client: TestClient, **query) -> dict:
    response = client.get(EXPORT_PATH, params=query)
    assert response.status_code == 200, response.text
    document = yaml.safe_load(response.json()["data"])
    assert isinstance(document, dict)
    return document


def import_yaml(client: TestClient, document: dict | str, app_id: str | None = DEFAULT_APP_ID):
    content = document if isinstance(document, str) else yaml.safe_dump(document, allow_unicode=True, sort_keys=False)
    return client.post(IMPORT_PATH, json={"mode": "yaml-content", "yaml_content": content, "app_id": app_id})


def changed_graph(template: str) -> dict:
    graph = copy.deepcopy(FIXTURE["graph"])
    graph["nodes"][1]["data"]["template"] = template
    return graph


def test_export_uses_the_dify_envelope_and_redacts_secret_values_by_default(tmp_path: Path) -> None:
    with authenticated_client(tmp_path) as client:
        save_draft(client)
        variables = [
            {"id": "secret-1", "name": "API_TOKEN", "value_type": "secret", "value": "sensitive", "description": ""},
            {"id": "string-1", "name": "GREETING", "value_type": "string", "value": "Hello", "description": ""},
        ]
        updated = client.post(f"{DRAFT_PATH}/environment-variables", json={"environment_variables": variables})
        assert updated.status_code == 200

        document = export_document(client)
        with_secrets = export_document(client, include_secret=True)

    assert document["version"] == "0.7.0"
    assert document["kind"] == "app"
    assert document["app"]["mode"] == "workflow"
    assert document["app"]["name"] == "Workflow"
    assert document["dependencies"] == []
    assert document["workflow"]["graph"] == FIXTURE["graph"]
    assert document["workflow"]["features"] == {}
    assert [variable["value"] for variable in document["workflow"]["environment_variables"]] == ["", "Hello"]
    assert [variable["value"] for variable in with_secrets["workflow"]["environment_variables"]] == [
        "sensitive",
        "Hello",
    ]


def test_export_selects_a_published_version_instead_of_the_current_draft(tmp_path: Path) -> None:
    with authenticated_client(tmp_path) as client:
        save_draft(client)
        published = client.post(f"/console/api/apps/{DEFAULT_APP_ID}/workflows/publish", json={})
        assert published.status_code == 200
        workflow_id = client.get(f"/console/api/apps/{DEFAULT_APP_ID}/workflows/publish").json()["id"]
        save_draft(client, changed_graph("Draft {{ name }}"))

        document = export_document(client, workflow_id=workflow_id)
        missing = client.get(EXPORT_PATH, params={"workflow_id": "missing"})
        unsupported_package = client.get(EXPORT_PATH, params={"format": "ifpkg"})

    assert document["workflow"]["graph"] == FIXTURE["graph"]
    assert missing.status_code == 404
    assert unsupported_package.status_code == 400


def test_yaml_import_replaces_the_draft_variables_features_and_app_name(tmp_path: Path) -> None:
    with authenticated_client(tmp_path) as client:
        save_draft(client)
        document = export_document(client)
        document["app"]["name"] = "Imported Workflow"
        document["workflow"]["graph"] = changed_graph("Imported {{ name }}")
        document["workflow"]["features"] = {"opening_statement": "Welcome"}
        document["workflow"]["environment_variables"] = [
            {"id": "env-1", "name": "GREETING", "value_type": "string", "value": "Hi", "description": "Imported"}
        ]
        document["workflow"]["conversation_variables"] = [
            {"id": "conv-1", "name": "counter", "value_type": "number", "value": 0, "description": ""}
        ]

        response = import_yaml(client, document)
        draft = client.get(DRAFT_PATH)
        app = client.get(f"/console/api/apps/{DEFAULT_APP_ID}")
        roundtrip = export_document(client)

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed"
    assert response.json()["app_id"] == DEFAULT_APP_ID
    assert response.json()["app_mode"] == "workflow"
    assert response.json()["current_dsl_version"] == "0.7.0"
    assert response.json()["imported_dsl_version"] == "0.7.0"
    assert response.json()["warnings"] == []
    assert draft.json()["graph"] == document["workflow"]["graph"]
    assert draft.json()["features"] == document["workflow"]["features"]
    assert draft.json()["environment_variables"] == document["workflow"]["environment_variables"]
    assert draft.json()["conversation_variables"] == document["workflow"]["conversation_variables"]
    assert app.json()["name"] == "Imported Workflow"
    assert roundtrip["workflow"]["graph"] == document["workflow"]["graph"]


def test_invalid_or_unsupported_yaml_returns_a_failed_import_without_replacing_the_draft(tmp_path: Path) -> None:
    with authenticated_client(tmp_path) as client:
        save_draft(client)
        original = client.get(DRAFT_PATH).json()
        unsupported = export_document(client)
        unsupported["workflow"]["graph"]["nodes"][1]["data"]["type"] = "tool"
        wrong_mode = export_document(client)
        wrong_mode["app"]["mode"] = "advanced-chat"

        responses = [
            import_yaml(client, "workflow: ["),
            import_yaml(client, ""),
            import_yaml(client, unsupported),
            import_yaml(client, wrong_mode),
            client.post(
                IMPORT_PATH,
                json={"mode": "yaml-url", "yaml_url": "https://example.com/workflow.yml", "app_id": DEFAULT_APP_ID},
            ),
        ]
        after = client.get(DRAFT_PATH).json()

    for response in responses:
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "failed"
        assert response.json()["error"]
        assert response.json()["id"]
    assert after["graph"] == original["graph"]
    assert after["hash"] == original["hash"]


def test_version_compatibility_and_pending_confirmation_match_dify(tmp_path: Path) -> None:
    with authenticated_client(tmp_path) as client:
        save_draft(client)
        older = export_document(client)
        older["version"] = "0.6.0"
        older["workflow"]["graph"] = changed_graph("Older {{ name }}")
        older_response = import_yaml(client, older)
        assert older_response.json()["status"] == "completed-with-warnings"
        assert client.get(DRAFT_PATH).json()["graph"] == older["workflow"]["graph"]

        newer = export_document(client)
        newer["version"] = "0.8.0"
        newer["workflow"]["graph"] = changed_graph("Pending {{ name }}")
        pending = import_yaml(client, newer)
        before_confirm = client.get(DRAFT_PATH).json()
        confirmed = client.post(f"{IMPORT_PATH}/{pending.json()['id']}/confirm")
        repeated = client.post(f"{IMPORT_PATH}/{pending.json()['id']}/confirm")
        after_confirm = client.get(DRAFT_PATH).json()
        missing = client.post(f"{IMPORT_PATH}/missing/confirm")

        invalid = export_document(client)
        invalid["version"] = "not-a-version"
        invalid_response = import_yaml(client, invalid)

    assert older_response.status_code == 200
    assert pending.json()["status"] == "pending"
    assert pending.json()["imported_dsl_version"] == "0.8.0"
    assert before_confirm["graph"] == older["workflow"]["graph"]
    assert confirmed.json()["status"] == "completed"
    assert confirmed.json()["imported_dsl_version"] == "0.8.0"
    assert repeated.json()["status"] == "failed"
    assert after_confirm["graph"] == newer["workflow"]["graph"]
    assert missing.json()["status"] == "failed"
    assert invalid_response.json()["status"] == "failed"


def test_import_export_requires_the_dsl_permission_even_when_read_and_run_are_allowed(tmp_path: Path) -> None:
    app = setup_application(tmp_path)
    create_member(tmp_path, "normal")
    with login(app, MEMBER["email"], MEMBER["password"]) as client:
        client.headers.update(csrf(client))
        exported = client.get(EXPORT_PATH)
        imported = import_yaml(client, {"kind": "app", "version": "0.7.0"})
        dependencies = client.get(f"{IMPORT_PATH}/{DEFAULT_APP_ID}/check-dependencies")

    assert exported.status_code == 403
    assert imported.status_code == 403
    assert dependencies.status_code == 200
    assert dependencies.json() == {"leaked_dependencies": []}


def test_pending_import_is_bound_to_the_account_that_staged_it(tmp_path: Path) -> None:
    app = setup_application(tmp_path)
    create_member(tmp_path, "editor")
    with login(app, "admin@example.com", "Password123") as owner:
        owner.headers.update(csrf(owner))
        document = export_document(owner)
        document["version"] = "0.8.0"
        document["workflow"]["graph"] = changed_graph("Owner pending {{ name }}")
        pending = import_yaml(owner, document)
        assert pending.json()["status"] == "pending"

    with login(app, MEMBER["email"], MEMBER["password"]) as member:
        member.headers.update(csrf(member))
        denied = member.post(f"{IMPORT_PATH}/{pending.json()['id']}/confirm")

    with login(app, "admin@example.com", "Password123") as owner:
        owner.headers.update(csrf(owner))
        confirmed = owner.post(f"{IMPORT_PATH}/{pending.json()['id']}/confirm")

    assert denied.status_code == 200
    assert denied.json()["status"] == "failed"
    assert confirmed.json()["status"] == "completed"
