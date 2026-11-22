import asyncio
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from service_helpers import create_test_database

from api.models.entities import (
    Actor,
    GraphDocument,
    NodeFinished,
    NodeStarted,
    RunIdentity,
    RunSnapshot,
    RunSucceeded,
    TerminalStateConflict,
    WorkflowFinished,
    WorkflowStarted,
)
from api.services.runs import RunHistoryService

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads((ROOT / "contracts/fixtures/reference-workflow.json").read_text())
APP_ID = FIXTURE["app_id"]
NOW = datetime(2026, 1, 1, tzinfo=UTC)
ACTOR = Actor(id="local-user", name="Local User", email="local@example.invalid")


def make_snapshot() -> RunSnapshot:
    graph = GraphDocument.parse(FIXTURE["graph"])
    return RunSnapshot(
        identity=RunIdentity(
            app_id=APP_ID,
            workflow_id="workflow-1",
            workflow_run_id="run-1",
            task_id="task-1",
        ),
        graph=graph,
        draft_hash="hash-1",
        inputs=FIXTURE["inputs"],
        actor=ACTOR,
        created_at=NOW,
    )


def test_run_events_are_idempotent_and_survive_reopen(tmp_path: Path) -> None:
    database_path = tmp_path / "workflow.db"
    database_url = f"sqlite:///{database_path}"
    snapshot = make_snapshot()
    identity = snapshot.identity
    events = [
        WorkflowStarted(identity=identity, sequence=0, inputs={"name": "World"}, created_at=NOW),
        NodeStarted(
            identity=identity,
            sequence=1,
            execution_id="execution-1",
            node_id="start",
            node_type="start",
            title="Start",
            index=1,
            predecessor_node_id=None,
            created_at=NOW,
        ),
        NodeFinished(
            identity=identity,
            sequence=2,
            execution_id="execution-1",
            node_id="start",
            node_type="start",
            title="Start",
            index=1,
            predecessor_node_id=None,
            inputs={"name": "World"},
            process_data={},
            outputs={"name": "World"},
            status="succeeded",
            error=None,
            elapsed_time=0.01,
            created_at=NOW,
            finished_at=NOW,
        ),
        WorkflowFinished(
            identity=identity,
            sequence=3,
            outcome=RunSucceeded(outputs={"message": "Hello World"}, exceptions_count=0),
            elapsed_time=0.02,
            total_tokens=0,
            total_steps=1,
            created_at=NOW,
            finished_at=NOW,
        ),
    ]

    async def exercise() -> None:
        engine, session_factory = await create_test_database(database_url, (APP_ID,))
        try:
            async with session_factory() as db:
                history = RunHistoryService(db)
                await history.create(snapshot)
                for event in [*events, *events]:
                    await history.project(event)
        finally:
            await engine.dispose()

        with sqlite3.connect(database_path) as connection:
            run = connection.execute("SELECT status, outputs_json, total_steps FROM workflow_runs").fetchone()
            node_count = connection.execute("SELECT COUNT(*) FROM node_executions").fetchone()[0]
        assert run == ("succeeded", '{"message": "Hello World"}', 1)
        assert node_count == 1

        engine, session_factory = await create_test_database(database_url)
        try:
            async with session_factory() as db:
                assert await RunHistoryService(db).identity(APP_ID, "task-1") == identity
        finally:
            await engine.dispose()

    asyncio.run(exercise())


def test_a_conflicting_terminal_event_cannot_replace_the_first(tmp_path: Path) -> None:
    async def exercise() -> None:
        engine, session_factory = await create_test_database(f"sqlite:///{tmp_path / 'workflow.db'}", (APP_ID,))
        try:
            async with session_factory() as db:
                history = RunHistoryService(db)
                snapshot = make_snapshot()
                await history.create(snapshot)
                terminal = WorkflowFinished(
                    identity=snapshot.identity,
                    sequence=0,
                    outcome=RunSucceeded(outputs={"message": "First"}, exceptions_count=0),
                    elapsed_time=0.01,
                    total_tokens=0,
                    total_steps=1,
                    created_at=NOW,
                    finished_at=NOW,
                )
                await history.project(terminal)

                conflicting = WorkflowFinished(
                    identity=snapshot.identity,
                    sequence=1,
                    outcome=RunSucceeded(outputs={"message": "Second"}, exceptions_count=0),
                    elapsed_time=0.02,
                    total_tokens=0,
                    total_steps=2,
                    created_at=NOW,
                    finished_at=NOW,
                )
                with pytest.raises(TerminalStateConflict):
                    await history.project(conflicting)

                conflicting_steps = WorkflowFinished(
                    identity=snapshot.identity,
                    sequence=2,
                    outcome=RunSucceeded(outputs={"message": "First"}, exceptions_count=0),
                    elapsed_time=0.01,
                    total_tokens=0,
                    total_steps=2,
                    created_at=NOW,
                    finished_at=NOW,
                )
                with pytest.raises(TerminalStateConflict):
                    await history.project(conflicting_steps)
        finally:
            await engine.dispose()

    asyncio.run(exercise())


def test_restart_marks_an_unfinished_run_as_failed_once(tmp_path: Path) -> None:
    async def exercise() -> None:
        engine, session_factory = await create_test_database(f"sqlite:///{tmp_path / 'workflow.db'}", (APP_ID,))
        try:
            async with session_factory() as db:
                history = RunHistoryService(db)
                await history.create(make_snapshot())

                assert await history.fail_interrupted("Process interrupted") == 1
                assert await history.fail_interrupted("Process interrupted") == 0
        finally:
            await engine.dispose()

    asyncio.run(exercise())
