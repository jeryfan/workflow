from __future__ import annotations

import json
import time
from collections.abc import Mapping
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from sqlalchemy import JSON, cast, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.configs import PROJECT_ROOT
from api.models import Account, App, WorkflowDraft, WorkflowTool, WorkflowVersion
from api.models.entities import (
    Actor,
    AppId,
    Draft,
    DraftConflict,
    DraftHash,
    EnvironmentVariablePatch,
    GraphDocument,
    PublishDraft,
    PublishedWorkflow,
    SaveDraft,
    WorkflowId,
    generate_draft_hash,
    merge_environment_variables,
)

from .base_service import BaseService

if TYPE_CHECKING:
    pass

LOCAL_ACTOR = Actor(id="local-user", name="Local User", email="local@example.invalid")


class DraftNotFound(Exception):
    pass


class InvalidWorkflow(Exception):
    pass


def draft_from_model(model: WorkflowDraft) -> Draft:
    return Draft(
        app_id=AppId(model.app_id),
        workflow_id=WorkflowId(model.workflow_id),
        graph=GraphDocument.parse(json.loads(model.graph_json)),
        features=json.loads(model.features_json),
        environment_variables=tuple(json.loads(model.environment_variables_json)),
        conversation_variables=tuple(json.loads(model.conversation_variables_json)),
        hash=DraftHash(model.hash),
        created_at=datetime.fromtimestamp(model.created_at, UTC),
        updated_at=datetime.fromtimestamp(model.updated_at, UTC),
        created_by=Actor(**json.loads(model.created_by_json)),
        updated_by=Actor(**json.loads(model.updated_by_json)),
    )


def published_workflow_from_model(model: WorkflowVersion) -> PublishedWorkflow:
    return PublishedWorkflow(
        id=model.id,
        app_id=AppId(model.app_id),
        version_number=model.version_number,
        graph=GraphDocument.parse(json.loads(model.graph_json)),
        features=json.loads(model.features_json),
        environment_variables=tuple(json.loads(model.environment_variables_json)),
        conversation_variables=tuple(json.loads(model.conversation_variables_json)),
        hash=DraftHash(model.hash),
        marked_name=model.marked_name,
        marked_comment=model.marked_comment,
        created_at=datetime.fromtimestamp(model.created_at, UTC),
        created_by=Actor(**json.loads(model.created_by_json)),
    )


