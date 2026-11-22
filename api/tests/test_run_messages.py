from datetime import UTC, datetime

import pytest

from api.models.entities import (
    ContainerEvent,
    NodeFinished,
    NodeStarted,
    RunFailed,
    RunIdentity,
    RunPartiallySucceeded,
    RunStopped,
    RunSucceeded,
    WorkflowFinished,
    WorkflowPaused,
    WorkflowStarted,
)
from api.services.run_messages import deserialize_event, serialize_event

IDENTITY = RunIdentity(app_id="app-1", workflow_id="workflow-1", workflow_run_id="run-1", task_id="task-1")
NOW = datetime.now(UTC)


def test_every_execution_event_roundtrips_through_redis_json() -> None:
    events = [
        WorkflowStarted(identity=IDENTITY, sequence=0, inputs={"name": "World"}, created_at=NOW),
        NodeStarted(
            identity=IDENTITY,
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
            identity=IDENTITY,
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
            elapsed_time=0.1,
            created_at=NOW,
            finished_at=NOW,
        ),
        ContainerEvent(identity=IDENTITY, sequence=3, event="iteration_started", data={"id": "iteration-1"}),
        WorkflowPaused(
            identity=IDENTITY,
            sequence=4,
            runtime_state='{"version":"3.0"}',
            reasons=({"TYPE": "hitl_required", "session_id": "form-1"},),
            outputs={},
            elapsed_time=0.1,
            total_tokens=0,
            total_steps=1,
            created_at=NOW,
        ),
    ]
    for index, outcome in enumerate(
        (
            RunSucceeded(outputs={"message": "Hello"}, exceptions_count=0),
            RunPartiallySucceeded(outputs={"message": "Partial"}, exceptions_count=1),
            RunFailed(outputs={}, error="failed", exceptions_count=1),
            RunStopped(outputs={}, reason="stopped"),
        )
    ):
        events.append(
            WorkflowFinished(
                identity=IDENTITY,
                sequence=5 + index,
                outcome=outcome,
                elapsed_time=0.2,
                total_tokens=0,
                total_steps=2,
                created_at=NOW,
                finished_at=NOW,
            )
        )

    for event in events:
        assert deserialize_event(serialize_event(event)) == event


def test_unknown_or_malformed_messages_are_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown workflow event"):
        deserialize_event('{"type":"unknown","data":{}}')
    with pytest.raises(ValueError, match="Unknown workflow outcome"):
        deserialize_event(
            '{"type":"WorkflowFinished","data":{"identity":'
            '{"app_id":"app","workflow_id":"workflow","workflow_run_id":"run","task_id":"task"},'
            '"outcome":{"type":"Unknown","data":{}}}}'
        )
