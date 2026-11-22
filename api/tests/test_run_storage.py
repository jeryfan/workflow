import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from api.db.store import Store
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
    store = Store(f"sqlite:///{database_path}")
    store.ensure_app(APP_ID)
    snapshot = make_snapshot()
    store.create_run(snapshot)
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

    for event in [*events, *events]:
        store.project_event(event)
    store.close()

    with sqlite3.connect(database_path) as connection:
        run = connection.execute("SELECT status, outputs_json, total_steps FROM workflow_runs").fetchone()
        node_count = connection.execute("SELECT COUNT(*) FROM node_executions").fetchone()[0]
    assert run == ("succeeded", '{"message": "Hello World"}', 1)
    assert node_count == 1

    reopened = Store(f"sqlite:///{database_path}")
    assert reopened.get_run_identity(APP_ID, "task-1") == identity
    reopened.close()


def test_a_conflicting_terminal_event_cannot_replace_the_first(tmp_path: Path) -> None:
    store = Store(f"sqlite:///{tmp_path / 'workflow.db'}")
    store.ensure_app(APP_ID)
    snapshot = make_snapshot()
    store.create_run(snapshot)
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
    store.project_event(terminal)

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
        store.project_event(conflicting)

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
        store.project_event(conflicting_steps)
    store.close()


def test_restart_marks_an_unfinished_run_as_failed_once(tmp_path: Path) -> None:
    store = Store(f"sqlite:///{tmp_path / 'workflow.db'}")
    store.ensure_app(APP_ID)
    store.create_run(make_snapshot())

    assert store.fail_interrupted("Process interrupted") == 1
    assert store.fail_interrupted("Process interrupted") == 0
    store.close()
