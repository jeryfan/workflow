from __future__ import annotations

import re
import time
from collections.abc import Generator, Mapping
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from api.db.store import Store
from api.models.entities import (
    Actor,
    AppId,
    RunFailed,
    RunPartiallySucceeded,
    RunStopped,
    RunSucceeded,
    WorkflowFinished,
    WorkflowTool,
)
from api.services.workflows import RunService
from graphon.model_runtime.entities.llm_entities import LLMUsage
from graphon.nodes.tool.entities import ToolNodeData
from graphon.nodes.tool.exc import ToolFileError, ToolNodeError
from graphon.nodes.tool_runtime_entities import ToolRuntimeHandle, ToolRuntimeMessage, ToolRuntimeParameter
from graphon.variables.template_resolution import convert_template

TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,49}$")
MAX_WORKFLOW_CALL_DEPTH = 4


class WorkflowToolNotFound(Exception):
    pass


class WorkflowToolConflict(Exception):
    pass


class WorkflowToolService:
    def __init__(self, store: Store, runs: RunService) -> None:
        self.store = store
        self.runs = runs

    @staticmethod
    def output_schema(workflow) -> dict[str, Any]:
        properties: dict[str, dict[str, Any]] = {}
        for node in workflow.graph.to_json_object()["nodes"]:
            data = node.get("data", {})
            if data.get("type") != "end":
                continue
            for output in data.get("outputs", []):
                name = output.get("variable")
                if not isinstance(name, str) or not name:
                    continue
                properties[name] = {
                    "type": output.get("value_type") or "string",
                    "description": output.get("description") or "",
                }
        return {"type": "object", "properties": properties}

    @staticmethod
    def localized(value: str) -> dict[str, str]:
        return {"en_US": value, "zh_Hans": value}

    @staticmethod
    def parameter_type(value: Any) -> str:
        return {
            "text-input": "string",
            "paragraph": "string",
            "select": "string",
            "checkbox": "boolean",
            "number": "number",
            "json_object": "object",
        }.get(str(value), "string")

    def tool_entity(self, tool: WorkflowTool) -> dict[str, Any]:
        published = self.store.get_published(tool.app_id)
        app = self.store.get_app_record(tool.app_id)
        author = app.author_name if app is not None and app.author_name else "Workflow"
        parameters = []
        for parameter in tool.parameters:
            name = str(parameter["name"])
            description = str(parameter.get("description") or "")
            parameters.append(
                {
                    "name": name,
                    "label": self.localized(name),
                    "human_description": self.localized(description),
                    "llm_description": description,
                    "type": self.parameter_type(parameter.get("type")),
                    "form": parameter.get("form") or "llm",
                    "required": bool(parameter.get("required", False)),
                    "multiple": False,
                }
            )
        return {
            "name": tool.name,
            "author": author,
            "label": self.localized(tool.label),
            "description": self.localized(tool.description),
            "parameters": parameters,
            "labels": list(tool.labels),
            "output_schema": self.output_schema(published) if published is not None else {},
        }

    def provider_entity(self, tool: WorkflowTool) -> dict[str, Any]:
        return {
            "id": tool.id,
            "name": tool.name,
            "author": "Workflow",
            "description": self.localized(tool.description),
            "icon": dict(tool.icon),
            "label": self.localized(tool.label),
            "type": "workflow",
            "team_credentials": {},
            "is_team_authorization": True,
            "allow_delete": True,
            "labels": list(tool.labels),
            "workflow_app_id": tool.app_id,
            "tools": [self.tool_entity(tool)],
        }

    def list(self) -> list[dict[str, Any]]:
        return [self.provider_entity(tool) for tool in self.store.list_workflow_tools()]

    def detail(self, tool: WorkflowTool) -> dict[str, Any]:
        published = self.store.get_published(tool.app_id)
        return {
            "workflow_app_id": tool.app_id,
            "workflow_tool_id": tool.id,
            "label": tool.label,
            "name": tool.name,
            "icon": dict(tool.icon),
            "description": tool.description,
            "synced": published is not None and published.id == tool.published_workflow_id,
            "tool": self.tool_entity(tool),
            "privacy_policy": tool.privacy_policy,
        }

    @staticmethod
    def validate_definition(
        *,
        name: str,
        label: str,
        parameters: tuple[Mapping[str, Any], ...],
    ) -> None:
        if not TOOL_NAME_PATTERN.fullmatch(name):
            raise ValueError("Tool name must start with a letter and contain only letters, numbers, and underscores")
        if not label.strip():
            raise ValueError("Tool label must not be empty")
        names = [str(parameter.get("name") or "") for parameter in parameters]
        if any(not name for name in names) or len(set(names)) != len(names):
            raise ValueError("Tool parameter names must be non-empty and unique")
        if any(parameter.get("type") in {"file", "file-list"} for parameter in parameters):
            raise ValueError("File inputs are not supported by workflow tools")

    def create(
        self,
        *,
        app_id: AppId,
        name: str,
        label: str,
        description: str,
        icon: Mapping[str, Any],
        parameters: tuple[Mapping[str, Any], ...],
        labels: tuple[str, ...],
        privacy_policy: str,
    ) -> WorkflowTool:
        self.validate_definition(name=name, label=label, parameters=parameters)
        app = self.store.get_app_record(app_id)
        published = self.store.get_published(app_id)
        if app is None or published is None:
            raise ValueError("Publish the workflow before configuring it as a tool")
        if self.store.get_workflow_tool_by_app(app_id) is not None:
            raise WorkflowToolConflict("This workflow already has a tool configuration")
        if any(tool.name == name for tool in self.store.list_workflow_tools()):
            raise WorkflowToolConflict("A workflow tool with this name already exists")
        now = int(time.time())
        tool = WorkflowTool(
            id=str(uuid4()),
            app_id=app_id,
            published_workflow_id=published.id,
            name=name,
            label=label,
            description=description,
            icon=icon,
            parameters=parameters,
            labels=labels,
            privacy_policy=privacy_policy,
            created_at=now,
            updated_at=now,
        )
        self.store.create_workflow_tool(tool)
        return tool

    def update(
        self,
        tool_id: str,
        *,
        name: str,
        label: str,
        description: str,
        icon: Mapping[str, Any],
        parameters: tuple[Mapping[str, Any], ...],
        labels: tuple[str, ...],
        privacy_policy: str,
    ) -> WorkflowTool:
        current = self.store.get_workflow_tool(tool_id)
        if current is None:
            raise WorkflowToolNotFound
        published = self.store.get_published(current.app_id)
        if published is None:
            raise ValueError("Publish the workflow before updating its tool")
        self.validate_definition(name=name, label=label, parameters=parameters)
        if any(tool.id != tool_id and tool.name == name for tool in self.store.list_workflow_tools()):
            raise WorkflowToolConflict("A workflow tool with this name already exists")
        updated = WorkflowTool(
            id=current.id,
            app_id=current.app_id,
            published_workflow_id=published.id,
            name=name,
            label=label,
            description=description,
            icon=icon,
            parameters=parameters,
            labels=labels,
            privacy_policy=privacy_policy,
            created_at=current.created_at,
            updated_at=int(time.time()),
        )
        self.store.update_workflow_tool(updated)
        return updated

    def runtime_tool(self, provider_id: str, tool_name: str, caller_app_id: AppId) -> WorkflowTool:
        tool = self.store.get_workflow_tool(provider_id)
        caller = self.store.get_app_record(caller_app_id)
        if tool is None or tool.name != tool_name or caller is None:
            raise ToolNodeError("The selected workflow tool is unavailable")
        return tool

    def invoke(
        self,
        *,
        provider_id: str,
        tool_name: str,
        inputs: Mapping[str, Any],
        caller_app_id: AppId,
        actor: Actor,
        call_depth: int,
        call_stack: tuple[str, ...],
    ) -> dict[str, Any]:
        tool = self.runtime_tool(provider_id, tool_name, caller_app_id)
        if call_depth >= MAX_WORKFLOW_CALL_DEPTH or tool.app_id in call_stack:
            raise ToolNodeError("Workflow tool call depth exceeded or a circular call was detected")
        declared = {str(parameter["name"]): parameter for parameter in tool.parameters}
        values = {name: inputs[name] for name in declared if name in inputs}
        for name, parameter in declared.items():
            if parameter.get("required") and (name not in values or values[name] is None or values[name] == ""):
                raise ToolNodeError(f"Required workflow tool input is missing: {name}")
        session = self.runs.start_published(
            tool.app_id,
            values,
            actor,
            call_depth=call_depth + 1,
            call_stack=call_stack,
        )
        try:
            for event in session.events():
                if not isinstance(event, WorkflowFinished):
                    continue
                outcome = event.outcome
                if isinstance(outcome, RunSucceeded | RunPartiallySucceeded):
                    return dict(outcome.outputs)
                if isinstance(outcome, RunStopped):
                    raise ToolNodeError(outcome.reason)
                if isinstance(outcome, RunFailed):
                    raise ToolNodeError(outcome.error)
            raise ToolNodeError("Workflow tool finished without a result")
        finally:
            session.close()


