import asyncio
from pathlib import Path

from service_helpers import create_test_database

from api.models.entities import GraphDocument, RunSucceeded, SaveDraft, WorkflowFinished
from api.services.engine import GraphonWorkflowEngine
from api.services.run_service import RunService
from api.services.workflows import LOCAL_ACTOR, WorkflowService


async def _run_graph(graph: dict, inputs: dict, database_path: Path) -> list:
    engine, session_factory = await create_test_database(f"sqlite:///{database_path}", ("app-1",))
    try:
        async with session_factory() as db:
            await WorkflowService(db).save_draft(
                SaveDraft(
                    app_id="app-1",
                    graph=GraphDocument.parse(graph),
                    features={},
                    conversation_variables=(),
                    expected_hash=None,
                    force=False,
                    actor=LOCAL_ACTOR,
                )
            )
            service = RunService(db, GraphonWorkflowEngine(), session_factory=session_factory)
            session = await service.start_draft("app-1", inputs)
            events = await asyncio.to_thread(lambda: list(session.events()))
            await service.close()
            return events
    finally:
        await engine.dispose()


def run_graph(graph: dict, inputs: dict, database_path: Path) -> list:
    return asyncio.run(_run_graph(graph, inputs, database_path))


def edge(source: str, target: str) -> dict:
    return {"source": source, "target": target}


def terminal_outputs(events: list) -> dict:
    terminal = events[-1]
    assert isinstance(terminal, WorkflowFinished)
    assert isinstance(terminal.outcome, RunSucceeded), terminal.outcome
    return dict(terminal.outcome.outputs)


def test_iteration_maps_each_item_and_collects_outputs(tmp_path: Path) -> None:
    graph = {
        "nodes": [
            {"id": "start", "data": {"type": "start", "title": "Start", "variables": []}},
            {
                "id": "iteration",
                "data": {
                    "type": "iteration",
                    "title": "For each item",
                    "iterator_selector": ["start", "items"],
                    "output_selector": ["render", "output"],
                    "start_node_id": "iteration-start",
                    "is_parallel": False,
                    "parallel_nums": 1,
                    "error_handle_mode": "terminated",
                    "flatten_output": True,
                },
            },
            {"id": "iteration-start", "data": {"type": "iteration-start", "title": "", "iteration_id": "iteration"}},
            {
                "id": "render",
                "data": {
                    "type": "template-transform",
                    "title": "Render",
                    "iteration_id": "iteration",
                    "variables": [{"variable": "item", "value_selector": ["iteration", "item"]}],
                    "template": "{{ item }}!",
                },
            },
            {
                "id": "end",
                "data": {
                    "type": "end",
                    "title": "End",
                    "outputs": [{"variable": "items", "value_selector": ["iteration", "output"]}],
                },
            },
        ],
        "edges": [edge("start", "iteration"), edge("iteration-start", "render"), edge("iteration", "end")],
    }

    events = run_graph(graph, {"items": ["a", "b"]}, tmp_path / "iteration.db")

    assert terminal_outputs(events) == {"items": ["a!", "b!"]}


def test_loop_updates_a_variable_until_its_break_condition(tmp_path: Path) -> None:
    graph = {
        "nodes": [
            {"id": "start", "data": {"type": "start", "title": "Start", "variables": []}},
            {
                "id": "loop",
                "data": {
                    "type": "loop",
                    "title": "Loop",
                    "loop_count": 5,
                    "start_node_id": "loop-start",
                    "break_conditions": [
                        {
                            "variable_selector": ["loop", "counter"],
                            "comparison_operator": "≥",
                            "value": "3",
                        }
                    ],
                    "logical_operator": "and",
                    "loop_variables": [
                        {"label": "counter", "var_type": "number", "value_type": "constant", "value": 0}
                    ],
                },
            },
            {"id": "loop-start", "data": {"type": "loop-start", "title": "", "container_id": "loop"}},
            {
                "id": "increment",
                "data": {
                    "type": "assigner",
                    "title": "Increment",
                    "version": "2",
                    "container_id": "loop",
                    "items": [
                        {
                            "variable_selector": ["loop", "counter"],
                            "input_type": "constant",
                            "operation": "+=",
                            "value": 1,
                        }
                    ],
                },
            },
            {
                "id": "end",
                "data": {
                    "type": "end",
                    "title": "End",
                    "outputs": [{"variable": "counter", "value_selector": ["loop", "counter"]}],
                },
            },
        ],
        "edges": [edge("start", "loop"), edge("loop-start", "increment"), edge("loop", "end")],
    }

    events = run_graph(graph, {}, tmp_path / "loop.db")

    assert terminal_outputs(events) == {"counter": 3}
