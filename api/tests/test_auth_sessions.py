import base64
import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from http.cookies import SimpleCookie
from pathlib import Path

from fastapi import Request
from fastapi.testclient import TestClient
from test_auth import ADMIN, encoded_password

from api.dependencies.auth import require_principal
from api.main import create_app
from api.security.cookies import CookiePolicy


def auth_app(database_path: Path, secure: bool = False):
    app = create_app(database_url=f"sqlite:///{database_path}")
    if secure:
        app.state.cookies = CookiePolicy.from_urls("https://api.example.com", "https://workflow.example.com")

    @app.get("/_test/principal")
    def protected(request: Request):
        principal = require_principal(request)
        return {"account_id": principal.account_id, "role": principal.role, "session_id": principal.session_id}

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


def test_login_cookies_are_safe_and_only_token_digests_are_stored(tmp_path: Path) -> None:
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
            decoded = base64.urlsafe_b64decode(morsel.value + "=" * (-len(morsel.value) % 4))
            assert len(decoded) == 32
        assert cookies["access_token"]["httponly"]
        assert cookies["refresh_token"]["httponly"]
        assert not cookies["csrf_token"]["httponly"]
        assert cookies["access_token"]["max-age"] == "3600"
        assert cookies["csrf_token"]["max-age"] == "3600"
        assert cookies["refresh_token"]["max-age"] == str(30 * 24 * 60 * 60)

        raw_tokens = {name: cookies[name].value for name in cookies}
        assert len(set(raw_tokens.values())) == 3
        with sqlite3.connect(database_path) as connection:
            stored = connection.execute(
                "SELECT access_token_hash, refresh_token_hash, csrf_token_hash FROM auth_sessions"
            ).fetchone()
        assert stored == tuple(hashlib.sha256(raw_tokens[name].encode()).hexdigest() for name in raw_tokens)
        assert all(len(value) == 64 for value in stored)
        assert not set(stored).intersection(raw_tokens.values())

        protected = client.get("/_test/principal", headers=csrf_headers(client))
        assert protected.status_code == 200
        assert protected.json()["role"] == "owner"


def test_https_host_cookies_and_logout_clearing_use_the_same_policy(tmp_path: Path) -> None:
    database_path = tmp_path / "workflow.db"
    app = auth_app(database_path, secure=True)
    with TestClient(app, base_url="https://testserver") as client:
        response = setup_and_login(client)
        cookies = response_cookies(response)
        assert set(cookies) == {"__Host-access_token", "__Host-refresh_token", "__Host-csrf_token"}
        for morsel in cookies.values():
            assert morsel["secure"]
            assert morsel["path"] == "/"
            assert morsel["domain"] == ""

        old_access = cookies["__Host-access_token"].value
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
        stale.cookies.set("__Host-csrf_token", old_csrf)
        rejected = stale.get(
            "/_test/principal",
            headers={"X-CSRF-Token": old_csrf},
        )
        stale.close()
        assert rejected.status_code == 401
        with sqlite3.connect(database_path) as connection:
            assert connection.execute("SELECT revoked_at IS NOT NULL FROM auth_sessions").fetchone() == (1,)


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
        assert unicode_token.get("/_test/principal", headers={"X-CSRF-Token": "invalid"}).status_code == 401
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


def test_login_throttle_uses_normalized_email_and_success_resets_failures(tmp_path: Path) -> None:
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
        with sqlite3.connect(database_path) as connection:
            assert connection.execute("SELECT COUNT(*) FROM auth_throttles").fetchone() == (0,)

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
