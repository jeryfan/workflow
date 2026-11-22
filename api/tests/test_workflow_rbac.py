import json
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from test_auth import ADMIN, encoded_password

from api.main import DEFAULT_APP_ID, create_app

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads((ROOT / "contracts/fixtures/reference-workflow.json").read_text())
MEMBER = {
    "email": "member@example.com",
    "name": "Member",
    "password": "MemberPassword1",
}


def setup_application(tmp_path: Path):
    app = create_app(database_url=f"sqlite:///{tmp_path / 'workflow.db'}")
    with TestClient(app) as client:
        assert client.post("/console/api/setup", json=ADMIN).status_code == 201
        logged_in = client.post(
            "/console/api/login",
            json={"email": ADMIN["email"], "password": encoded_password(ADMIN["password"])},
        )
        assert logged_in.status_code == 200, logged_in.text
        saved = client.post(
            f"/console/api/apps/{DEFAULT_APP_ID}/workflows/draft",
            headers=csrf(client),
            json={
                "graph": FIXTURE["graph"],
                "features": {},
                "conversation_variables": [],
                "hash": None,
            },
        )
        assert saved.status_code == 200
    return app


def csrf(client: TestClient) -> dict[str, str]:
    token = client.cookies.get("csrf_token")
    return {"X-CSRF-Token": token} if token else {}


def create_member(tmp_path: Path, role: str) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "api.cli",
            "create-member",
            "--database-url",
            f"sqlite:///{tmp_path / 'workflow.db'}",
            "--email",
            MEMBER["email"],
            "--name",
            MEMBER["name"],
            "--password-stdin",
            "--role",
            role,
        ],
        cwd=ROOT / "api",
        input=MEMBER["password"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def login(app, email: str, password: str) -> TestClient:
    client = TestClient(app)
    response = client.post(
        "/console/api/login",
        json={"email": email, "password": encoded_password(password)},
    )
    assert response.status_code == 200, response.text
    return client


def test_editor_role_inherits_dify_workflow_permissions(tmp_path: Path) -> None:
    app = setup_application(tmp_path)
    create_member(tmp_path, "editor")
    client = login(app, MEMBER["email"], MEMBER["password"])

    profile = client.get("/console/api/account/profile", headers=csrf(client))
    permissions = client.get("/console/api/account/permissions", headers=csrf(client))
    draft = client.get(f"/console/api/apps/{DEFAULT_APP_ID}/workflows/draft", headers=csrf(client))
    client.close()

    assert profile.json()["role"] == "editor"
    assert "app.acl.edit" in permissions.json()["app"]["default_permission_keys"]
    assert draft.status_code == 200


def test_normal_role_can_read_and_run_but_cannot_manage_workflows(tmp_path: Path) -> None:
    app = setup_application(tmp_path)
    create_member(tmp_path, "normal")
    client = login(app, MEMBER["email"], MEMBER["password"])
    headers = csrf(client)
    responses = [
        client.get(f"/console/api/apps/{DEFAULT_APP_ID}/workflows/draft", headers=headers),
        client.post(
            f"/console/api/apps/{DEFAULT_APP_ID}/workflows/draft",
            headers=headers,
            json={"graph": FIXTURE["graph"], "features": {}, "hash": None},
        ),
        client.post(
            f"/console/api/apps/{DEFAULT_APP_ID}/workflows/draft/run",
            headers=headers,
            json={"inputs": FIXTURE["inputs"], "files": []},
        ),
        client.post(
            f"/console/api/apps/{DEFAULT_APP_ID}/workflow-runs/tasks/any-task/stop",
            headers=headers,
        ),
        client.post(
            f"/console/api/apps/{DEFAULT_APP_ID}/workflows/publish",
            headers=headers,
            json={},
        ),
    ]
    client.close()

    assert [response.status_code for response in responses] == [200, 403, 200, 404, 403]


def test_normal_role_can_run_without_editing(tmp_path: Path) -> None:
    app = setup_application(tmp_path)
    create_member(tmp_path, "normal")
    client = login(app, MEMBER["email"], MEMBER["password"])
    headers = csrf(client)

    app_detail = client.get(f"/console/api/apps/{DEFAULT_APP_ID}", headers=headers)
    draft = client.get(f"/console/api/apps/{DEFAULT_APP_ID}/workflows/draft", headers=headers)
    save = client.post(
        f"/console/api/apps/{DEFAULT_APP_ID}/workflows/draft",
        headers=headers,
        json={"graph": FIXTURE["graph"], "features": {}, "hash": draft.json().get("hash")},
    )
    with client.stream(
        "POST",
        f"/console/api/apps/{DEFAULT_APP_ID}/workflows/draft/run",
        headers=headers,
        json={"inputs": FIXTURE["inputs"], "files": []},
    ) as response:
        run_status = response.status_code
        events = [line for line in response.iter_lines() if line.startswith("data: ")]
    client.close()

    assert set(app_detail.json()["permission_keys"]) == {
        "app.acl.view_layout",
        "app.acl.test_and_run",
        "app.acl.access_point_view",
    }
    assert draft.status_code == 200
    assert save.status_code == 403
    assert run_status == 200
    assert events
