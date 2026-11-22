import asyncio
import io
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from service_helpers import create_test_database
from test_auth import ADMIN, encoded_password

from api import main as member_main
from api.app import create_app
from api.models import engine as engine_module
from api.models.engine import create_session_factory

PASSWORD = "MemberPassword1"


def database_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'workflow.db'}"


def setup_database(tmp_path: Path) -> None:
    with TestClient(create_app(database_url=database_url(tmp_path))) as client:
        assert client.post("/console/api/setup", json=ADMIN).status_code == 201


@pytest.fixture
def create_member(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    async def initialize_schema() -> None:
        schema_engine, _ = await create_test_database(database_url(tmp_path))
        await schema_engine.dispose()

    asyncio.run(initialize_schema())
    engine, session_factory = create_session_factory(database_url(tmp_path))
    monkeypatch.setattr(engine_module, "AsyncSessionLocal", session_factory)

    def invoke(
        email: str = "Member@Example.COM",
        role: str = "editor",
        password: str = PASSWORD,
        *,
        password_flag: bool = False,
    ):
        arguments = [
            "create-member",
            "--email",
            email,
            "--name",
            "Member",
            "--role",
            role,
        ]
        if password_flag:
            arguments.extend(["--password", password])
        else:
            arguments.append("--password-stdin")
            monkeypatch.setattr(sys, "stdin", io.StringIO(password))
        try:
            returncode = member_main(arguments)
        except SystemExit as error:
            returncode = error.code
        captured = capsys.readouterr()
        return SimpleNamespace(returncode=returncode, stdout=captured.out, stderr=captured.err)

    yield invoke
    asyncio.run(engine.dispose())


def test_create_member_reads_stdin_and_creates_active_login_account(tmp_path: Path, create_member) -> None:
    setup_database(tmp_path)
    result = create_member()

    assert result.returncode == 0, result.stderr
    assert "with role editor" in result.stdout
    assert PASSWORD not in result.stdout + result.stderr
    with sqlite3.connect(tmp_path / "workflow.db") as connection:
        account = connection.execute(
            "SELECT id, normalized_email, password_hash, status, initialized_at FROM accounts "
            "WHERE normalized_email = 'member@example.com'"
        ).fetchone()
        assert account is not None
        account_id, _, password_hash, status, initialized_at = account
        assert account_id in result.stdout
        assert password_hash.startswith("$argon2id$")
        assert PASSWORD not in password_hash
        assert status == "active"
        assert initialized_at is not None
        assert connection.execute("SELECT role FROM accounts WHERE id = ?", (account_id,)).fetchone() == ("editor",)

    with TestClient(create_app(database_url=database_url(tmp_path))) as client:
        login = client.post(
            "/console/api/login",
            json={"email": "member@example.com", "password": encoded_password(PASSWORD)},
        )
        assert login.status_code == 200, login.text
        assert login.json() == {"result": "success"}


def test_create_member_rejects_duplicate_email_without_changes(tmp_path: Path, create_member) -> None:
    setup_database(tmp_path)
    assert create_member().returncode == 0
    with sqlite3.connect(tmp_path / "workflow.db") as connection:
        before = connection.execute("SELECT * FROM accounts ORDER BY id").fetchall()

    duplicate = create_member(email="member@example.com")

    assert duplicate.returncode == 1
    assert "An account with this email already exists" in duplicate.stderr
    assert PASSWORD not in duplicate.stdout + duplicate.stderr
    with sqlite3.connect(tmp_path / "workflow.db") as connection:
        assert connection.execute("SELECT * FROM accounts ORDER BY id").fetchall() == before
        assert connection.execute("SELECT COUNT(*) FROM accounts").fetchone() == (2,)


def test_create_member_rejects_owner_and_invalid_password(tmp_path: Path, create_member) -> None:
    setup_database(tmp_path)
    owner = create_member(role="owner")
    assert owner.returncode == 2
    assert "invalid choice: 'owner'" in owner.stderr
    assert PASSWORD not in owner.stdout + owner.stderr

    with sqlite3.connect(tmp_path / "workflow.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM accounts").fetchone() == (1,)
        assert connection.execute("SELECT role FROM accounts").fetchall() == [("owner",)]


@pytest.mark.parametrize("password", ["Short1", "NoNumbersHere", "12345678", "Pass word1"])
def test_create_member_uses_setup_password_rules(tmp_path: Path, create_member, password: str) -> None:
    setup_database(tmp_path)
    result = create_member(password=password)

    assert result.returncode == 1
    assert "Password must have" in result.stderr
    assert password not in result.stdout + result.stderr
    with sqlite3.connect(tmp_path / "workflow.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM accounts").fetchone() == (1,)


def test_create_member_requires_setup_and_never_accepts_password_flag(tmp_path: Path, create_member) -> None:
    before_setup = create_member()
    assert before_setup.returncode == 1
    assert "Setup must be completed first" in before_setup.stderr
    assert PASSWORD not in before_setup.stdout + before_setup.stderr

    setup_database(tmp_path)
    with_password_flag = create_member(password_flag=True)
    assert with_password_flag.returncode == 2
    assert "unrecognized arguments: --password" in with_password_flag.stderr
    with sqlite3.connect(tmp_path / "workflow.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM accounts").fetchone() == (1,)
