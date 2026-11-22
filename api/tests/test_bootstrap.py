import json
from pathlib import Path

from auth_helpers import ADMIN, authenticated_client

from api.app import DEFAULT_APP_ID

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads((ROOT / "contracts/frontend-runtime.json").read_text())
ROLES = json.loads((ROOT / "contracts/permissions.json").read_text(encoding="utf-8"))["roles"]


def test_authenticated_account_matches_the_setup_identity(tmp_path: Path) -> None:
    with authenticated_client(tmp_path) as client:
        profile = client.get("/console/api/account/profile").json()
        permissions = client.get("/console/api/account/permissions").json()
        system_features = client.get("/console/api/system-features").json()
        features = client.get("/console/api/features").json()
        upload = client.get("/console/api/files/upload").json()

    assert profile["email"] == ADMIN["email"]
    assert profile["name"] == ADMIN["name"]
    assert profile["is_password_set"] is True
    assert profile["role"] == "owner"
    assert set(permissions["account"]["permission_keys"]) == set(ROLES["owner"]["account"])
    assert set(permissions["app"]["default_permission_keys"]) == set(ROLES["owner"]["app"])
    assert system_features["enable_email_password_login"] is True
    assert system_features["rbac_enabled"] is True
    assert features["workflow_collaboration_enabled"] is False
    assert upload == FIXTURE["upload_config"]


def test_app_detail_contains_generated_client_fields_and_owner_permissions(tmp_path: Path) -> None:
    with authenticated_client(tmp_path) as client:
        response = client.get(f"/console/api/apps/{DEFAULT_APP_ID}")
        profile = client.get("/console/api/account/profile").json()

    assert response.status_code == 200
    app = response.json()
    for key, value in FIXTURE["app_required"].items():
        if key != "permission_keys":
            assert app[key] == value, key
    assert set(app["permission_keys"]) == set(ROLES["owner"]["app"])
    assert app["maintainer"] == profile["id"]


def test_original_editor_supporting_queries_have_explicit_empty_or_unpublished_results(tmp_path: Path) -> None:
    with authenticated_client(tmp_path) as client:
        assert client.get(f"/console/api/apps/{DEFAULT_APP_ID}/workflows/publish").json() is None
        assert client.get(f"/console/api/apps/{DEFAULT_APP_ID}/triggers").json() == {"data": []}
        for path in (
            "/console/api/workspaces/current/tools/builtin",
            "/console/api/workspaces/current/tools/api",
            "/console/api/workspaces/current/tools/workflow",
            "/console/api/workspaces/current/tools/mcp",
            "/console/api/workspaces/current/agent-providers",
            "/console/api/workspaces/current/triggers",
            "/console/api/spec/schema-definitions",
        ):
            response = client.get(path)
            assert response.status_code == 200, path
            assert response.json() == [], path

        empty_objects = {
            "/console/api/workspaces/current/model-providers/summary": {"data": [], "plugins": {}},
            f"/console/api/apps/{DEFAULT_APP_ID}/workflows/draft/environment-variables": {"items": []},
            f"/console/api/apps/{DEFAULT_APP_ID}/workflows/draft/conversation-variables": {"items": []},
            f"/console/api/apps/{DEFAULT_APP_ID}/workflows/draft/variables?page=1&limit=100": {
                "items": [],
                "total": 0,
            },
            f"/console/api/apps/{DEFAULT_APP_ID}/workflows/draft/system-variables": {"items": []},
            "/console/api/app-dsl-version": {"app_dsl_version": "0.7.0"},
            "/console/api/workspaces/current/default-model?model_type=llm": {"data": None},
            "/console/api/workspaces/current/models/model-types/text-embedding": {"data": []},
            "/console/api/workspaces/current/models/model-types/rerank": {"data": []},
        }
        for path, expected in empty_objects.items():
            response = client.get(path)
            assert response.status_code == 200, path
            assert response.json() == expected, path


def test_unknown_default_block_config_uses_the_dify_empty_success_response(tmp_path: Path) -> None:
    with authenticated_client(tmp_path) as client:
        response = client.get(f"/console/api/apps/{DEFAULT_APP_ID}/workflows/default-workflow-block-configs/not-a-node")

    assert response.status_code == 200
    assert response.json() == {}
