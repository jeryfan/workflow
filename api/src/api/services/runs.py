from __future__ import annotations

import json
import time
from dataclasses import asdict
from datetime import UTC, datetime

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.entities import (
    Actor,
    AppId,
    ContainerEvent,
    DraftHash,
    ExecutionEvent,
    GraphDocument,
    NodeExecutionRecord,
    NodeFinished,
    NodeStarted,
    RunFailed,
    RunIdentity,
    RunPage,
    RunPartiallySucceeded,
    RunRecord,
    RunSnapshot,
    RunStopped,
    RunSucceeded,
    TerminalStateConflict,
    WorkflowFinished,
    WorkflowId,
    WorkflowPaused,
    WorkflowStarted,
)
from api.models.execution import NodeExecution, WorkflowRun

from .base_service import BaseService


class RunHistoryService(BaseService):
    def __init__(self, db: AsyncSession) -> None:
        super().__init__(db)

    async def create(self, snapshot: RunSnapshot) -> None:
        identity = snapshot.identity
        self.db.add(
            WorkflowRun(
                id=identity.workflow_run_id,
                task_id=identity.task_id,
                app_id=identity.app_id,
                workflow_id=identity.workflow_id,
                draft_hash=snapshot.draft_hash,
                graph_snapshot_json=self._json(snapshot.graph.to_json_object()),
                inputs_json=self._json(snapshot.inputs),
                status="running",
                outputs_json="{}",
                error=None,
                elapsed_time=0.0,
                total_tokens=0,
                total_steps=0,
                exceptions_count=0,
                created_by_json=self._json(asdict(snapshot.actor)),
                created_at=self._timestamp(snapshot.created_at),
                finished_at=None,
                root_node_id=snapshot.root_node_id,
                call_depth=snapshot.call_depth,
                call_stack_json=self._json(snapshot.call_stack),
            )
        )
        await self.db.commit()

    async def project(self, event: ExecutionEvent) -> None:
        if isinstance(event, WorkflowStarted | ContainerEvent):
            return
        if isinstance(event, NodeStarted):
            await self._node_started(event)
            return
        if isinstance(event, NodeFinished):
            await self._node_finished(event)
            return
        if isinstance(event, WorkflowPaused):
            await self._workflow_paused(event)
            return
        await self._workflow_finished(event)

    async def _node_started(self, event: NodeStarted) -> None:
        existing = await self.db.get(NodeExecution, event.execution_id)
        if existing is None:
            self.db.add(
                NodeExecution(
                    id=event.execution_id,
                    run_id=event.identity.workflow_run_id,
                    sequence=event.sequence,
                    node_id=event.node_id,
                    node_type=event.node_type,
                    title=event.title,
                    index=event.index,
                    predecessor_node_id=event.predecessor_node_id,
                    inputs_json="{}",
                    process_data_json="{}",
                    outputs_json="{}",
                    status="running",
                    error=None,
                    elapsed_time=0.0,
                    created_at=self._timestamp(event.created_at),
                    finished_at=None,
                )
            )
        await self.db.commit()

    async def _node_finished(self, event: NodeFinished) -> None:
        result = await self.db.execute(
            update(NodeExecution)
            .where(NodeExecution.id == event.execution_id)
            .values(
                sequence=event.sequence,
                inputs_json=self._json(event.inputs),
                process_data_json=self._json(event.process_data),
                outputs_json=self._json(event.outputs),
                status=event.status,
                error=event.error,
                elapsed_time=event.elapsed_time,
                finished_at=self._timestamp(event.finished_at),
            )
        )
        if result.rowcount != 1:
            await self.db.rollback()
            raise RuntimeError(f"Node execution was not started: {event.execution_id}")
        await self.db.commit()

    async def _workflow_paused(self, event: WorkflowPaused) -> None:
        result = await self.db.execute(
            update(WorkflowRun)
            .where(WorkflowRun.id == event.identity.workflow_run_id, WorkflowRun.status == "running")
            .values(
                status="paused",
                runtime_state_json=event.runtime_state,
                outputs_json=self._json(event.outputs),
                elapsed_time=event.elapsed_time,
                total_tokens=event.total_tokens,
                total_steps=event.total_steps,
                finished_at=None,
            )
        )
        if result.rowcount != 1:
            raise TerminalStateConflict
        await self.db.commit()

    async def _workflow_finished(self, event: WorkflowFinished) -> None:
        status, outputs, error, exceptions_count = self._outcome_values(event)
        result = await self.db.execute(
            update(WorkflowRun)
            .where(WorkflowRun.id == event.identity.workflow_run_id, WorkflowRun.status == "running")
            .values(
                status=status,
                outputs_json=self._json(outputs),
                error=error,
                elapsed_time=event.elapsed_time,
                total_tokens=event.total_tokens,
                total_steps=event.total_steps,
                exceptions_count=exceptions_count,
                runtime_state_json=None,
                finished_at=self._timestamp(event.finished_at),
            )
        )
        if result.rowcount == 1:
            await self.db.commit()
            return
        existing = (
            await self.db.execute(
                select(
                    WorkflowRun.status,
                    WorkflowRun.outputs_json,
                    WorkflowRun.error,
                    WorkflowRun.elapsed_time,
                    WorkflowRun.total_tokens,
                    WorkflowRun.total_steps,
                    WorkflowRun.exceptions_count,
                    WorkflowRun.finished_at,
                ).where(WorkflowRun.id == event.identity.workflow_run_id)
            )
        ).first()
        expected = (
            status,
            self._json(outputs),
            error,
            event.elapsed_time,
            event.total_tokens,
            event.total_steps,
            exceptions_count,
            self._timestamp(event.finished_at),
        )
        if existing is None or tuple(existing) != expected:
            await self.db.rollback()
            raise TerminalStateConflict
        await self.db.commit()

    async def list(
        self,
        app_id: AppId,
        limit: int,
        last_id: str | None,
        status: str | None,
    ) -> RunPage:
        statement = select(WorkflowRun).where(WorkflowRun.app_id == app_id)
        if status is not None:
            statement = statement.where(WorkflowRun.status == status)
        if last_id is not None:
            cursor = (
                await self.db.execute(
                    select(WorkflowRun.created_at, WorkflowRun.id).where(
                        WorkflowRun.id == last_id,
                        WorkflowRun.app_id == app_id,
                    )
                )
            ).first()
            if cursor is None:
                return RunPage(records=(), limit=limit, has_more=False)
            statement = statement.where(
                or_(
                    WorkflowRun.created_at < cursor.created_at,
                    and_(WorkflowRun.created_at == cursor.created_at, WorkflowRun.id < cursor.id),
                )
            )
        rows = (
            (
                await self.db.execute(
                    statement.order_by(WorkflowRun.created_at.desc(), WorkflowRun.id.desc()).limit(limit + 1)
                )
            )
            .scalars()
            .all()
        )
        return RunPage(
            records=tuple(self._run_record(row) for row in rows[:limit]),
            limit=limit,
            has_more=len(rows) > limit,
        )

    async def get(self, app_id: AppId, run_id: str) -> RunRecord | None:
        row = await self.db.scalar(select(WorkflowRun).where(WorkflowRun.id == run_id, WorkflowRun.app_id == app_id))
        return self._run_record(row) if row is not None else None

    async def list_nodes(self, app_id: AppId, run_id: str) -> tuple[NodeExecutionRecord, ...]:
        rows = (
            (
                await self.db.execute(
                    select(NodeExecution)
                    .join(WorkflowRun, WorkflowRun.id == NodeExecution.run_id)
                    .where(WorkflowRun.id == run_id, WorkflowRun.app_id == app_id)
                    .order_by(NodeExecution.sequence)
                )
            )
            .scalars()
            .all()
        )
        return tuple(self._node_execution_record(row) for row in rows)

    async def latest_node(
        self,
        app_id: AppId,
        workflow_id: WorkflowId,
        node_id: str,
    ) -> NodeExecutionRecord | None:
        row = await self.db.scalar(
            select(NodeExecution)
            .join(WorkflowRun, WorkflowRun.id == NodeExecution.run_id)
            .where(
                WorkflowRun.app_id == app_id,
                WorkflowRun.workflow_id == workflow_id,
                NodeExecution.node_id == node_id,
            )
            .order_by(NodeExecution.created_at.desc(), NodeExecution.sequence.desc())
            .limit(1)
        )
        return self._node_execution_record(row) if row is not None else None

    async def snapshot(self, run_id: str) -> RunSnapshot | None:
        row = await self.db.get(WorkflowRun, run_id)
        if row is None:
            return None
        record = self._run_record(row)
        return RunSnapshot(
            identity=record.identity,
            graph=record.graph,
            draft_hash=DraftHash(row.draft_hash),
            inputs=record.inputs,
            actor=record.actor,
            created_at=record.created_at,
            call_depth=record.call_depth,
            call_stack=record.call_stack,
            root_node_id=record.root_node_id,
        )

    async def paused_snapshot(self, run_id: str) -> tuple[RunSnapshot, str] | None:
        row = await self.db.get(WorkflowRun, run_id)
        if row is None or row.status != "paused" or row.runtime_state_json is None:
            return None
        snapshot = await self.snapshot(run_id)
        return (snapshot, row.runtime_state_json) if snapshot is not None else None

    async def mark_resuming(self, run_id: str) -> bool:
        result = await self.db.execute(
            update(WorkflowRun).where(WorkflowRun.id == run_id, WorkflowRun.status == "paused").values(status="running")
        )
        if result.rowcount != 1:
            return False
        await self.db.commit()
        return True

    async def runtime_state(self, run_id: str) -> str | None:
        return await self.db.scalar(select(WorkflowRun.runtime_state_json).where(WorkflowRun.id == run_id))

    async def next_sequence(self, run_id: str) -> int:
        sequence = await self.db.scalar(
            select(NodeExecution.sequence)
            .where(NodeExecution.run_id == run_id)
            .order_by(NodeExecution.sequence.desc())
            .limit(1)
        )
        return 0 if sequence is None else sequence + 1

    async def identity(self, app_id: AppId, task_id: str) -> RunIdentity | None:
        row = await self.db.scalar(
            select(WorkflowRun).where(WorkflowRun.app_id == app_id, WorkflowRun.task_id == task_id)
        )
        if row is None:
            return None
        return RunIdentity(
            app_id=row.app_id,
            workflow_id=row.workflow_id,
            workflow_run_id=row.id,
            task_id=row.task_id,
        )

    async def fail_interrupted(self, reason: str) -> int:
        result = await self.db.execute(
            update(WorkflowRun)
            .where(WorkflowRun.status == "running")
            .values(status="failed", error=reason, finished_at=int(time.time()))
        )
        await self.db.commit()
        return result.rowcount

    @staticmethod
    def _run_record(row: WorkflowRun) -> RunRecord:
        return RunRecord(
            identity=RunIdentity(
                app_id=row.app_id,
                workflow_id=row.workflow_id,
                workflow_run_id=row.id,
                task_id=row.task_id,
            ),
            graph=GraphDocument.parse(json.loads(row.graph_snapshot_json)),
            inputs=json.loads(row.inputs_json),
            status=row.status,
            outputs=json.loads(row.outputs_json),
            error=row.error,
            elapsed_time=row.elapsed_time,
            total_tokens=row.total_tokens,
            total_steps=row.total_steps,
            exceptions_count=row.exceptions_count,
            actor=Actor(**json.loads(row.created_by_json)),
            created_at=datetime.fromtimestamp(row.created_at, UTC),
            finished_at=datetime.fromtimestamp(row.finished_at, UTC) if row.finished_at is not None else None,
            call_depth=row.call_depth,
            call_stack=tuple(json.loads(row.call_stack_json)),
            root_node_id=row.root_node_id,
        )

    @staticmethod
    def _node_execution_record(row: NodeExecution) -> NodeExecutionRecord:
        return NodeExecutionRecord(
            id=row.id,
            node_id=row.node_id,
            node_type=row.node_type,
            title=row.title,
            index=row.index,
            predecessor_node_id=row.predecessor_node_id,
            inputs=json.loads(row.inputs_json),
            process_data=json.loads(row.process_data_json),
            outputs=json.loads(row.outputs_json),
            status=row.status,
            error=row.error,
            elapsed_time=row.elapsed_time,
            created_at=datetime.fromtimestamp(row.created_at, UTC),
            finished_at=datetime.fromtimestamp(row.finished_at, UTC) if row.finished_at is not None else None,
        )

    @staticmethod
    def _outcome_values(event: WorkflowFinished) -> tuple[str, dict, str | None, int]:
        outcome = event.outcome
        if isinstance(outcome, RunSucceeded):
            return "succeeded", dict(outcome.outputs), None, outcome.exceptions_count
        if isinstance(outcome, RunPartiallySucceeded):
            return "partial-succeeded", dict(outcome.outputs), None, outcome.exceptions_count
        if isinstance(outcome, RunFailed):
            return "failed", dict(outcome.outputs), outcome.error, outcome.exceptions_count
        if isinstance(outcome, RunStopped):
            return "stopped", dict(outcome.outputs), outcome.reason, 0
        raise TypeError(f"Unknown run outcome: {type(outcome).__name__}")

    @staticmethod
    def _json(value) -> str:
        from .run_messages import encode_value

        return json.dumps(encode_value(value), ensure_ascii=False)

    @staticmethod
    def _timestamp(value: datetime) -> int:
        return int(value.timestamp())
