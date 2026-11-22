from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Callable, Coroutine, Generator, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.graphon.model_runtime.entities.llm_entities import LLMUsage
from api.core.graphon.nodes.tool.entities import ToolNodeData
from api.core.graphon.nodes.tool.exc import ToolFileError, ToolNodeError
from api.core.graphon.nodes.tool_runtime_entities import ToolRuntimeHandle, ToolRuntimeMessage, ToolRuntimeParameter
from api.core.graphon.variables.template_resolution import convert_template
from api.models.account import Account
from api.models.app import App
from api.models.entities import (
    Actor,
    AppId,
    DraftHash,
    GraphDocument,
    PublishedWorkflow,
    RunFailed,
    RunPartiallySucceeded,
    RunStopped,
    RunSucceeded,
    WorkflowFinished,
    WorkflowTool,
)
from api.models.workflow import WorkflowVersion
from api.models.workflow_tool import WorkflowTool as WorkflowToolModel

from .base_service import BaseService

if TYPE_CHECKING:
    from api.services.run_service import RunService, RunSession

TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,49}$")
MAX_WORKFLOW_CALL_DEPTH = 4


class WorkflowToolNotFound(Exception):
    pass


class WorkflowToolConflict(Exception):
    pass


class WorkflowToolService(BaseService):
    def __init__(self, db: AsyncSession, runs: RunService) -> None:
        super().__init__(db)
        self.runs = runs

    @staticmethod
    def _from_model(tool: WorkflowToolModel) -> WorkflowTool:
        return WorkflowTool(
            id=tool.id,
            app_id=AppId(tool.app_id),
            published_workflow_id=tool.published_workflow_id,
            name=tool.name,
            label=tool.label,
            description=tool.description,
            icon=json.loads(tool.icon_json),
            parameters=tuple(json.loads(tool.parameters_json)),
            labels=tuple(json.loads(tool.labels_json)),
            privacy_policy=tool.privacy_policy,
            created_at=tool.created_at,
            updated_at=tool.updated_at,
        )

    @staticmethod
    def _published_from_model(workflow: WorkflowVersion) -> PublishedWorkflow:
        return PublishedWorkflow(
            id=workflow.id,
            app_id=AppId(workflow.app_id),
            version_number=workflow.version_number,
            graph=GraphDocument.parse(json.loads(workflow.graph_json)),
            features=json.loads(workflow.features_json),
            environment_variables=tuple(json.loads(workflow.environment_variables_json)),
            conversation_variables=tuple(json.loads(workflow.conversation_variables_json)),
            hash=DraftHash(workflow.hash),
            marked_name=workflow.marked_name,
            marked_comment=workflow.marked_comment,
            created_at=datetime.fromtimestamp(workflow.created_at, UTC),
            created_by=Actor(**json.loads(workflow.created_by_json)),
        )

    async def _published(self, app_id: AppId) -> PublishedWorkflow | None:
        workflow = await self.db.scalar(
            select(WorkflowVersion).join(App, App.published_workflow_id == WorkflowVersion.id).where(App.id == app_id)
        )
        return self._published_from_model(workflow) if workflow is not None else None

    @staticmethod
    def output_schema(workflow: PublishedWorkflow) -> dict[str, Any]:
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

    async def tool_entity(self, tool: WorkflowTool) -> dict[str, Any]:
        published = await self._published(tool.app_id)
        author_name = await self.db.scalar(
            select(Account.name).join(App, App.created_by_id == Account.id).where(App.id == tool.app_id)
        )
        author = author_name or "Workflow"
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

    async def provider_entity(self, tool: WorkflowTool) -> dict[str, Any]:
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
            "tools": [await self.tool_entity(tool)],
        }

    async def list(self) -> list[dict[str, Any]]:
        tools = (
            (await self.db.execute(select(WorkflowToolModel).order_by(WorkflowToolModel.created_at))).scalars().all()
        )
        return [await self.provider_entity(self._from_model(tool)) for tool in tools]

    async def get(self, tool_id: str) -> WorkflowTool | None:
        tool = await self.db.get(WorkflowToolModel, tool_id)
        return self._from_model(tool) if tool is not None else None

    async def get_by_app(self, app_id: AppId) -> WorkflowTool | None:
        tool = await self.db.scalar(select(WorkflowToolModel).where(WorkflowToolModel.app_id == app_id))
        return self._from_model(tool) if tool is not None else None

    async def detail(self, tool: WorkflowTool) -> dict[str, Any]:
        published = await self._published(tool.app_id)
        return {
            "workflow_app_id": tool.app_id,
            "workflow_tool_id": tool.id,
            "label": tool.label,
            "name": tool.name,
            "icon": dict(tool.icon),
            "description": tool.description,
            "synced": published is not None and published.id == tool.published_workflow_id,
            "tool": await self.tool_entity(tool),
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

    async def create(
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
        app = await self.db.get(App, app_id)
        published = await self._published(app_id)
        if app is None or published is None:
            raise ValueError("Publish the workflow before configuring it as a tool")
        if await self.get_by_app(app_id) is not None:
            raise WorkflowToolConflict("This workflow already has a tool configuration")
        if await self.db.scalar(select(WorkflowToolModel.id).where(WorkflowToolModel.name == name)) is not None:
            raise WorkflowToolConflict("A workflow tool with this name already exists")
        now = int(time.time())
        tool = WorkflowToolModel(
            id=str(uuid4()),
            app_id=app_id,
            published_workflow_id=published.id,
            name=name,
            label=label,
            description=description,
            icon_json=json.dumps(icon),
            parameters_json=json.dumps(parameters),
            labels_json=json.dumps(labels),
            privacy_policy=privacy_policy,
            created_at=now,
            updated_at=now,
        )
        self.db.add(tool)
        await self.db.flush()
        created = self._from_model(tool)
        await self.db.commit()
        return created

    async def update(
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
        current = await self.db.get(WorkflowToolModel, tool_id)
        if current is None:
            raise WorkflowToolNotFound
        published = await self._published(AppId(current.app_id))
        if published is None:
            raise ValueError("Publish the workflow before updating its tool")
        self.validate_definition(name=name, label=label, parameters=parameters)
        if (
            await self.db.scalar(
                select(WorkflowToolModel.id).where(
                    WorkflowToolModel.name == name,
                    WorkflowToolModel.id != tool_id,
                )
            )
            is not None
        ):
            raise WorkflowToolConflict("A workflow tool with this name already exists")
        current.published_workflow_id = published.id
        current.name = name
        current.label = label
        current.description = description
        current.icon_json = json.dumps(icon)
        current.parameters_json = json.dumps(parameters)
        current.labels_json = json.dumps(labels)
        current.privacy_policy = privacy_policy
        current.updated_at = int(time.time())
        await self.db.flush()
        updated = self._from_model(current)
        await self.db.commit()
        return updated

    async def delete(self, tool_id: str) -> bool:
        result = await self.db.execute(delete(WorkflowToolModel).where(WorkflowToolModel.id == tool_id))
        await self.db.commit()
        return result.rowcount == 1

    async def runtime_tool(self, provider_id: str, tool_name: str, caller_app_id: AppId) -> WorkflowTool:
        model = await self.db.get(WorkflowToolModel, provider_id)
        caller = await self.db.get(App, caller_app_id)
        if model is None or model.name != tool_name or caller is None:
            raise ToolNodeError("The selected workflow tool is unavailable")
        return self._from_model(model)

    async def invoke(
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
        tool = await self.runtime_tool(provider_id, tool_name, caller_app_id)
        if call_depth >= MAX_WORKFLOW_CALL_DEPTH or tool.app_id in call_stack:
            raise ToolNodeError("Workflow tool call depth exceeded or a circular call was detected")
        declared = {str(parameter["name"]): parameter for parameter in tool.parameters}
        values = {name: inputs[name] for name in declared if name in inputs}
        for name, parameter in declared.items():
            if parameter.get("required") and (name not in values or values[name] is None or values[name] == ""):
                raise ToolNodeError(f"Required workflow tool input is missing: {name}")
        session = await self.runs.start_published(
            tool.app_id,
            values,
            actor,
            call_depth=call_depth + 1,
            call_stack=call_stack,
        )
        return await asyncio.to_thread(self._wait_for_result, session)

    def runtime_tool_sync(self, provider_id: str, tool_name: str, caller_app_id: AppId) -> WorkflowTool:
        return self._isolated(lambda service: service.runtime_tool(provider_id, tool_name, caller_app_id))

    def invoke_sync(self, **kwargs: Any) -> dict[str, Any]:
        return self._isolated(lambda service: service.invoke(**kwargs))

    def _isolated(self, operation: Callable[[WorkflowToolService], Coroutine[Any, Any, Any]]) -> Any:
        coordinator = self.runs.coordinator
        if self.runs.session_factory is None:
            raise ToolNodeError("Workflow tool execution requires a session factory")

        async def execute() -> Any:
            from .run_service import RunService

            engine, session_factory = self.runs.isolated_session_factory()
            try:
                async with session_factory() as db:
                    runs = RunService(db, session_factory=session_factory, coordinator=coordinator)
                    return await operation(WorkflowToolService(db, runs))
            finally:
                if engine is not None:
                    await engine.dispose()

        with ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(lambda: asyncio.run(execute())).result()

    @staticmethod
    def _wait_for_result(session: RunSession) -> dict[str, Any]:
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
        tool = self.service.runtime_tool_sync(node_data.provider_id, node_data.tool_name, self.caller_app_id)
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
        outputs = self.service.invoke_sync(
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
