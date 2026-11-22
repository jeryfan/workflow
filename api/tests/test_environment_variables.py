import json
from pathlib import Path

from auth_helpers import authenticated_client
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads((ROOT / "contracts/fixtures/reference-workflow.json").read_text())
APP_ID = FIXTURE["app_id"]
DRAFT_URL = f"/console/api/apps/{APP_ID}/workflows/draft"
ENVIRONMENT_URL = f"{DRAFT_URL}/environment-variables"


def save_reference(client: TestClient) -> str:
    response = client.post(
        DRAFT_URL,
        json={
            "graph": FIXTURE["graph"],
            "features": {},
            "conversation_variables": [],
            "hash": None,
        },
    )
    assert response.status_code == 200
    return response.json()["hash"]


def variable(variable_id: str, name: str, value: str) -> dict:
    return {
        "id": variable_id,
        "name": name,
        "value_type": "string",
        "value": value,
        "description": "",
    }


def test_environment_variable_replacement_preserves_the_graph_and_hash(tmp_path: Path) -> None:
    with authenticated_client(tmp_path) as client:
        original_hash = save_reference(client)
        updated = client.post(
            ENVIRONMENT_URL,
            json={
                "environment_variables": [variable("env-1", "GREETING", "Hello")],
                "patch": False,
            },
        )
        read = client.get(ENVIRONMENT_URL)
        draft = client.get(DRAFT_URL)

    assert updated.status_code == 200
    assert updated.json() == {"result": "success"}
    assert read.json() == {
        "items": [
            {
                **variable("env-1", "GREETING", "Hello"),
                "type": "env",
                "selector": ["env", "GREETING"],
                "edited": False,
                "visible": True,
                "editable": True,
            }
        ]
    }
    assert draft.json()["environment_variables"] == [variable("env-1", "GREETING", "Hello")]
    assert draft.json()["graph"] == FIXTURE["graph"]
    assert draft.json()["hash"] == original_hash


def test_environment_patch_updates_by_id_appends_and_deletes_without_reordering(tmp_path: Path) -> None:
    with authenticated_client(tmp_path) as client:
        save_reference(client)
        client.post(
            ENVIRONMENT_URL,
            json={
                "environment_variables": [
                    variable("env-1", "FIRST", "one"),
                    variable("env-2", "SECOND", "two"),
                ],
                "patch": False,
            },
        )
        patched = client.post(
            ENVIRONMENT_URL,
            json={
                "environment_variables": [
                    variable("env-2", "SECOND", "changed"),
                    variable("env-3", "THIRD", "three"),
                ],
                "deleted_environment_variable_ids": ["env-1"],
                "patch": True,
            },
        )
        read = client.get(ENVIRONMENT_URL)

    assert patched.status_code == 200
    items = read.json()["items"]
    assert [item["id"] for item in items] == ["env-2", "env-3"]
    assert [item["value"] for item in items] == ["changed", "three"]


def test_normal_draft_save_applies_environment_patch_without_clearing_server_values(tmp_path: Path) -> None:
    with authenticated_client(tmp_path) as client:
        original_hash = save_reference(client)
        client.post(
            ENVIRONMENT_URL,
            json={
                "environment_variables": [
                    variable("env-1", "FIRST", "one"),
                    variable("env-2", "SECOND", "two"),
                ],
                "patch": False,
            },
        )
        saved = client.post(
            DRAFT_URL,
            json={
                "graph": FIXTURE["graph"],
                "features": {},
                "conversation_variables": [],
                "hash": original_hash,
                "environment_variable_patch": {
                    "environment_variables": [variable("env-2", "SECOND", "changed")],
                    "deleted_environment_variable_ids": ["env-1"],
                },
            },
        )
        read = client.get(ENVIRONMENT_URL)

    assert saved.status_code == 200
    assert saved.json()["hash"] == original_hash
    assert [item["id"] for item in read.json()["items"]] == ["env-2"]
    assert read.json()["items"][0]["value"] == "changed"


def test_invalid_environment_patches_fail_without_changing_saved_values(tmp_path: Path) -> None:
    with authenticated_client(tmp_path) as client:
        save_reference(client)
        client.post(
            ENVIRONMENT_URL,
            json={"environment_variables": [variable("env-1", "FIRST", "one")], "patch": False},
        )
        invalid_payloads = [
            {
                "environment_variables": [variable("", "RENAMED", "changed")],
                "patch": True,
            },
            {
                "environment_variables": [
                    variable("env-1", "FIRST", "changed"),
                    variable("env-1", "DUPLICATE", "two"),
                ],
                "patch": True,
            },
            {
                "environment_variables": [variable("env-1", "FIRST", "changed")],
                "deleted_environment_variable_ids": ["env-1"],
                "patch": True,
            },
            {
                "environment_variables": [
                    variable("env-2", "FIRST", "two"),
                    variable("env-3", "FIRST", "three"),
                ],
                "patch": False,
            },
        ]
        for payload in invalid_payloads:
            response = client.post(ENVIRONMENT_URL, json=payload)
            assert response.status_code == 400, payload
        read = client.get(ENVIRONMENT_URL)

    assert [item["id"] for item in read.json()["items"]] == ["env-1"]
    assert read.json()["items"][0]["value"] == "one"
