from __future__ import annotations

import logging
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from jinja2 import StrictUndefined, TemplateError
from jinja2.sandbox import SandboxedEnvironment

from api.core.graphon.dsl.code_runtime import SandboxCodeExecutor
from api.core.graphon.dsl.entities import DslCodeSettings
from api.core.graphon.engine import Engine
from api.core.graphon.engine.command import AbortCommand, InMemoryChannel
from api.core.graphon.engine_events import (
    GraphRunAbortedEvent,
    GraphRunFailedEvent,
    GraphRunPartialSucceededEvent,
    GraphRunPausedEvent,
    GraphRunStartedEvent,
    GraphRunSucceededEvent,
    NodeRunExceptionEvent,
    NodeRunFailedEvent,
    NodeRunIterationFailedEvent,
    NodeRunIterationNextEvent,
    NodeRunIterationStartedEvent,
    NodeRunIterationSucceededEvent,
    NodeRunLoopFailedEvent,
    NodeRunLoopNextEvent,
    NodeRunLoopStartedEvent,
    NodeRunLoopSucceededEvent,
    NodeRunPauseRequestedEvent,
    NodeRunStartedEvent,
    NodeRunStreamChunkEvent,
    NodeRunSucceededEvent,
    NodeRunVariableUpdatedEvent,
)
from api.core.graphon.engine_events.base import EngineEvent, NodeEvent
from api.core.graphon.entities.base_node_data import BaseNodeData
from api.core.graphon.entities.graph_config import NodeConfigDict
from api.core.graphon.enums import NodeExecutionType
from api.core.graphon.graph import Graph
from api.core.graphon.nodes.base.node import Node
from api.core.graphon.nodes.code.limits import CodeNodeLimits
from api.core.graphon.runtime import InitParams, RuntimeState, VariablePool
from api.core.graphon.template_rendering import Jinja2TemplateRenderer, TemplateRenderError
from api.models.entities import (
    ContainerEvent,
    ExecutionEvent,
    NodeFinished,
    NodeStarted,
    RunFailed,
    RunPartiallySucceeded,
    RunSnapshot,
    RunStopped,
    RunSucceeded,
    WorkflowFinished,
    WorkflowPaused,
    WorkflowStarted,
)
from api.schemas.human_input import HumanInputConfig
from api.services.adapters import build_http_request_config, http_request_dependencies
from api.services.file_runtime import WorkflowFileRuntime
from api.services.human_input import HumanInputRuntime
from api.services.node_registry import NODE_BUILDERS
from api.services.tools import UnsupportedToolFileManager, WorkflowToolNodeRuntime

logger = logging.getLogger(__name__)


class SandboxJinja2Renderer(Jinja2TemplateRenderer):
    def render_template(self, template: str, variables: Mapping[str, Any]) -> str:
        try:
            environment = SandboxedEnvironment(autoescape=False, undefined=StrictUndefined)
            return environment.from_string(template).render(**dict(variables))
        except TemplateError as error:
            raise TemplateRenderError(str(error)) from error


