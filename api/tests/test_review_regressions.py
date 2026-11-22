import asyncio
import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

import httpx
from auth_helpers import authenticated_client
from fastapi import Depends
from service_helpers import create_test_database
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies.db import get_db
from api.dependencies.workflows import get_run_service
from api.models.entities import (
    ExecutionEvent,
    GraphDocument,
    NodeStarted,
    RunSnapshot,
    RunStopped,
    SaveDraft,
    WorkflowFinished,
    WorkflowStarted,
)
from api.services.run_service import RunService
from api.services.runs import RunHistoryService
from api.services.workflows import LOCAL_ACTOR, WorkflowService

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads((ROOT / "contracts/fixtures/reference-workflow.json").read_text())
APP_ID = FIXTURE["app_id"]


class ProjectionFailureHistory(RunHistoryService):
    def __init__(self, db) -> None:
        super().__init__(db)
        self.failed_once = False

    async def project(self, event: ExecutionEvent) -> None:
        if isinstance(event, NodeStarted) and not self.failed_once:
            self.failed_once = True
            raise RuntimeError("projection failed")
        await super().project(event)


class ProjectionFailureExecution:
    def __init__(self, snapshot: RunSnapshot) -> None:
        self.snapshot = snapshot

    def events(self):
        yield WorkflowStarted(
            identity=self.snapshot.identity,
            sequence=0,
            inputs=self.snapshot.inputs,
            created_at=self.snapshot.created_at,
        )
        yield NodeStarted(
            identity=self.snapshot.identity,
            sequence=1,
            execution_id="execution-1",
            node_id="start",
            node_type="start",
            title="Start",
            index=1,
            predecessor_node_id=None,
            created_at=self.snapshot.created_at,
        )

    def request_stop(self, reason: str) -> None:
        return


class ProjectionFailureEngine:
    def start(self, snapshot: RunSnapshot) -> ProjectionFailureExecution:
        return ProjectionFailureExecution(snapshot)


class BlockingExecution:
    def __init__(self, snapshot: RunSnapshot) -> None:
        self.snapshot = snapshot
        self.stop_requested = threading.Event()
        self.waiting_for_stop = threading.Event()
        self.allow_finish = threading.Event()

    def events(self):
        yield WorkflowStarted(
            identity=self.snapshot.identity,
            sequence=0,
            inputs=self.snapshot.inputs,
            created_at=self.snapshot.created_at,
        )
        self.waiting_for_stop.set()
        self.stop_requested.wait(1)
        self.allow_finish.wait(1)
        yield WorkflowFinished(
            identity=self.snapshot.identity,
            sequence=1,
            outcome=RunStopped(outputs={}, reason="test stop"),
            elapsed_time=0.01,
            total_tokens=0,
            total_steps=0,
            created_at=self.snapshot.created_at,
            finished_at=datetime.now(UTC),
        )

    def request_stop(self, reason: str) -> None:
        self.stop_requested.set()


class BlockingEngine:
    def __init__(self) -> None:
        self.execution: BlockingExecution | None = None

    def start(self, snapshot: RunSnapshot) -> BlockingExecution:
        self.execution = BlockingExecution(snapshot)
        return self.execution


def save_command() -> SaveDraft:
    return SaveDraft(
        app_id=APP_ID,
        graph=GraphDocument.parse(FIXTURE["graph"]),
        features={},
        conversation_variables=(),
        expected_hash=None,
        force=False,
        actor=LOCAL_ACTOR,
    )


def test_empty_graph_requires_force_even_when_fields_are_missing(tmp_path: Path) -> None:
    with authenticated_client(tmp_path) as client:
        for graph in ({}, {"nodes": []}, {"edges": []}):
            response = client.post(
                f"/console/api/apps/{APP_ID}/workflows/draft",
                json={"graph": graph, "features": {}, "hash": None},
            )
            assert response.status_code == 400


def test_projection_failure_finishes_the_stream_and_marks_the_run_failed(tmp_path: Path, monkeypatch) -> None:
    async def exercise() -> None:
        engine, session_factory = await create_test_database(f"sqlite:///{tmp_path / 'workflow.db'}", (APP_ID,))
        try:
            monkeypatch.setattr("api.services.run_service.RunHistoryService", ProjectionFailureHistory)
            async with session_factory() as db:
                await WorkflowService(db).save_draft(save_command())
                service = RunService(db, ProjectionFailureEngine(), session_factory=session_factory)
                session = await service.start_draft(APP_ID, FIXTURE["inputs"])
                events = await asyncio.to_thread(lambda: list(session.events()))
                await service.close()

            assert isinstance(events[-1], WorkflowFinished)
            assert events[-1].outcome.error == "projection failed"
            with sqlite3.connect(tmp_path / "workflow.db") as connection:
                status = connection.execute("SELECT status FROM workflow_runs").fetchone()[0]
            assert status == "failed"
        finally:
            await engine.dispose()

    asyncio.run(exercise())


