import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from http.cookies import SimpleCookie
from pathlib import Path

from fastapi import Depends
from fastapi.testclient import TestClient
from test_auth import ADMIN, encoded_password

from api.app import create_app
from api.configs import app_config
from api.dependencies.auth import require_principal
from api.libs.csrf import generate_csrf_token
from api.libs.passport import PassportService
from api.schemas.auth import Principal


def auth_app(database_path: Path):
    app = create_app(database_url=f"sqlite:///{database_path}")

    @app.get("/_test/principal")
    async def protected(principal: Principal = Depends(require_principal)):
        return {"account_id": principal.account_id, "role": principal.role}

    return app


def setup_and_login(client: TestClient):
    assert client.post("/console/api/setup", json=ADMIN).status_code == 201
    response = client.post(
        "/console/api/login",
        json={"email": ADMIN["email"], "password": encoded_password(ADMIN["password"])},
    )
    assert response.status_code == 200, response.text
    return response


def response_cookies(response) -> dict[str, object]:
    result = {}
    for header in response.headers.get_list("set-cookie"):
        parsed = SimpleCookie()
        parsed.load(header)
        result.update(parsed.items())
    return result


def csrf_headers(client: TestClient, name: str = "csrf_token") -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies.get(name)}


def test_login_cookies_are_safe_and_only_token_digests_are_stored(tmp_path: Path, redis_backend) -> None:
    database_path = tmp_path / "workflow.db"
    app = auth_app(database_path)
    with TestClient(app) as client:
        response = setup_and_login(client)

        assert response.json() == {"result": "success"}
        assert response.headers["cache-control"] == "no-store"
        cookies = response_cookies(response)
        assert set(cookies) == {"access_token", "refresh_token", "csrf_token"}
        for name in ("access_token", "refresh_token", "csrf_token"):
            morsel = cookies[name]
            assert morsel["path"] == "/"
            assert morsel["samesite"].lower() == "lax"
            assert not morsel["secure"]
            assert morsel["domain"] == ""
        assert cookies["access_token"]["httponly"]
        assert cookies["refresh_token"]["httponly"]
        assert not cookies["csrf_token"]["httponly"]
        assert cookies["access_token"]["max-age"] == "3600"
        assert cookies["csrf_token"]["max-age"] == str(30 * 24 * 60 * 60)
        assert cookies["refresh_token"]["max-age"] == str(30 * 24 * 60 * 60)

        raw_tokens = {name: cookies[name].value for name in cookies}
        assert len(set(raw_tokens.values())) == 3
        claims = PassportService().verify(raw_tokens["access_token"])
        assert claims["iss"] == "workflow"
        assert claims["sub"]
        assert claims["jti"]
        assert claims["exp"] > claims["iat"]
        assert len(raw_tokens["refresh_token"]) == 128
        int(raw_tokens["refresh_token"], 16)
        assert raw_tokens["csrf_token"] == generate_csrf_token(raw_tokens["refresh_token"])

        digest = hashlib.sha256(raw_tokens["refresh_token"].encode()).hexdigest()
        assert redis_backend.values[f"refresh_token:{digest}"] == claims["sub"]
        assert redis_backend.sets[f"account_refresh_tokens:{claims['sub']}"] == {digest}
        assert all(raw_tokens["refresh_token"] not in key for key in redis_backend.values)
        assert raw_tokens["refresh_token"] not in redis_backend.values.values()
        with sqlite3.connect(database_path) as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert not {"auth_sessions", "auth_throttles"}.intersection(tables)

        protected = client.get("/_test/principal", headers=csrf_headers(client))
        assert protected.status_code == 200
        assert protected.json()["role"] == "owner"


