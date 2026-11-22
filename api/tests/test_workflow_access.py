import sqlite3
from pathlib import Path

from test_workflow_rbac import MEMBER, create_member, csrf, login, setup_application

from api.app import DEFAULT_APP_ID


def owner(app):
    return login(app, "admin@example.com", "Password123")


def test_legacy_workspace_access_routes_are_removed(tmp_path: Path) -> None:
    app = setup_application(tmp_path)
    with owner(app) as client:
        for path in (
            "/console/api/workspaces/current/summary",
            "/console/api/workspaces/current/rbac/my-permissions",
            f"/console/api/workspaces/current/rbac/apps/{DEFAULT_APP_ID}/access-policy",
            f"/console/api/workspaces/current/rbac/apps/{DEFAULT_APP_ID}/whitelist",
        ):
            response = client.get(path, headers=csrf(client))
            assert response.status_code == 404, path


def test_account_role_changes_apply_to_an_existing_session(tmp_path: Path) -> None:
    database_path = tmp_path / "workflow.db"
    app = setup_application(tmp_path)
    create_member(tmp_path, "normal")
    member = login(app, MEMBER["email"], MEMBER["password"])
    save_payload = {"graph": {}, "features": {}, "hash": None, "force": True}
    assert (
        member.post(
            f"/console/api/apps/{DEFAULT_APP_ID}/workflows/draft",
            headers=csrf(member),
            json=save_payload,
        ).status_code
        == 403
    )

    with sqlite3.connect(database_path) as connection:
        connection.execute("UPDATE accounts SET role = 'editor' WHERE email = ?", (MEMBER["email"],))
    current = member.get(f"/console/api/apps/{DEFAULT_APP_ID}/workflows/draft", headers=csrf(member))
    save_payload["hash"] = current.json()["hash"]
    changed = member.post(
        f"/console/api/apps/{DEFAULT_APP_ID}/workflows/draft",
        headers=csrf(member),
        json=save_payload,
    )
    member.close()
    assert changed.status_code == 200, changed.text


def test_missing_app_is_not_created_by_a_read(tmp_path: Path) -> None:
    app = setup_application(tmp_path)
    with owner(app) as client:
        response = client.get("/console/api/apps/00000000-0000-0000-0000-000000000099", headers=csrf(client))
    assert response.status_code == 404
    with sqlite3.connect(tmp_path / "workflow.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM apps").fetchone()[0] == 1
