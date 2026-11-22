import json
from pathlib import Path

import pytest

from api.db.store import Store
from api.models.entities import Actor, DraftConflict, GraphDocument, SaveDraft

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads((ROOT / "contracts/fixtures/reference-workflow.json").read_text())
APP_ID = FIXTURE["app_id"]
ACTOR = Actor(id="local-user", name="Local User", email="local@example.invalid")


def save_command(
    graph: dict | None = None,
    expected_hash: str | None = None,
    app_id: str = APP_ID,
) -> SaveDraft:
    return SaveDraft(
        app_id=app_id,
        graph=GraphDocument.parse(graph or FIXTURE["graph"]),
        features={},
        conversation_variables=(),
        expected_hash=expected_hash,
        force=False,
        actor=ACTOR,
    )


def test_draft_survives_reopen_and_stale_hash_cannot_replace_it(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'workflow.db'}"
    store = Store(database_url)
    store.ensure_app(APP_ID)
    assert store.get_draft(APP_ID) is None

    saved = store.save_draft(save_command())
    store.close()
    reopened = Store(database_url)

    loaded = reopened.get_draft(APP_ID)
    assert loaded is not None
    assert loaded.graph.to_json_object() == FIXTURE["graph"]
    assert loaded.hash == saved.hash
    assert loaded.updated_by == ACTOR

    changed_graph = deepcopy_graph()
    changed_graph["nodes"][1]["data"]["template"] = "Changed {{ name }}"
    with pytest.raises(DraftConflict):
        reopened.save_draft(save_command(changed_graph, expected_hash="stale"))

    unchanged = reopened.get_draft(APP_ID)
    assert unchanged is not None
    assert unchanged.graph.to_json_object() == FIXTURE["graph"]
    reopened.close()


def test_identical_graphs_can_be_saved_for_two_apps(tmp_path: Path) -> None:
    store = Store(f"sqlite:///{tmp_path / 'workflow.db'}")
    other_app_id = "00000000-0000-0000-0000-000000000002"
    store.ensure_app(APP_ID)
    store.ensure_app(other_app_id)

    first = store.save_draft(save_command())
    second = store.save_draft(save_command(app_id=other_app_id))

    assert first.hash == second.hash
    assert store.get_draft(APP_ID) is not None
    store.close()


def deepcopy_graph() -> dict:
    return json.loads(json.dumps(FIXTURE["graph"]))