@dataclass(frozen=True, slots=True)
class _PreparedWorkflowTool:
    tool: WorkflowTool
    fixed_inputs: Mapping[str, Any]


class WorkflowToolNodeRuntime:
    def __init__(
        self,
        service: WorkflowToolService,
        *,
        caller_app_id: AppId,
        actor: Actor,
        call_depth: int,
        call_stack: tuple[str, ...],
    ) -> None:
        self.service = service
        self.caller_app_id = caller_app_id
        self.actor = actor
        self.call_depth = call_depth
        self.call_stack = call_stack

    def get_runtime(
        self,
        *,
        node_id: str,
        node_data: ToolNodeData,
        variable_pool,
        node_execution_id: str | None = None,
    ) -> ToolRuntimeHandle:
        _ = node_id, node_execution_id
        if node_data.provider_type.value != "workflow":
            raise ToolNodeError("Only workflow tools are supported")
        tool = self.service.runtime_tool(node_data.provider_id, node_data.tool_name, self.caller_app_id)
        fixed_inputs = {}
        for name, configured in node_data.tool_configurations.items():
            value = configured
            input_type = None
            if isinstance(configured, Mapping):
                input_type = configured.get("type")
                value = configured.get("value")
            if input_type == "variable" and isinstance(value, list):
                variable = variable_pool.get(value) if variable_pool is not None else None
                value = variable.value if variable is not None else None
            elif input_type in {"mixed", "constant"} and value is not None and variable_pool is not None:
                value = convert_template(variable_pool, str(value)).text
            fixed_inputs[name] = value
        return ToolRuntimeHandle(raw=_PreparedWorkflowTool(tool=tool, fixed_inputs=fixed_inputs))

    def get_runtime_parameters(self, *, tool_runtime: ToolRuntimeHandle) -> list[ToolRuntimeParameter]:
        prepared = self._prepared(tool_runtime)
        return [
            ToolRuntimeParameter(name=str(parameter["name"]), required=bool(parameter.get("required", False)))
            for parameter in prepared.tool.parameters
        ]

    def invoke(
        self,
        *,
        tool_runtime: ToolRuntimeHandle,
        tool_parameters: Mapping[str, Any],
        workflow_call_depth: int,
        provider_name: str,
    ) -> Generator[ToolRuntimeMessage]:
        _ = provider_name
        prepared = self._prepared(tool_runtime)
        outputs = self.service.invoke(
            provider_id=prepared.tool.id,
            tool_name=prepared.tool.name,
            inputs={**prepared.fixed_inputs, **tool_parameters},
            caller_app_id=self.caller_app_id,
            actor=self.actor,
            call_depth=max(self.call_depth, workflow_call_depth),
            call_stack=self.call_stack,
        )
        for name, value in outputs.items():
            yield ToolRuntimeMessage(
                type=ToolRuntimeMessage.MessageType.VARIABLE,
                message=ToolRuntimeMessage.VariableMessage(variable_name=name, variable_value=value),
            )
        yield ToolRuntimeMessage(
            type=ToolRuntimeMessage.MessageType.JSON,
            message=ToolRuntimeMessage.JsonMessage(json_object=outputs),
        )

    def get_usage(self, *, tool_runtime: ToolRuntimeHandle) -> LLMUsage:
        _ = tool_runtime
        return LLMUsage.empty_usage()

    def build_file_reference(self, *, mapping: Mapping[str, Any]) -> Any:
        _ = mapping
        raise ToolFileError("Workflow tool file references are not supported")

    @staticmethod
    def _prepared(tool_runtime: ToolRuntimeHandle) -> _PreparedWorkflowTool:
        if not isinstance(tool_runtime.raw, _PreparedWorkflowTool):
            raise ToolNodeError("Invalid workflow tool runtime handle")
        return tool_runtime.raw


class UnsupportedToolFileManager:
    def create_file_by_raw(self, **_kwargs) -> Any:
        raise ToolFileError("Workflow tool files are not supported")

    def get_file_generator_by_tool_file_id(self, *, tool_file_id: str):
        _ = tool_file_id
        raise ToolFileError("Workflow tool files are not supported")
