from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any, NewType

AppId = NewType("AppId", str)
WorkflowId = NewType("WorkflowId", str)
DraftHash = NewType("DraftHash", str)
type JsonScalar = None | bool | int | float | str
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        frozen = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("JSON object keys must be strings")
            frozen[key] = _freeze(item)
        return MappingProxyType(frozen)
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class GraphDocument:
    raw: Mapping[str, Any]
    nodes_by_id: Mapping[str, Mapping[str, Any]]

    @classmethod
    def parse(cls, value: Mapping[str, object]) -> GraphDocument:
        if not isinstance(value, Mapping):
            raise ValueError("Graph must be an object")
        if not isinstance(value.get("nodes", []), list):
            raise ValueError("Graph nodes must be an array")
        if not isinstance(value.get("edges", []), list):
            raise ValueError("Graph edges must be an array")

        raw = _freeze(value)
        nodes_by_id: dict[str, Mapping[str, Any]] = {}
        for node in raw.get("nodes", ()):
            if not isinstance(node, Mapping):
                raise ValueError("Every graph node must be an object")
            node_id = node.get("id")
            if not isinstance(node_id, str) or not node_id:
                raise ValueError("Every graph node needs a non-empty id")
            if node_id in nodes_by_id:
                raise ValueError(f"Duplicate node id: {node_id}")
            data = node.get("data")
            if not isinstance(data, Mapping):
                raise ValueError(f"Node {node_id} needs a data object")
            node_type = data.get("type")
            if not isinstance(node_type, str) or not node_type:
                raise ValueError(f"Node {node_id} needs a non-empty data.type")
            nodes_by_id[node_id] = node

        for edge in raw.get("edges", ()):
            if not isinstance(edge, Mapping):
                raise ValueError("Every graph edge must be an object")
            for field in ("source", "target"):
                if not isinstance(edge.get(field), str) or not edge.get(field):
                    raise ValueError(f"Every graph edge needs a non-empty {field}")

        return cls(raw=raw, nodes_by_id=MappingProxyType(nodes_by_id))

    def to_json_object(self) -> JsonObject:
        return _thaw(self.raw)


def generate_draft_hash(graph: GraphDocument) -> DraftHash:
    text = json.dumps({"graph": graph.to_json_object()}, sort_keys=True)
    return DraftHash(hashlib.sha256((text + "None").encode()).hexdigest())


@dataclass(frozen=True, slots=True)
class Actor:
    id: str
    name: str
    email: str


@dataclass(frozen=True, slots=True)
class AppRecord:
    id: str
    name: str
    mode: str
    created_at: int
    updated_at: int
    maintainer_id: str | None
    created_by_id: str | None
    updated_by_id: str | None
    published_workflow_id: str | None
    author_name: str | None
    description: str
    icon: str | None
    icon_type: str | None
    icon_background: str | None
    use_icon_as_answer_icon: bool


@dataclass(frozen=True, slots=True)
class AppSite:
    app_id: AppId
    access_token: str
    enable_site: bool
    created_at: int
    updated_at: int


@dataclass(frozen=True, slots=True)
class WorkflowTool:
    id: str
    app_id: AppId
    published_workflow_id: str
    name: str
    label: str
    description: str
    icon: Mapping[str, Any]
    parameters: tuple[Mapping[str, Any], ...]
    labels: tuple[str, ...]
    privacy_policy: str
    created_at: int
    updated_at: int


@dataclass(frozen=True, slots=True)
class WorkflowTrigger:
    id: str
    app_id: AppId
    workflow_id: str
    node_id: str
    trigger_type: str
    status: str
    webhook_id: str | None
    config: Mapping[str, Any]
    created_by: Actor
    next_run_at: int | None
    last_run_key: str | None
    created_at: int
    updated_at: int


@dataclass(frozen=True, slots=True)
class UploadedFile:
    id: str
    app_id: AppId | None
    name: str
    extension: str
    mime_type: str
    size: int
    storage_key: str
    source_url: str | None
    created_by: str
    created_at: int