def test_https_host_cookies_and_logout_clearing_use_the_same_policy(tmp_path: Path, monkeypatch, redis_backend) -> None:
    monkeypatch.setattr(app_config, "PUBLIC_API_URL", "https://api.example.com")
    monkeypatch.setattr(app_config, "PUBLIC_WEB_URL", "https://workflow.example.com")
    app = auth_app(tmp_path / "workflow.db")
    with TestClient(app, base_url="https://testserver") as client:
        response = setup_and_login(client)
        cookies = response_cookies(response)
        assert set(cookies) == {"__Host-access_token", "__Host-refresh_token", "__Host-csrf_token"}
        for morsel in cookies.values():
            assert morsel["secure"]
            assert morsel["path"] == "/"
            assert morsel["domain"] == ""

        old_access = cookies["__Host-access_token"].value
        old_refresh = cookies["__Host-refresh_token"].value
        old_csrf = cookies["__Host-csrf_token"].value
        logged_out = client.post(
            "/console/api/logout",
            headers=csrf_headers(client, "__Host-csrf_token"),
        )
        assert logged_out.status_code == 200
        cleared = response_cookies(logged_out)
        assert set(cleared) == set(cookies)
        for name, morsel in cleared.items():
            assert morsel.value == ""
            assert morsel["max-age"] == "0"
            assert morsel["secure"]
            assert morsel["path"] == "/"
            assert morsel["domain"] == ""
            assert bool(morsel["httponly"]) is (name != "__Host-csrf_token")

        stale = TestClient(app, base_url="https://testserver")
        stale.cookies.set("__Host-access_token", old_access)
        stale.cookies.set("__Host-refresh_token", old_refresh)
        stale.cookies.set("__Host-csrf_token", old_csrf)
        rejected = stale.get(
            "/_test/principal",
            headers={"X-CSRF-Token": old_csrf},
        )
        reused_refresh = stale.post("/console/api/refresh-token")
        stale.close()
        assert rejected.status_code == 401
        assert reused_refresh.status_code == 401
        claims = PassportService().verify(old_access)
        assert f"access_token_denylist:{claims['jti']}" in redis_backend.values
        digest = hashlib.sha256(old_refresh.encode()).hexdigest()
        assert f"refresh_token:{digest}" not in redis_backend.values


def test_concurrent_refresh_has_one_winner_and_failed_refresh_does_not_clear(tmp_path: Path) -> None:
    app = auth_app(tmp_path / "workflow.db")
    with TestClient(app) as original:
        setup_and_login(original)
        old_refresh = original.cookies.get("refresh_token")
        old_access = original.cookies.get("access_token")

        clients = [TestClient(app), TestClient(app)]
        for client in clients:
            client.cookies.set("refresh_token", old_refresh)

        def refresh(client: TestClient):
            return client.post("/console/api/refresh-token")

        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(refresh, clients))
        for client in clients:
            client.close()

        assert sorted(response.status_code for response in responses) == [200, 401]
        winner = next(response for response in responses if response.status_code == 200)
        loser = next(response for response in responses if response.status_code == 401)
        assert len(winner.headers.get_list("set-cookie")) == 3
        assert loser.headers.get_list("set-cookie") == []
        assert winner.json() == {"result": "success"}

        refreshed = TestClient(app)
        for name in ("access_token", "refresh_token", "csrf_token"):
            refreshed.cookies.set(name, winner.cookies.get(name))
        assert refreshed.get("/_test/principal", headers=csrf_headers(refreshed)).status_code == 200
        assert refreshed.post("/console/api/refresh-token").status_code == 200
        refreshed.close()

        assert original.get("/_test/principal", headers=csrf_headers(original)).status_code == 401
        assert original.post("/console/api/refresh-token").status_code == 401
        assert old_access != winner.cookies.get("access_token")


