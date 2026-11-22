from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import fields
from datetime import datetime
from typing import Any

from api.models.entities import (
    ContainerEvent,
    ExecutionEvent,
    NodeFinished,
    NodeStarted,
    RunFailed,
    RunIdentity,
    RunPartiallySucceeded,
    RunStopped,
    RunSucceeded,
    WorkflowFinished,
    WorkflowStarted,
)

EVENT_TYPES = {
    cls.__name__: cls for cls in (WorkflowStarted, NodeStarted, NodeFinished, ContainerEvent, WorkflowFinished)
}
OUTCOME_TYPES = {cls.__name__: cls for cls in (RunSucceeded, RunPartiallySucceeded, RunFailed, RunStopped)}


def _encode(value: Any) -> Any:
    if isinstance(value, datetime):
        return {"__datetime__": value.isoformat()}
    if isinstance(value, RunIdentity):
        return {field.name: _encode(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, (RunSucceeded, RunPartiallySucceeded, RunFailed, RunStopped)):
        return {
            "type": type(value).__name__,
            "data": {field.name: _encode(getattr(value, field.name)) for field in fields(value)},
        }
    if isinstance(value, Mapping):
        return {str(key): _encode(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_encode(item) for item in value]
    return value


def serialize_event(event: ExecutionEvent) -> str:
    return json.dumps(
        {
            "type": type(event).__name__,
            "data": {field.name: _encode(getattr(event, field.name)) for field in fields(event)},
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _datetime(value: Any) -> Any:
    if isinstance(value, dict) and set(value) == {"__datetime__"}:
        return datetime.fromisoformat(value["__datetime__"])
    return value


def deserialize_event(payload: str) -> ExecutionEvent:
    document = json.loads(payload)
    event_type = EVENT_TYPES.get(document.get("type"))
    data = document.get("data")
    if event_type is None or not isinstance(data, dict):
        raise ValueError("Unknown workflow event message")
    data = {key: _datetime(value) for key, value in data.items()}
    data["identity"] = RunIdentity(**data["identity"])
    if event_type is WorkflowFinished:
        outcome = data["outcome"]
        outcome_type = OUTCOME_TYPES.get(outcome.get("type"))
        if outcome_type is None:
            raise ValueError("Unknown workflow outcome")
        data["outcome"] = outcome_type(**outcome["data"])
    return event_type(**data)