@dataclass(frozen=True, slots=True)
class WhitelistNodeFactory:
    graph_config: Mapping[str, Any]
    init_params: InitParams
    runtime_state: RuntimeState
    tool_service: Any = None
    actor: Any = None
    app_id: str = ""
    call_depth: int = 0
    call_stack: tuple[str, ...] = ()
    session_factory: Any = None
    database_url: str | None = None
    human_input_events: list[Mapping[str, Any]] | None = None

    def with_runtime_state(self, runtime_state: RuntimeState) -> WhitelistNodeFactory:
        return replace(self, runtime_state=runtime_state)

    def with_graph_config(self, graph_config: Mapping[str, Any]) -> WhitelistNodeFactory:
        return replace(
            self,
            graph_config=graph_config,
            init_params=self.init_params.model_copy(update={"graph_config": graph_config}),
        )

    def validate_node(self, node_config: NodeConfigDict) -> NodeExecutionType:
        node_class, _ = self._resolve_node(node_config)
        return node_class.execution_type

    def create_node(self, node_config: NodeConfigDict) -> Node:
        node_class, node_data = self._resolve_node(node_config)
        arguments = {
            "node_id": str(node_config["id"]),
            "data": node_data,
            "init_params": self.init_params,
            "runtime_state": self.runtime_state,
        }
        if node_class is NODE_BUILDERS["template-transform"]:
            return node_class(**arguments, jinja2_template_renderer=SandboxJinja2Renderer())
        if node_class is NODE_BUILDERS["http-request"]:
            return node_class(
                **arguments,
                http_request_config=build_http_request_config(),
                dependencies=http_request_dependencies(),
            )
        if node_class is NODE_BUILDERS["code"]:
            settings = DslCodeSettings()
            return node_class(
                **arguments,
                code_executor=SandboxCodeExecutor(settings),
                code_limits=CodeNodeLimits(
                    max_string_length=settings.max_string_length,
                    max_number=settings.max_number,
                    min_number=settings.min_number,
                    max_precision=settings.max_precision,
                    max_depth=settings.max_depth,
                    max_number_array_length=settings.max_number_array_length,
                    max_string_array_length=settings.max_string_array_length,
                    max_object_array_length=settings.max_object_array_length,
                ),
            )
        if node_class is NODE_BUILDERS["human-input"]:
            if self.session_factory is None or self.actor is None:
                raise ValueError("Human input runtime is not configured")
            raw_data = node_config["data"]
            config = HumanInputConfig.model_validate(
                raw_data if isinstance(raw_data, Mapping) else raw_data.model_dump(mode="json")
            )
            callback = HumanInputRuntime(
                self.session_factory,
                config=config,
                actor_id=self.actor.id,
                database_url=self.database_url,
                events=self.human_input_events,
            )
            node = node_class(**arguments, hitl_callback=callback)
            callback.bind_node(node)
            return node
        if node_class is NODE_BUILDERS["tool"]:
            if self.tool_service is None or self.actor is None:
                raise ValueError("Workflow tool runtime is not configured")
            return node_class(
                **arguments,
                tool_file_manager=UnsupportedToolFileManager(),
                runtime=WorkflowToolNodeRuntime(
                    self.tool_service,
                    caller_app_id=self.app_id,
                    actor=self.actor,
                    call_depth=self.call_depth,
                    call_stack=self.call_stack,
                ),
            )
        return node_class(**arguments)

    @staticmethod
    def _resolve_node(node_config: NodeConfigDict) -> tuple[type[Node], BaseNodeData]:
        data = node_config["data"]
        node_type = data.type if isinstance(data, BaseNodeData) else data["type"]
        node_class = NODE_BUILDERS.get(str(node_type))
        if node_class is None:
            raise ValueError(f"Unsupported node type: {node_type}")
        return node_class, node_class.validate_node_data(data)