class WorkflowService(BaseService):
    def __init__(self, db: AsyncSession, node_contract_path: Path | None = None) -> None:
        super().__init__(db)
        contract_path = node_contract_path or PROJECT_ROOT / "contracts" / "nodes.json"
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        self._nodes = tuple(contract["nodes"])
        self._allowed_types = frozenset(node["type"] for node in self._nodes if node["execution_class"] is not None)

    async def _draft_model(self, app_id: AppId, *, populate_existing: bool = False) -> WorkflowDraft | None:
        statement = select(WorkflowDraft).where(WorkflowDraft.app_id == app_id)
        if populate_existing:
            statement = statement.execution_options(populate_existing=True)
        return await self.db.scalar(statement)

    async def get_draft(self, app_id: AppId) -> Draft:
        draft = await self._draft_model(app_id)
        if draft is None:
            raise DraftNotFound
        return draft_from_model(draft)

    async def save_draft(self, command: SaveDraft) -> Draft:
        await self.validate_graph(command.graph, force=command.force)
        if command.environment_variables is not None and command.environment_variable_patch is not None:
            raise ValueError("Environment variable replacement and patch cannot be combined")
        new_hash = generate_draft_hash(command.graph)
        now = int(time.time())
        actor_json = self._json(asdict(command.actor))
        try:
            current = (
                await self.db.execute(
                    select(WorkflowDraft.hash, WorkflowDraft.environment_variables_json).where(
                        WorkflowDraft.app_id == command.app_id
                    )
                )
            ).first()
            if current is None:
                if command.expected_hash is not None:
                    raise DraftConflict
                if command.environment_variables is not None:
                    environment_variables = command.environment_variables
                elif command.environment_variable_patch is not None:
                    environment_variables = merge_environment_variables((), command.environment_variable_patch)
                else:
                    environment_variables = ()
                self.db.add(
                    WorkflowDraft(
                        app_id=command.app_id,
                        workflow_id=str(uuid4()),
                        graph_json=self._json(command.graph.to_json_object()),
                        features_json=self._json(command.features),
                        environment_variables_json=self._json(environment_variables),
                        conversation_variables_json=self._json(command.conversation_variables),
                        hash=new_hash,
                        created_at=now,
                        updated_at=now,
                        created_by_json=actor_json,
                        updated_by_json=actor_json,
                    )
                )
            else:
                if current.hash != command.expected_hash:
                    raise DraftConflict
                environment_variables = tuple(json.loads(current.environment_variables_json))
                if command.environment_variables is not None:
                    environment_variables = command.environment_variables
                elif command.environment_variable_patch is not None:
                    environment_variables = merge_environment_variables(
                        environment_variables, command.environment_variable_patch
                    )
                result = await self.db.execute(
                    update(WorkflowDraft)
                    .where(WorkflowDraft.app_id == command.app_id)
                    .where(WorkflowDraft.hash == command.expected_hash)
                    .values(
                        graph_json=self._json(command.graph.to_json_object()),
                        features_json=self._json(command.features),
                        environment_variables_json=self._json(environment_variables),
                        conversation_variables_json=self._json(command.conversation_variables),
                        hash=new_hash,
                        updated_at=now,
                        updated_by_json=actor_json,
                    )
                )
                if result.rowcount != 1:
                    raise DraftConflict
            app_values = {
                "updated_at": now,
                "updated_by_id": select(Account.id).where(Account.id == command.actor.id).scalar_subquery(),
            }
            if command.app_name is not None:
                app_values["name"] = command.app_name
            await self.db.execute(update(App).where(App.id == command.app_id).values(**app_values))
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise
        draft = await self._draft_model(command.app_id, populate_existing=True)
        if draft is None:
            raise RuntimeError("Saved draft could not be read")
        return draft_from_model(draft)

    async def validate_graph(self, graph_document: GraphDocument, *, force: bool = False) -> None:
        graph = graph_document.to_json_object()
        if not graph.get("nodes", []) and not graph.get("edges", []) and not force:
            raise InvalidWorkflow("Saving an empty workflow requires force=true")
        for node_id, node in graph_document.nodes_by_id.items():
            node_type = node["data"]["type"]
            if node_type not in self._allowed_types:
                raise InvalidWorkflow(f"Unsupported node type: {node_type} at {node_id}")
            if node_type == "tool":
                data = node["data"]
                provider_id = data.get("provider_id")
                tool_name = data.get("tool_name")
                if data.get("provider_type") != "workflow":
                    raise InvalidWorkflow(f"Only workflow tools are supported at {node_id}")
                if (
                    not isinstance(provider_id, str)
                    or not provider_id
                    or not isinstance(tool_name, str)
                    or not tool_name
                ):
                    raise InvalidWorkflow(f"Tool node requires provider_id and tool_name at {node_id}")
                tool = await self.db.get(WorkflowTool, provider_id)
                if tool is None or tool.name != tool_name:
                    raise InvalidWorkflow(f"Workflow tool is unavailable at {node_id}")

    def default_configs(self) -> list[dict[str, Any]]:
        return [
            {"type": node["type"], "config": node["default_config"]}
            for node in self._nodes
            if node["execution_class"] is not None
        ]

    def default_config(self, node_type: str, query: str | None) -> dict[str, Any]:
        if query:
            parsed = json.loads(query)
            if not isinstance(parsed, dict):
                raise InvalidWorkflow("Default node config query must be a JSON object")
        for item in self.default_configs():
            if item["type"] == node_type:
                return item["config"]
        raise InvalidWorkflow(f"Unsupported node type: {node_type}")

    @staticmethod
    def _normalize_environment_variable(variable: Mapping[str, Any]) -> dict[str, Any]:
        variable_id = variable.get("id")
        if variable_id is not None and (not isinstance(variable_id, str) or not variable_id):
            raise ValueError("Environment variable id must be a non-empty string")
        name = variable.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("Environment variable name must not be empty")
        value_type = variable.get("value_type")
        if value_type not in {"string", "number", "secret", "llm"}:
            raise ValueError(f"Unsupported environment variable type: {value_type}")
        return {
            "id": variable_id or str(uuid4()),
            "name": name,
            "value_type": value_type,
            "value": variable.get("value"),
            "description": variable.get("description") or "",
        }

    @staticmethod
    def normalize_environment_variables(variables: list[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
        normalized = tuple(WorkflowService._normalize_environment_variable(variable) for variable in variables)
        ids = [variable["id"] for variable in normalized]
        names = [variable["name"] for variable in normalized]
        if len(set(ids)) != len(ids) or len(set(names)) != len(names):
            raise ValueError("Environment variable ids and names must be unique")
        return normalized

    @staticmethod
    def _environment_patch(variables: list[Mapping[str, Any]], deleted_ids: list[str]) -> EnvironmentVariablePatch:
        if any(not variable.get("id") for variable in variables):
            raise ValueError("Patched environment variables require an id")
        return EnvironmentVariablePatch(
            upserts=tuple(WorkflowService._normalize_environment_variable(variable) for variable in variables),
            deleted_ids=tuple(deleted_ids),
        )

    async def update_environment_variables(
        self,
        app_id: AppId,
        variables: list[dict[str, Any]],
        deleted_ids: list[str],
        patch: bool,
        actor: Actor,
    ) -> None:
        if deleted_ids and not patch:
            raise ValueError("deleted_environment_variable_ids requires patch=true")
        normalized = self.normalize_environment_variables(variables)
        command = (
            self._environment_patch(variables, deleted_ids)
            if patch
            else EnvironmentVariablePatch(upserts=normalized, deleted_ids=())
        )
        try:
            current = await self.db.scalar(
                select(WorkflowDraft.environment_variables_json).where(WorkflowDraft.app_id == app_id)
            )
            if current is None:
                raise DraftNotFound
            existing = tuple(json.loads(current))
            updated = merge_environment_variables(existing, command) if patch else command.upserts
            result = await self.db.execute(
                update(WorkflowDraft)
                .where(WorkflowDraft.app_id == app_id)
                .values(
                    environment_variables_json=self._json(updated),
                    updated_at=int(time.time()),
                    updated_by_json=self._json(asdict(actor)),
                )
            )
            if result.rowcount != 1:
                raise DraftNotFound
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise

    async def get_published(self, app_id: AppId) -> PublishedWorkflow | None:
        workflow = await self.db.scalar(
            select(WorkflowVersion).join(App, App.published_workflow_id == WorkflowVersion.id).where(App.id == app_id)
        )
        return published_workflow_from_model(workflow) if workflow is not None else None

    async def get_workflow_version(self, app_id: AppId, workflow_id: str) -> PublishedWorkflow | None:
        workflow = await self.db.scalar(
            select(WorkflowVersion).where(
                WorkflowVersion.app_id == app_id,
                WorkflowVersion.id == workflow_id,
            )
        )
        return published_workflow_from_model(workflow) if workflow is not None else None

    async def publish_draft(self, command: PublishDraft) -> PublishedWorkflow | None:
        try:
            draft = await self._draft_model(command.app_id)
            if draft is None:
                return None
            version_number = await self.db.scalar(
                select(WorkflowVersion.version_number)
                .where(WorkflowVersion.app_id == command.app_id)
                .order_by(WorkflowVersion.version_number.desc())
                .limit(1)
            )
            now = int(time.time())
            workflow = WorkflowVersion(
                id=str(uuid4()),
                app_id=command.app_id,
                version_number=0 if version_number is None else version_number + 1,
                graph_json=draft.graph_json,
                features_json=draft.features_json,
                environment_variables_json=draft.environment_variables_json,
                conversation_variables_json=draft.conversation_variables_json,
                hash=draft.hash,
                marked_name=command.marked_name,
                marked_comment=command.marked_comment,
                created_at=now,
                created_by_json=self._json(asdict(command.actor)),
            )
            self.db.add(workflow)
            await self.db.flush()
            await self.db.execute(
                update(App)
                .where(App.id == command.app_id)
                .values(
                    published_workflow_id=workflow.id,
                    updated_at=now,
                    updated_by_id=select(Account.id).where(Account.id == command.actor.id).scalar_subquery(),
                )
            )
            published = published_workflow_from_model(workflow)
            await self.db.commit()
            return published
        except Exception:
            await self.db.rollback()
            raise

    async def list_workflow_versions(
        self,
        app_id: AppId,
        page: int,
        limit: int,
        user_id: str | None,
        named_only: bool,
    ) -> tuple[tuple[Draft | PublishedWorkflow, ...], bool]:
        current_id = await self.db.scalar(select(App.published_workflow_id).where(App.id == app_id))
        if current_id is None:
            return (), False

        items: list[Draft | PublishedWorkflow] = []
        sqlite = self.db.bind.dialect.name == "sqlite"
        if not named_only:
            draft_statement = select(WorkflowDraft).where(WorkflowDraft.app_id == app_id)
            if user_id is not None:
                creator_id = (
                    func.json_extract(WorkflowDraft.created_by_json, "$.id")
                    if sqlite
                    else cast(WorkflowDraft.created_by_json, JSON).op("->>")("id")
                )
                draft_statement = draft_statement.where(creator_id == user_id)
            draft = await self.db.scalar(draft_statement)
            if draft is not None:
                items.append(draft_from_model(draft))

        version_statement = (
            select(WorkflowVersion)
            .where(WorkflowVersion.app_id == app_id)
            .order_by(
                WorkflowVersion.created_at.desc(),
                WorkflowVersion.version_number.desc(),
                WorkflowVersion.id.desc(),
            )
            .limit(page * limit + 1)
        )
        if user_id is not None:
            creator_id = (
                func.json_extract(WorkflowVersion.created_by_json, "$.id")
                if sqlite
                else cast(WorkflowVersion.created_by_json, JSON).op("->>")("id")
            )
            version_statement = version_statement.where(creator_id == user_id)
        if named_only:
            version_statement = version_statement.where(WorkflowVersion.marked_name != "")
        versions = (await self.db.scalars(version_statement)).all()
        items.extend(published_workflow_from_model(workflow) for workflow in versions)

        start = (page - 1) * limit
        selected = items[start : start + limit + 1]
        return tuple(selected[:limit]), len(selected) > limit

    async def update_workflow_version(
        self,
        app_id: AppId,
        workflow_id: str,
        marked_name: str | None,
        marked_comment: str | None,
    ) -> PublishedWorkflow | None:
        values = {}
        if marked_name is not None:
            values["marked_name"] = marked_name
        if marked_comment is not None:
            values["marked_comment"] = marked_comment
        try:
            result = await self.db.execute(
                update(WorkflowVersion)
                .where(WorkflowVersion.app_id == app_id, WorkflowVersion.id == workflow_id)
                .values(**values)
            )
            if result.rowcount != 1:
                await self.db.rollback()
                return None
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise
        workflow = await self.db.scalar(
            select(WorkflowVersion)
            .where(WorkflowVersion.app_id == app_id, WorkflowVersion.id == workflow_id)
            .execution_options(populate_existing=True)
        )
        return published_workflow_from_model(workflow) if workflow is not None else None

    async def restore_workflow_version(self, app_id: AppId, workflow_id: str, actor: Actor) -> Draft | None:
        try:
            source = await self.db.scalar(
                select(WorkflowVersion).where(
                    WorkflowVersion.app_id == app_id,
                    WorkflowVersion.id == workflow_id,
                )
            )
            if source is None:
                return None
            now = int(time.time())
            actor_json = self._json(asdict(actor))
            values = {
                "graph_json": source.graph_json,
                "features_json": source.features_json,
                "environment_variables_json": source.environment_variables_json,
                "conversation_variables_json": source.conversation_variables_json,
                "hash": generate_draft_hash(published_workflow_from_model(source).graph),
                "updated_at": now,
                "updated_by_json": actor_json,
            }
            result = await self.db.execute(update(WorkflowDraft).where(WorkflowDraft.app_id == app_id).values(**values))
            if result.rowcount == 0:
                self.db.add(
                    WorkflowDraft(
                        app_id=app_id,
                        workflow_id=str(uuid4()),
                        **values,
                        created_at=now,
                        created_by_json=actor_json,
                    )
                )
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise
        draft = await self._draft_model(app_id, populate_existing=True)
        return draft_from_model(draft) if draft is not None else None

    async def delete_workflow_version(
        self,
        app_id: AppId,
        workflow_id: str,
    ) -> Literal["deleted", "not_found", "current", "draft"]:
        current_id = await self.db.scalar(select(App.published_workflow_id).where(App.id == app_id))
        if current_id == workflow_id:
            return "current"
        version_id = await self.db.scalar(
            select(WorkflowVersion.id).where(
                WorkflowVersion.app_id == app_id,
                WorkflowVersion.id == workflow_id,
            )
        )
        if version_id is None:
            draft_id = await self.db.scalar(
                select(WorkflowDraft.workflow_id).where(
                    WorkflowDraft.app_id == app_id,
                    WorkflowDraft.workflow_id == workflow_id,
                )
            )
            return "draft" if draft_id is not None else "not_found"
        try:
            await self.db.execute(
                delete(WorkflowVersion).where(
                    WorkflowVersion.app_id == app_id,
                    WorkflowVersion.id == workflow_id,
                )
            )
            await self.db.commit()
            return "deleted"
        except Exception:
            await self.db.rollback()
            raise

    @staticmethod
    def parse_save_draft(app_id: str, payload: Any, actor: Actor) -> SaveDraft:
        patch = payload.environment_variable_patch
        environment_patch = (
            WorkflowService._environment_patch(
                [variable.model_dump() for variable in patch.environment_variables],
                patch.deleted_environment_variable_ids,
            )
            if patch is not None
            else None
        )
        return SaveDraft(
            app_id=AppId(app_id),
            graph=GraphDocument.parse(payload.graph),
            features=payload.features,
            conversation_variables=tuple(payload.conversation_variables),
            expected_hash=DraftHash(payload.hash) if payload.hash is not None else None,
            force=payload.force,
            actor=actor,
            environment_variable_patch=environment_patch,
        )

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False)