@dataclass(frozen=True, slots=True)
class CreateAppCommand:
    name: str
    mode: str
    actor: Actor
    description: str = ""
    icon: str = "🤖"
    icon_type: str = "emoji"
    icon_background: str = "#FFEAD5"


@dataclass(frozen=True, slots=True)
class UpdateAppCommand:
    app_id: str
    actor: Actor
    name: str | None = None
    description: str | None = None
    icon: str | None = None
    icon_type: str | None = None
    icon_background: str | None = None
    use_icon_as_answer_icon: bool | None = None


@dataclass(frozen=True, slots=True)
class CopyAppCommand:
    source_app_id: str
    actor: Actor
    name: str | None = None
    description: str | None = None
    icon: str | None = None
    icon_type: str | None = None
    icon_background: str | None = None


@dataclass(frozen=True, slots=True)
class Draft:
    app_id: AppId
    workflow_id: WorkflowId
    graph: GraphDocument
    features: Mapping[str, JsonValue]
    environment_variables: tuple[Mapping[str, JsonValue], ...]
    conversation_variables: tuple[Mapping[str, JsonValue], ...]
    hash: DraftHash
    created_at: datetime
    updated_at: datetime
    created_by: Actor
    updated_by: Actor


@dataclass(frozen=True, slots=True)
class PublishedWorkflow:
    id: str
    app_id: AppId
    version_number: int
    graph: GraphDocument
    features: Mapping[str, JsonValue]
    environment_variables: tuple[Mapping[str, JsonValue], ...]
    conversation_variables: tuple[Mapping[str, JsonValue], ...]
    hash: DraftHash
    marked_name: str
    marked_comment: str
    created_at: datetime
    created_by: Actor


@dataclass(frozen=True, slots=True)
class PublishDraft:
    app_id: AppId
    actor: Actor
    marked_name: str
    marked_comment: str


@dataclass(frozen=True, slots=True)
class EnvironmentVariablePatch:
    upserts: tuple[Mapping[str, JsonValue], ...]
    deleted_ids: tuple[str, ...]


def merge_environment_variables(
    current: tuple[Mapping[str, JsonValue], ...],
    patch: EnvironmentVariablePatch,
) -> tuple[Mapping[str, JsonValue], ...]:
    upserts_by_id = {}
    for variable in patch.upserts:
        variable_id = variable.get("id")
        if not isinstance(variable_id, str) or not variable_id:
            raise ValueError("Patched environment variables require an id")
        if variable_id in upserts_by_id:
            raise ValueError(f"Duplicate patched environment variable id: {variable_id}")
        upserts_by_id[variable_id] = variable
    deleted_ids = frozenset(patch.deleted_ids)
    if len(deleted_ids) != len(patch.deleted_ids):
        raise ValueError("Deleted environment variable ids must be unique")
    if "" in deleted_ids:
        raise ValueError("Deleted environment variable ids must not be empty")
    conflict = deleted_ids.intersection(upserts_by_id)
    if conflict:
        raise ValueError(f"Environment variable cannot be upserted and deleted: {min(conflict)}")
    existing_ids = {str(variable.get("id", "")) for variable in current}
    merged = [
        upserts_by_id.get(str(variable.get("id", "")), variable)
        for variable in current
        if str(variable.get("id", "")) not in deleted_ids
    ]
    merged.extend(variable for variable_id, variable in upserts_by_id.items() if variable_id not in existing_ids)
    names = [variable.get("name") for variable in merged]
    if len(set(names)) != len(names):
        raise ValueError("Environment variable names must be unique")
    return tuple(merged)


@dataclass(frozen=True, slots=True)
class SaveDraft:
    app_id: AppId
    graph: GraphDocument
    features: Mapping[str, JsonValue]
    conversation_variables: tuple[Mapping[str, JsonValue], ...]
    expected_hash: DraftHash | None
    force: bool
    actor: Actor
    environment_variable_patch: EnvironmentVariablePatch | None = None
    environment_variables: tuple[Mapping[str, JsonValue], ...] | None = None
    app_name: str | None = None


class DraftConflict(Exception):
    pass


WorkflowRunId = NewType("WorkflowRunId", str)
TaskId = NewType("TaskId", str)
NodeExecutionId = NewType("NodeExecutionId", str)