def test_require_principal_distinguishes_access_and_csrf_failures(tmp_path: Path) -> None:
    app = auth_app(tmp_path / "workflow.db")
    with TestClient(app) as client:
        assert client.get("/_test/principal").status_code == 401
        setup_and_login(client)
        assert client.get("/_test/principal").status_code == 403
        assert client.get("/_test/principal", headers={"X-CSRF-Token": "wrong"}).status_code == 403
        assert client.get("/_test/principal", headers=[(b"X-CSRF-Token", "無効".encode())]).status_code == 403
        assert client.get("/_test/principal", headers=csrf_headers(client)).status_code == 200

        missing_cookie = TestClient(app)
        missing_cookie.cookies.set("access_token", client.cookies.get("access_token"))
        csrf = client.cookies.get("csrf_token")
        assert missing_cookie.get("/_test/principal", headers={"X-CSRF-Token": csrf}).status_code == 403
        missing_cookie.close()

        unicode_token = TestClient(app)
        unicode_token.cookies.set("access_token", "not-a-valid-token")
        unicode_token.cookies.set("refresh_token", client.cookies.get("refresh_token"))
        unicode_token.cookies.set("csrf_token", csrf)
        assert unicode_token.get("/_test/principal", headers={"X-CSRF-Token": csrf}).status_code == 401
        unicode_token.close()


def test_disabled_account_invalidates_active_session(tmp_path: Path) -> None:
    database_path = tmp_path / "workflow.db"
    app = auth_app(database_path)
    with TestClient(app) as client:
        setup_and_login(client)
        with sqlite3.connect(database_path) as connection:
            connection.execute("UPDATE accounts SET status = 'disabled'")
        assert client.get("/_test/principal", headers=csrf_headers(client)).status_code == 401
        assert client.post("/console/api/refresh-token").status_code == 401

        with sqlite3.connect(database_path) as connection:
            connection.execute("UPDATE accounts SET status = 'active'")
        assert client.get("/_test/principal", headers=csrf_headers(client)).status_code == 200


def test_login_throttle_uses_normalized_email_and_success_resets_failures(tmp_path: Path, redis_backend) -> None:
    database_path = tmp_path / "workflow.db"
    app = auth_app(database_path)
    with TestClient(app) as client:
        assert client.post("/console/api/setup", json=ADMIN).status_code == 201
        wrong_password = encoded_password("WrongPassword1")
        for _ in range(4):
            response = client.post(
                "/console/api/login",
                json={"email": "Admin@Example.COM", "password": wrong_password},
            )
            assert response.status_code == 401
        successful = client.post(
            "/console/api/login",
            json={"email": ADMIN["email"], "password": encoded_password(ADMIN["password"])},
        )
        assert successful.status_code == 200
        assert not any(key.startswith("login_email_errors:") for key in redis_backend.values)
        assert not any(key.startswith("login_ip_errors:") for key in redis_backend.values)

        first_failure = client.post(
            "/console/api/login",
            json={"email": ADMIN["email"], "password": wrong_password},
        )
        assert first_failure.status_code == 401

    with TestClient(app) as throttled:
        for email in (ADMIN["email"], "Admin@Example.COM", "ADMIN@example.com", ADMIN["email"]):
            response = throttled.post(
                "/console/api/login",
                json={"email": email, "password": wrong_password},
            )
            assert response.status_code == 401
        locked = throttled.post(
            "/console/api/login",
            json={"email": "admin@EXAMPLE.com", "password": encoded_password(ADMIN["password"])},
        )
        assert locked.status_code == 429
        assert locked.json()["code"] == "email_password_login_limit"
        assert locked.headers.get_list("set-cookie") == []


def test_unknown_email_and_wrong_password_have_the_same_response(tmp_path: Path) -> None:
    app = auth_app(tmp_path / "workflow.db")
    with TestClient(app) as client:
        assert client.post("/console/api/setup", json=ADMIN).status_code == 201
        wrong_password = encoded_password("WrongPassword1")
        unknown = client.post(
            "/console/api/login",
            json={"email": "unknown@example.com", "password": wrong_password},
        )
        wrong = client.post(
            "/console/api/login",
            json={"email": ADMIN["email"], "password": wrong_password},
        )

    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json()
    assert unknown.headers.get_list("set-cookie") == wrong.headers.get_list("set-cookie") == []