def test_close_waits_for_the_consumer_to_persist_the_stop(tmp_path: Path) -> None:
    async def exercise() -> None:
        engine, session_factory = await create_test_database(f"sqlite:///{tmp_path / 'workflow.db'}", (APP_ID,))
        try:
            async with session_factory() as db:
                await WorkflowService(db).save_draft(save_command())
                execution_engine = BlockingEngine()
                service = RunService(db, execution_engine, session_factory=session_factory)
                session = await service.start_draft(APP_ID, FIXTURE["inputs"])
                consumed = []
                consumer = threading.Thread(target=lambda: consumed.extend(session.events()))
                consumer.start()
                execution = execution_engine.execution
                assert execution is not None
                assert execution.waiting_for_stop.wait(1)
                closing = threading.Thread(target=lambda: asyncio.run(service.close()))
                closing.start()
                assert execution.stop_requested.wait(1)

                waited_while_running = closing.is_alive()
                execution.allow_finish.set()
                closing.join(1)
                consumer.join(1)

                assert waited_while_running
                assert not closing.is_alive()
                assert isinstance(consumed[-1], WorkflowFinished)
                with sqlite3.connect(tmp_path / "workflow.db") as connection:
                    status = connection.execute("SELECT status FROM workflow_runs").fetchone()[0]
                assert status == "stopped"
        finally:
            await engine.dispose()

    asyncio.run(exercise())


def test_console_and_public_stop_apis_stop_an_active_run(tmp_path: Path) -> None:
    with authenticated_client(tmp_path) as client:
        saved = client.post(
            f"/console/api/apps/{APP_ID}/workflows/draft",
            json={"graph": FIXTURE["graph"], "features": {}, "conversation_variables": [], "hash": None},
        )
        assert saved.status_code == 200, saved.text
        published = client.post(f"/console/api/apps/{APP_ID}/workflows/publish", json={})
        assert published.status_code == 200, published.text
        token = client.get(f"/console/api/apps/{APP_ID}").json()["site"]["access_token"]

        async def exercise(http: httpx.AsyncClient, public: bool) -> None:
            engine = BlockingEngine()

            def run_service(db: AsyncSession = Depends(get_db)) -> RunService:
                return RunService(
                    db,
                    engine,
                    session_factory=client.app.state.session_factory,
                    coordinator=client.app.state.run_coordinator,
                )

            client.app.dependency_overrides[get_run_service] = run_service
            path = "/api/workflows/run" if public else f"/console/api/apps/{APP_ID}/workflows/draft/run"
            headers = {"X-App-Code": token} if public else {}
            payload = {"inputs": FIXTURE["inputs"], "files": []}
            if public:
                payload["response_mode"] = "streaming"
            running = asyncio.create_task(http.post(path, headers=headers, json=payload))
            execution = None
            try:
                for _ in range(100):
                    execution = engine.execution
                    if execution is not None and execution.waiting_for_stop.is_set():
                        break
                    await asyncio.sleep(0.02)
                assert execution is not None and execution.waiting_for_stop.is_set()
                async with client.app.state.session_factory() as db:
                    page = await RunHistoryService(db).list(APP_ID, 1, None, None)
                record = page.records[0]
                stop_path = (
                    f"/api/workflows/tasks/{record.identity.task_id}/stop"
                    if public
                    else f"/console/api/apps/{APP_ID}/workflow-runs/tasks/{record.identity.task_id}/stop"
                )
                stopped = await http.post(stop_path, headers=headers)
                assert stopped.status_code == 200, stopped.text
                assert stopped.json() == {"result": "success"}
                assert execution.stop_requested.wait(1)
            finally:
                if execution is not None:
                    execution.allow_finish.set()
            response = await running
            assert response.status_code == 200
            events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
            assert events[-1]["event"] == "workflow_finished"
            assert events[-1]["data"]["status"] == "stopped"

        async def run_both() -> None:
            try:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=client.app),
                    base_url="http://testserver",
                    cookies=client.cookies,
                    headers={"X-CSRF-Token": client.headers["X-CSRF-Token"]},
                ) as http:
                    await exercise(http, False)
                    await exercise(http, True)
            finally:
                client.app.dependency_overrides.clear()

        asyncio.run(run_both())