@dataclass(frozen=True, slots=True)
class RunIdentity:
    app_id: AppId
    workflow_id: WorkflowId
    workflow_run_id: WorkflowRunId
    task_id: TaskId


@dataclass(frozen=True, slots=True)
class RunSnapshot:
    identity: RunIdentity
    graph: GraphDocument
    draft_hash: DraftHash
    inputs: Mapping[str, JsonValue]
    actor: Actor
    created_at: datetime
    call_depth: int = 0
    call_stack: tuple[str, ...] = ()
    root_node_id: str | None = None


@dataclass(frozen=True, slots=True)
class WorkflowStarted:
    identity: RunIdentity
    sequence: int
    inputs: Mapping[str, JsonValue]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class NodeStarted:
    identity: RunIdentity
    sequence: int
    execution_id: NodeExecutionId
    node_id: str
    node_type: str
    title: str
    index: int
    predecessor_node_id: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class NodeFinished:
    identity: RunIdentity
    sequence: int
    execution_id: NodeExecutionId
    node_id: str
    node_type: str
    title: str
    index: int
    predecessor_node_id: str | None
    inputs: Mapping[str, JsonValue]
    process_data: Mapping[str, JsonValue]
    outputs: Mapping[str, JsonValue]
    status: str
    error: str | None
    elapsed_time: float
    created_at: datetime
    finished_at: datetime


@dataclass(frozen=True, slots=True)
class RunSucceeded:
    outputs: Mapping[str, JsonValue]
    exceptions_count: int


@dataclass(frozen=True, slots=True)
class RunPartiallySucceeded:
    outputs: Mapping[str, JsonValue]
    exceptions_count: int


@dataclass(frozen=True, slots=True)
class RunFailed:
    outputs: Mapping[str, JsonValue]
    error: str
    exceptions_count: int


@dataclass(frozen=True, slots=True)
class RunStopped:
    outputs: Mapping[str, JsonValue]
    reason: str


type RunOutcome = RunSucceeded | RunPartiallySucceeded | RunFailed | RunStopped


@dataclass(frozen=True, slots=True)
class WorkflowFinished:
    identity: RunIdentity
    sequence: int
    outcome: RunOutcome
    elapsed_time: float
    total_tokens: int
    total_steps: int
    created_at: datetime
    finished_at: datetime


@dataclass(frozen=True, slots=True)
class WorkflowPaused:
    identity: RunIdentity
    sequence: int
    runtime_state: str
    reasons: tuple[Mapping[str, JsonValue], ...]
    outputs: Mapping[str, JsonValue]
    elapsed_time: float
    total_tokens: int
    total_steps: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ContainerEvent:
    identity: RunIdentity
    sequence: int
    event: str
    data: Mapping[str, JsonValue]


type ExecutionEvent = WorkflowStarted | NodeStarted | NodeFinished | ContainerEvent | WorkflowFinished | WorkflowPaused


@dataclass(frozen=True, slots=True)
class RunRecord:
    identity: RunIdentity
    graph: GraphDocument
    inputs: Mapping[str, JsonValue]
    status: str
    outputs: Mapping[str, JsonValue]
    error: str | None
    elapsed_time: float
    total_tokens: int
    total_steps: int
    exceptions_count: int
    actor: Actor
    created_at: datetime
    finished_at: datetime | None
    version: str = "draft"
    call_depth: int = 0
    call_stack: tuple[str, ...] = ()
    root_node_id: str | None = None


@dataclass(frozen=True, slots=True)
class RunPage:
    records: tuple[RunRecord, ...]
    limit: int
    has_more: bool


@dataclass(frozen=True, slots=True)
class NodeExecutionRecord:
    id: NodeExecutionId
    node_id: str
    node_type: str
    title: str
    index: int
    predecessor_node_id: str | None
    inputs: Mapping[str, JsonValue]
    process_data: Mapping[str, JsonValue]
    outputs: Mapping[str, JsonValue]
    status: str
    error: str | None
    elapsed_time: float
    created_at: datetime
    finished_at: datetime | None


class TerminalStateConflict(Exception):
    pass
