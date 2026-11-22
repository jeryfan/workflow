import asyncio
import json
from pathlib import Path

import pytest
from service_helpers import create_test_database

from api.models.entities import Actor, DraftConflict, GraphDocument, SaveDraft
from api.services.workflows import DraftNotFound, WorkflowService

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

    async def exercise() -> None:
        engine, session_factory = await create_test_database(database_url, (APP_ID,))
        try:
            async with session_factory() as db:
                workflows = WorkflowService(db)
                with pytest.raises(DraftNotFound):
                    await workflows.get_draft(APP_ID)
                saved = await workflows.save_draft(save_command())
        finally:
            await engine.dispose()

        engine, session_factory = await create_test_database(database_url)
        try:
            async with session_factory() as db:
                reopened = WorkflowService(db)
                loaded = await reopened.get_draft(APP_ID)
                assert loaded.graph.to_json_object() == FIXTURE["graph"]
                assert loaded.hash == saved.hash
                assert loaded.updated_by == ACTOR

                changed_graph = deepcopy_graph()
                changed_graph["nodes"][1]["data"]["template"] = "Changed {{ name }}"
                with pytest.raises(DraftConflict):
                    await reopened.save_draft(save_command(changed_graph, expected_hash="stale"))

                unchanged = await reopened.get_draft(APP_ID)
                assert unchanged.graph.to_json_object() == FIXTURE["graph"]
        finally:
            await engine.dispose()

    asyncio.run(exercise())


def test_identical_graphs_can_be_saved_for_two_apps(tmp_path: Path) -> None:
    other_app_id = "00000000-0000-0000-0000-000000000002"

    async def exercise() -> None:
        engine, session_factory = await create_test_database(
            f"sqlite:///{tmp_path / 'workflow.db'}", (APP_ID, other_app_id)
        )
        try:
            async with session_factory() as db:
                workflows = WorkflowService(db)
                first = await workflows.save_draft(save_command())
                second = await workflows.save_draft(save_command(app_id=other_app_id))

                assert first.hash == second.hash
                assert await workflows.get_draft(APP_ID) is not None
        finally:
            await engine.dispose()

    asyncio.run(exercise())


def deepcopy_graph() -> dict:
    return json.loads(json.dumps(FIXTURE["graph"]))
