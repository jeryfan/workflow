import copy
import json
from pathlib import Path

from auth_helpers import authenticated_client

from api.main import DEFAULT_APP_ID

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads((ROOT / "contracts/fixtures/reference-workflow.json").read_text(encoding="utf-8"))
DRAFT_PATH = f"/console/api/apps/{DEFAULT_APP_ID}/workflows/draft"
PUBLISH_PATH = f"/console/api/apps/{DEFAULT_APP_ID}/workflows/publish"
APP_PATH = f"/console/api/apps/{DEFAULT_APP_ID}"


def publish_reference(client) -> str:
    response = client.post(
        DRAFT_PATH,
        json={"graph": FIXTURE["graph"], "features": {}, "conversation_variables": [], "hash": None},
    )
    assert response.status_code == 200, response.text
    response = client.post(PUBLISH_PATH, json={})
    assert response.status_code == 200, response.text
    app = client.get(APP_PATH).json()
    assert app["enable_site"] is True
    assert app["workflow"]["id"]
    return app["site"]["access_token"]


def public_headers(token: str) -> dict[str, str]:
    return {"X-App-Code": token}


def workflow_result(client, token: str) -> dict:
    response = client.post(
        "/api/workflows/run",
        headers=public_headers(token),
        json={"inputs": {"name": "World"}, "files": [], "response_mode": "streaming"},
    )
    assert response.status_code == 200, response.text
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    assert events[-1]["event"] == "workflow_finished"
    return events[-1]["data"]


def test_published_workflow_has_a_real_public_run_page_api(tmp_path: Path) -> None:
    with authenticated_client(tmp_path) as client:
        token = publish_reference(client)
        site = client.get("/api/site", headers=public_headers(token))
        parameters = client.get("/api/parameters", headers=public_headers(token))
        meta = client.get("/api/meta", headers=public_headers(token))
        permission = client.get("/api/webapp/permission", headers=public_headers(token))
        login_status = client.get("/api/login/status", params={"app_code": token})
        access_mode = client.get("/api/webapp/access-mode", params={"appCode": token})
        first_result = workflow_result(client, token)

        changed = copy.deepcopy(FIXTURE["graph"])
        changed["nodes"][1]["data"]["template"] = "Draft {{ name }}"
        current = client.get(DRAFT_PATH).json()
        saved = client.post(
            DRAFT_PATH,
            json={
                "graph": changed,
                "features": {},
                "conversation_variables": [],
                "hash": current["hash"],
            },
        )
        assert saved.status_code == 200
        second_result = workflow_result(client, token)

    assert site.status_code == 200
    assert site.json()["app_id"] == DEFAULT_APP_ID
    assert site.json()["site"]["title"] == "Workflow"
    assert parameters.json()["user_input_form"][0]["text-input"]["variable"] == "name"
    assert meta.json() == {"tool_icons": {}}
    assert permission.json() == {"result": True}
    assert login_status.status_code == 200
    assert login_status.json() == {"logged_in": True, "app_logged_in": True}
    assert access_mode.status_code == 200
    assert access_mode.json() == {"accessMode": "public"}
    assert first_result["status"] == "succeeded"
    assert first_result["outputs"] == {"message": "Hello World"}
    assert second_result["outputs"] == {"message": "Hello World"}


def test_site_disable_and_token_reset_revoke_the_old_address(tmp_path: Path) -> None:
    with authenticated_client(tmp_path) as client:
        token = publish_reference(client)
        disabled = client.post(f"{APP_PATH}/site-enable", json={"enable_site": False})
        unavailable = client.get("/api/site", headers=public_headers(token))
        enabled = client.post(f"{APP_PATH}/site-enable", json={"enable_site": True})
        reset = client.post(f"{APP_PATH}/site/access-token/reset")
        old_address = client.get("/api/site", headers=public_headers(token))
        new_token = reset.json()["access_token"]
        new_address = client.get("/api/site", headers=public_headers(new_token))

    assert disabled.status_code == 200
    assert unavailable.status_code == 404
    assert enabled.status_code == 200
    assert reset.status_code == 200
    assert new_token != token
    assert old_address.status_code == 404
    assert new_address.status_code == 200


def test_public_urls_follow_deployment_configuration(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PUBLIC_WEB_URL", "https://workflow.example.test/")
    monkeypatch.setenv("PUBLIC_API_URL", "https://workflow.example.test/api")
    with authenticated_client(tmp_path) as client:
        publish_reference(client)
        app = client.get(APP_PATH).json()
        assert app["site"]["app_base_url"] == "https://workflow.example.test"
        assert app["api_base_url"] == "https://workflow.example.test/api"
