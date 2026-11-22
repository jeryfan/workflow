import base64
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from api.app import create_app

ADMIN = {
    "email": "admin@example.com",
    "name": "Admin",
    "password": "Password123",
    "language": "en-US",
}


def csrf_headers(client: TestClient) -> dict[str, str]:
    token = client.cookies.get("csrf_token")
    return {"X-CSRF-Token": token} if token else {}


def encoded_password(value: str) -> str:
    return base64.b64encode(value.encode()).decode()


def test_first_admin_setup_login_and_account_bootstrap(tmp_path: Path) -> None:
    with TestClient(create_app(database_url=f"sqlite:///{tmp_path / 'workflow.db'}")) as client:
        before = client.get("/console/api/setup")
        assert before.status_code == 200
        assert before.json()["step"] == "not_started"

        created = client.post("/console/api/setup", json=ADMIN)
        assert created.status_code == 201, created.text
        assert created.json() == {"result": "success"}
        assert client.get("/console/api/setup").json()["step"] == "finished"

        duplicate = client.post("/console/api/setup", json=ADMIN)
        assert duplicate.status_code == 403
        assert duplicate.json()["code"] == "already_setup"

        logged_in = client.post(
            "/console/api/login",
            json={"email": ADMIN["email"], "password": encoded_password(ADMIN["password"]), "remember_me": True},
        )
        assert logged_in.status_code == 200, logged_in.text
        assert logged_in.json() == {"result": "success"}
        for name in ("access_token", "refresh_token", "csrf_token"):
            assert client.cookies.get(name)

        profile = client.get("/console/api/account/profile", headers=csrf_headers(client))
        permissions = client.get(
            "/console/api/account/permissions",
            headers=csrf_headers(client),
        )

    assert profile.json()["email"] == ADMIN["email"]
    assert profile.json()["name"] == ADMIN["name"]
    assert profile.json()["is_password_set"] is True
    assert profile.json()["role"] == "owner"
    assert "app.create_and_management" in permissions.json()["account"]["permission_keys"]
    assert permissions.json()["app"]["default_permission_keys"]


def test_login_logout_and_csrf_are_enforced(tmp_path: Path) -> None:
    with TestClient(create_app(database_url=f"sqlite:///{tmp_path / 'workflow.db'}")) as client:
        client.post("/console/api/setup", json=ADMIN)
        wrong = client.post(
            "/console/api/login",
            json={"email": ADMIN["email"], "password": "WrongPassword1"},
        )
        assert wrong.status_code == 401
        assert wrong.json()["code"] == "authentication_failed"

        logged_in = client.post(
            "/console/api/login",
            json={"email": ADMIN["email"], "password": encoded_password(ADMIN["password"])},
        )
        assert logged_in.status_code == 200
        without_csrf = client.post(
            "/console/api/logout",
        )
        assert without_csrf.status_code == 403
        logged_out = client.post("/console/api/logout", headers=csrf_headers(client))
        assert logged_out.status_code == 200
        assert client.get("/console/api/account/profile").status_code == 401


def test_refresh_rotates_tokens_and_rejects_reuse(tmp_path: Path) -> None:
    app = create_app(database_url=f"sqlite:///{tmp_path / 'workflow.db'}")
    with TestClient(app) as client:
        client.post("/console/api/setup", json=ADMIN)
        login = client.post(
            "/console/api/login",
            json={"email": ADMIN["email"], "password": encoded_password(ADMIN["password"])},
        )
        assert login.status_code == 200
        old_refresh = client.cookies.get("refresh_token")
        old_access = client.cookies.get("access_token")
        refreshed = client.post("/console/api/refresh-token")
        assert refreshed.status_code == 200
        assert client.cookies.get("refresh_token") != old_refresh
        assert client.cookies.get("access_token") != old_access
        profile = client.get("/console/api/account/profile", headers=csrf_headers(client))
        assert profile.status_code == 200

        with TestClient(app) as stale:
            stale.cookies.set("refresh_token", old_refresh)
            reused = stale.post("/console/api/refresh-token")
            assert reused.status_code == 401


def test_persistent_sessions_survive_application_restart(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'workflow.db'}"
    with TestClient(create_app(database_url=database_url)) as client:
        client.post("/console/api/setup", json=ADMIN)
        client.post(
            "/console/api/login",
            json={"email": ADMIN["email"], "password": encoded_password(ADMIN["password"])},
        )
        cookies = {name: client.cookies.get(name) for name in ("access_token", "refresh_token", "csrf_token")}

    with TestClient(create_app(database_url=database_url)) as restarted:
        for name, value in cookies.items():
            restarted.cookies.set(name, value)
        profile = restarted.get("/console/api/account/profile", headers=csrf_headers(restarted))

    assert profile.status_code == 200
    assert profile.json()["email"] == ADMIN["email"]


def test_concurrent_setup_creates_only_one_admin(tmp_path: Path) -> None:
    app = create_app(database_url=f"sqlite:///{tmp_path / 'workflow.db'}")

    with TestClient(app) as client:
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(lambda _: client.post("/console/api/setup", json=ADMIN), range(2)))

        assert sorted(response.status_code for response in responses) == [201, 403]
        assert [response.json().get("code") for response in responses if response.status_code == 403] == [
            "already_setup"
        ]
        assert client.get("/console/api/setup").json()["step"] == "finished"
        login = client.post(
            "/console/api/login",
            json={"email": ADMIN["email"], "password": encoded_password(ADMIN["password"])},
        )
        assert login.status_code == 200
        members = client.get("/console/api/accounts", headers=csrf_headers(client))
        assert members.status_code == 200
        assert len(members.json()["accounts"]) == 1