class GraphonExecution:
    def __init__(
        self,
        engine: Engine,
        channel: InMemoryChannel,
        snapshot: RunSnapshot,
        *,
        human_input_events: list[Mapping[str, Any]] | None = None,
        sequence_start: int = 0,
    ) -> None:
        self._engine = engine
        self._channel = channel
        self._snapshot = snapshot
        self._human_input_events = human_input_events if human_input_events is not None else []
        self._sequence = sequence_start
        self._node_index = 0
        self._started_nodes: dict[str, NodeStarted] = {}
        self._terminal_emitted = False
        self._started_at = time.perf_counter()

    def events(self) -> Iterator[ExecutionEvent]:
        yield self._workflow_started()
        paused_event = None
        try:
            for event in self._engine.run():
                if isinstance(event, GraphRunPausedEvent):
                    paused_event = event
                    mapped = None
                else:
                    mapped = self._map_event(event)
                yield from self._drain_human_input_events()
                if mapped is not None:
                    yield mapped
            if paused_event is not None:
                yield self._workflow_paused(paused_event)
        except Exception:
            logger.exception("Graphon workflow execution failed")
            if not self._terminal_emitted:
                yield self._workflow_finished(
                    RunFailed(outputs={}, error="Workflow execution failed", exceptions_count=1)
                )

    def _drain_human_input_events(self) -> Iterator[ContainerEvent]:
        pending = list(self._human_input_events)
        self._human_input_events.clear()
        for item in pending:
            yield ContainerEvent(
                identity=self._snapshot.identity,
                sequence=self._next_sequence(),
                event=str(item["event"]),
                data=dict(item["data"]),
            )

    def request_stop(self, reason: str) -> None:
        self._channel.send_command(AbortCommand(reason=reason))

    def _workflow_started(self) -> WorkflowStarted:
        return WorkflowStarted(
            identity=self._snapshot.identity,
            sequence=self._next_sequence(),
            inputs=self._snapshot.inputs,
            created_at=self._snapshot.created_at,
        )

    def _workflow_paused(self, event: GraphRunPausedEvent) -> WorkflowPaused:
        return WorkflowPaused(
            identity=self._snapshot.identity,
            sequence=self._next_sequence(),
            runtime_state=self._engine.runtime_state.dumps(),
            reasons=tuple(event.model_dump(mode="json")["reasons"]),
            outputs=event.model_dump(mode="json")["outputs"],
            elapsed_time=time.perf_counter() - self._started_at,
            total_tokens=0,
            total_steps=self._node_index,
            created_at=self._snapshot.created_at,
        )

    def _map_event(self, event: EngineEvent) -> ExecutionEvent | None:
        if isinstance(event, GraphRunStartedEvent):
            return None
        if isinstance(event, NodeRunStartedEvent):
            return self._node_started(event)
        if isinstance(
            event,
            NodeRunIterationStartedEvent
            | NodeRunIterationNextEvent
            | NodeRunIterationSucceededEvent
            | NodeRunIterationFailedEvent
            | NodeRunLoopStartedEvent
            | NodeRunLoopNextEvent
            | NodeRunLoopSucceededEvent
            | NodeRunLoopFailedEvent,
        ):
            return self._container_event(event)
        if isinstance(event, NodeRunVariableUpdatedEvent):
            return None
        if isinstance(event, NodeRunStreamChunkEvent):
            return ContainerEvent(
                identity=self._snapshot.identity,
                sequence=self._next_sequence(),
                event="text_chunk",
                data={"text": event.chunk, "from_variable_selector": list(event.selector)},
            )
        if isinstance(event, NodeRunSucceededEvent | NodeRunFailedEvent | NodeRunExceptionEvent):
            return self._node_finished(event)
        if isinstance(event, GraphRunSucceededEvent):
            outputs = event.model_dump(mode="json")["outputs"]
            return self._workflow_finished(RunSucceeded(outputs=outputs, exceptions_count=0))
        if isinstance(event, GraphRunPartialSucceededEvent):
            outputs = event.model_dump(mode="json")["outputs"]
            return self._workflow_finished(
                RunPartiallySucceeded(outputs=outputs, exceptions_count=event.exceptions_count)
            )
        if isinstance(event, GraphRunFailedEvent):
            return self._workflow_finished(
                RunFailed(outputs={}, error=event.error, exceptions_count=event.exceptions_count)
            )
        if isinstance(event, GraphRunAbortedEvent):
            outputs = event.model_dump(mode="json")["outputs"]
            return self._workflow_finished(RunStopped(outputs=outputs, reason=event.reason or "Stopped by user"))
        if isinstance(event, GraphRunPausedEvent | NodeRunPauseRequestedEvent):
            return None
        if isinstance(event, NodeEvent):
            raise ValueError(f"Unsupported node event: {type(event).__name__}")
        return None

    def _container_event(self, event: NodeEvent) -> ContainerEvent:
        event_names = {
            NodeRunIterationStartedEvent: "iteration_started",
            NodeRunIterationNextEvent: "iteration_next",
            NodeRunIterationSucceededEvent: "iteration_completed",
            NodeRunIterationFailedEvent: "iteration_completed",
            NodeRunLoopStartedEvent: "loop_started",
            NodeRunLoopNextEvent: "loop_next",
            NodeRunLoopSucceededEvent: "loop_completed",
            NodeRunLoopFailedEvent: "loop_completed",
        }
        data = event.model_dump(mode="json")
        data.update(
            {
                "id": event.id,
                "node_id": event.node_id,
                "node_type": str(event.node_type),
                "title": event.node_title,
                "created_at": int(self._utc(event.start_at).timestamp())
                if hasattr(event, "start_at")
                else int(datetime.now(UTC).timestamp()),
            }
        )
        if "pre_iteration_output" in data:
            data["output"] = data.pop("pre_iteration_output")
        if "pre_loop_output" in data:
            data["output"] = data.pop("pre_loop_output")
        if isinstance(event, NodeRunIterationFailedEvent | NodeRunLoopFailedEvent):
            data["status"] = "failed"
        else:
            data["status"] = "succeeded"
        return ContainerEvent(
            identity=self._snapshot.identity,
            sequence=self._next_sequence(),
            event=event_names[type(event)],
            data=data,
        )

    def _node_started(self, event: NodeRunStartedEvent) -> NodeStarted:
        self._node_index += 1
        started = NodeStarted(
            identity=self._snapshot.identity,
            sequence=self._next_sequence(),
            execution_id=event.id,
            node_id=event.node_id,
            node_type=str(event.node_type),
            title=event.node_title,
            index=self._node_index,
            predecessor_node_id=event.predecessor_node_id,
            created_at=self._utc(event.start_at),
        )
        self._started_nodes[event.id] = started
        return started

    def _node_finished(self, event: NodeRunSucceededEvent | NodeRunFailedEvent | NodeRunExceptionEvent) -> NodeFinished:
        started = self._started_nodes[event.id]
        result = event.node_run_result.model_dump(mode="json")
        outputs = result["inputs"] if event.node_type == "start" else result["outputs"]
        finished_at = self._utc(event.finished_at or datetime.now(UTC))
        return NodeFinished(
            identity=self._snapshot.identity,
            sequence=self._next_sequence(),
            execution_id=event.id,
            node_id=event.node_id,
            node_type=str(event.node_type),
            title=started.title,
            index=started.index,
            predecessor_node_id=started.predecessor_node_id,
            inputs=result["inputs"],
            process_data=result["process_data"],
            outputs=outputs,
            status=str(result["status"]),
            error=result["error"] or getattr(event, "error", None),
            elapsed_time=(finished_at - started.created_at).total_seconds(),
            created_at=started.created_at,
            finished_at=finished_at,
        )

    def _workflow_finished(self, outcome) -> WorkflowFinished:
        self._terminal_emitted = True
        return WorkflowFinished(
            identity=self._snapshot.identity,
            sequence=self._next_sequence(),
            outcome=outcome,
            elapsed_time=time.perf_counter() - self._started_at,
            total_tokens=0,
            total_steps=self._node_index,
            created_at=self._snapshot.created_at,
            finished_at=datetime.now(UTC),
        )

    def _next_sequence(self) -> int:
        sequence = self._sequence
        self._sequence += 1
        return sequence

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class GraphonWorkflowEngine:
    def __init__(self, tool_service=None, files=None, session_factory=None, database_url: str | None = None) -> None:
        self.tool_service = tool_service
        self.file_runtime = WorkflowFileRuntime(files) if files is not None else None
        self.session_factory = session_factory
        self.database_url = database_url

    @staticmethod
    async def prepare_inputs(snapshot: RunSnapshot, files) -> RunSnapshot:
        root_id = snapshot.root_node_id
        if root_id is None:
            start_ids = [
                node_id for node_id, node in snapshot.graph.nodes_by_id.items() if node["data"]["type"] == "start"
            ]
            if len(start_ids) != 1:
                return snapshot
            root_id = start_ids[0]
        variables = snapshot.graph.nodes_by_id[root_id]["data"].get("variables", [])
        variable_by_name = {
            variable.get("variable"): variable for variable in variables if isinstance(variable, Mapping)
        }
        inputs = dict(snapshot.inputs)
        for name, value in inputs.items():
            variable = variable_by_name.get(name)
            if isinstance(variable, Mapping) and variable.get("type") in {"file", "file-list"}:
                inputs[name] = await files.convert_input(
                    value,
                    variable,
                    account_id=snapshot.actor.id,
                    app_id=snapshot.identity.app_id,
                )
        return replace(snapshot, inputs=inputs)

    @staticmethod
    def _root_id(snapshot: RunSnapshot) -> str:
        root_ids = [
            node_id
            for node_id, node in snapshot.graph.nodes_by_id.items()
            if node["data"]["type"] in {"start", "trigger-schedule", "trigger-webhook"}
        ]
        if snapshot.root_node_id is not None:
            if snapshot.root_node_id not in root_ids:
                raise ValueError("Requested trigger root does not exist in this workflow version")
            return snapshot.root_node_id
        start_ids = [node_id for node_id in root_ids if snapshot.graph.nodes_by_id[node_id]["data"]["type"] == "start"]
        if len(start_ids) == 1:
            return start_ids[0]
        if len(root_ids) == 1:
            return root_ids[0]
        raise ValueError("Workflow execution requires exactly one selected start or trigger node")

    def _execution(
        self,
        snapshot: RunSnapshot,
        runtime_state: RuntimeState,
        *,
        human_input_events: list[Mapping[str, Any]],
        sequence_start: int,
    ) -> GraphonExecution:
        graph_config = snapshot.graph.to_json_object()
        init_params = InitParams(
            workflow_id=snapshot.identity.workflow_id,
            graph_config=graph_config,
            run_context={"user_id": snapshot.actor.id},
            call_depth=snapshot.call_depth,
        )
        factory = WhitelistNodeFactory(
            graph_config=graph_config,
            init_params=init_params,
            runtime_state=runtime_state,
            tool_service=self.tool_service,
            actor=snapshot.actor,
            app_id=snapshot.identity.app_id,
            call_depth=snapshot.call_depth,
            call_stack=(*snapshot.call_stack, snapshot.identity.app_id),
            session_factory=self.session_factory,
            database_url=self.database_url,
            human_input_events=human_input_events,
        )
        graph = Graph.init(
            graph_config=graph_config,
            node_factory=factory,
            root_node_id=self._root_id(snapshot),
        )
        channel = InMemoryChannel()
        engine = Engine(
            graph=graph,
            runtime_state=runtime_state,
            command_channel=channel,
            workers=4,
            file_runtime=self.file_runtime,
        )
        return GraphonExecution(
            engine,
            channel,
            snapshot,
            human_input_events=human_input_events,
            sequence_start=sequence_start,
        )

    def start(self, snapshot: RunSnapshot) -> GraphonExecution:
        root_id = self._root_id(snapshot)
        variable_pool = VariablePool()
        system_values = {
            "user_id": snapshot.actor.id,
            "app_id": snapshot.identity.app_id,
            "workflow_id": snapshot.identity.workflow_id,
            "workflow_run_id": snapshot.identity.workflow_run_id,
            "timestamp": int(snapshot.created_at.timestamp()),
        }
        for name, value in system_values.items():
            variable_pool.add(["sys", name], value)
        for name, value in snapshot.inputs.items():
            variable_pool.add([root_id, name], value)
        runtime_state = RuntimeState(
            variable_pool=variable_pool,
            start_at=time.perf_counter(),
            workflow_id=snapshot.identity.workflow_id,
        )
        return self._execution(snapshot, runtime_state, human_input_events=[], sequence_start=0)

    def resume(self, snapshot: RunSnapshot, runtime_state: str, *, sequence_start: int = 0) -> GraphonExecution:
        restored = RuntimeState.from_snapshot(runtime_state)
        return self._execution(snapshot, restored, human_input_events=[], sequence_start=sequence_start)
