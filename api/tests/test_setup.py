import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.db.store import Store
from api.main import DEFAULT_APP_ID, create_app

ADMIN = {
    "email": "Admin@Example.COM",
    "name": "Admin",
    "password": "Password123",
    "language": "en-US",
}


def identity_counts(database_path: Path) -> dict[str, int]:
    with sqlite3.connect(database_path) as connection:
        return {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("accounts", "installation_state")
        }


def test_setup_creates_owner_and_atomically_adopts_existing_apps(tmp_path: Path) -> None:
    database_path = tmp_path / "workflow.db"
    database_url = f"sqlite:///{database_path}"
    store = Store(database_url)
    store.ensure_app(DEFAULT_APP_ID)
    store.ensure_app("second-app")
    store.close()

    with TestClient(create_app(database_url=database_url)) as client:
        before = client.get("/console/api/setup")
        assert before.status_code == 200
        assert before.json() == {"step": "not_started"}
        assert client.get("/console/api/init").json() == {"status": "finished"}

        response = client.post("/console/api/setup", json=ADMIN)

        assert response.status_code == 201, response.text
        assert response.json() == {"result": "success"}
        assert not response.headers.get("set-cookie")
        assert client.get("/console/api/setup").json()["step"] == "finished"

        duplicate = client.post("/console/api/setup", json=ADMIN)
        assert duplicate.status_code == 403
        assert duplicate.json()["code"] == "already_setup"

    with sqlite3.connect(database_path) as connection:
        account = connection.execute(
            "SELECT id, email, normalized_email, name, password_hash, role, status, interface_language, "
            "initialized_at FROM accounts"
        ).fetchone()
        assert account is not None
        account_id, email, normalized_email, name, password_hash, role, status, language, initialized_at = account
        assert email == "Admin@example.com"
        assert normalized_email == "admin@example.com"
        assert (name, role, status, language) == ("Admin", "owner", "active", "en-US")
        assert initialized_at is not None
        assert password_hash.startswith("$argon2id$")
        assert ADMIN["password"] not in password_hash
        assert connection.execute("SELECT setup_at FROM installation_state").fetchone() is not None
        assert connection.execute(
            "SELECT maintainer_id, created_by_id, updated_by_id FROM apps ORDER BY id"
        ).fetchall() == [(account_id, account_id, account_id), (account_id, account_id, account_id)]
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert not {"workspaces", "workspace_memberships", "workflow_acl", "workflow_access_settings"}.intersection(
            tables
        )

    assert identity_counts(database_path) == {"accounts": 1, "installation_state": 1}


@pytest.mark.parametrize(
    "payload",
    [
        {**ADMIN, "password": "Short1"},
        {**ADMIN, "password": "NoNumbersHere"},
        {**ADMIN, "password": "12345678"},
        {**ADMIN, "password": "Pass word1"},
        {**ADMIN, "password": "Password123\t"},
        {**ADMIN, "name": "x" * 31},
        {**ADMIN, "email": "not-an-email"},
    ],
)
def test_setup_rejects_invalid_owner_input_without_creating_state(tmp_path: Path, payload: dict) -> None:
    database_path = tmp_path / "workflow.db"
    with TestClient(create_app(database_url=f"sqlite:///{database_path}")) as client:
        response = client.post("/console/api/setup", json=payload)

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_argument"
    assert identity_counts(database_path) == {"accounts": 0, "installation_state": 0}


def test_failed_setup_transaction_rolls_back_every_write(tmp_path: Path) -> None:
    database_path = tmp_path / "workflow.db"
    app = create_app(database_url=f"sqlite:///{database_path}")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TRIGGER reject_account BEFORE INSERT ON accounts "
            "BEGIN SELECT RAISE(ABORT, 'injected account failure'); END"
        )

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/console/api/setup", json=ADMIN)

    assert response.status_code == 500
    assert identity_counts(database_path) == {"accounts": 0, "installation_state": 0}
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT maintainer_id, created_by_id, updated_by_id FROM apps").fetchone() == (
            None,
            None,
            None,
        )


def test_concurrent_setup_has_one_winner_and_one_already_setup_response(tmp_path: Path) -> None:
    database_path = tmp_path / "workflow.db"
    app = create_app(database_url=f"sqlite:///{database_path}")

    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _: client.post("/console/api/setup", json=ADMIN), range(2)))

    assert sorted(response.status_code for response in responses) == [201, 403]
    assert [response.json().get("code") for response in responses if response.status_code == 403] == ["already_setup"]
    assert identity_counts(database_path) == {"accounts": 1, "installation_state": 1}
