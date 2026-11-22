import json
from pathlib import Path

from auth_helpers import authenticated_client
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
REFERENCE = json.loads((ROOT / "contracts/fixtures/reference-workflow.json").read_text())
APP_ID = REFERENCE["app_id"]


def branch_graph(*, missing_selector: bool = False) -> dict:
    nodes = [
        {
            "id": "start",
            "type": "custom",
            "data": {
                "type": "start",
                "title": "Start",
                "variables": [
                    {
                        "variable": "enabled",
                        "label": "Enabled",
                        "type": "checkbox",
                        "required": True,
                        "options": [],
                    },
                    {
                        "variable": "message",
                        "label": "Message",
                        "type": "text-input",
                        "required": True,
                        "options": [],
                        "max_length": 256,
                    },
                ],
            },
        },
        {
            "id": "condition",
            "type": "custom",
            "data": {
                "type": "if-else",
                "title": "Condition",
                "cases": [
                    {
                        "case_id": "true",
                        "logical_operator": "and",
                        "conditions": [
                            {
                                "variable_selector": ["start", "missing" if missing_selector else "enabled"],
                                "comparison_operator": "is",
                                "value": True,
                            }
                        ],
                    }
                ],
            },
        },
        {
            "id": "true-template",
            "type": "custom",
            "data": {
                "type": "template-transform",
                "title": "True Template",
                "variables": [{"variable": "value", "value_selector": ["start", "message"]}],
                "template": "TRUE {{ value }}",
            },
        },
        {
            "id": "false-template",
            "type": "custom",
            "data": {
                "type": "template-transform",
                "title": "False Template",
                "variables": [{"variable": "value", "value_selector": ["start", "message"]}],
                "template": "FALSE {{ value }}",
            },
        },
        {
            "id": "true-end",
            "type": "custom",
            "data": {
                "type": "end",
                "title": "True End",
                "outputs": [{"variable": "result", "value_selector": ["true-template", "output"]}],
            },
        },
        {
            "id": "false-end",
            "type": "custom",
            "data": {
                "type": "end",
                "title": "False End",
                "outputs": [{"variable": "result", "value_selector": ["false-template", "output"]}],
            },
        },
    ]
    return {
        "nodes": nodes,
        "edges": [
            {"id": "start-condition", "source": "start", "target": "condition"},
            {"id": "condition-true", "source": "condition", "target": "true-template", "sourceHandle": "true"},
            {"id": "condition-false", "source": "condition", "target": "false-template", "sourceHandle": "false"},
            {"id": "true-end-edge", "source": "true-template", "target": "true-end"},
            {"id": "false-end-edge", "source": "false-template", "target": "false-end"},
        ],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }


def run_branch(client: TestClient, *, enabled: bool, missing_selector: bool = False) -> list[dict]:
    saved = client.post(
        f"/console/api/apps/{APP_ID}/workflows/draft",
        json={
            "graph": branch_graph(missing_selector=missing_selector),
            "features": {},
            "conversation_variables": [],
            "hash": None,
        },
    )
    assert saved.status_code == 200, saved.text
    with client.stream(
        "POST",
        f"/console/api/apps/{APP_ID}/workflows/draft/run",
        json={"inputs": {"enabled": enabled, "message": "value"}, "files": []},
    ) as response:
        if response.status_code != 200:
            raise AssertionError(response.read().decode())
        return [json.loads(line[6:]) for line in response.iter_lines() if line.startswith("data: ")]


def test_if_else_executes_only_the_true_branch(tmp_path: Path) -> None:
    with authenticated_client(tmp_path) as client:
        events = run_branch(client, enabled=True)

    assert events[-1]["data"]["status"] == "succeeded"
    assert events[-1]["data"]["outputs"] == {"result": "TRUE value"}
    started = {event["data"]["node_id"] for event in events if event["event"] == "node_started"}
    assert "true-template" in started
    assert "true-end" in started
    assert "false-template" not in started
    assert "false-end" not in started


def test_if_else_executes_only_the_false_branch(tmp_path: Path) -> None:
    with authenticated_client(tmp_path) as client:
        events = run_branch(client, enabled=False)

    assert events[-1]["data"]["status"] == "succeeded"
    assert events[-1]["data"]["outputs"] == {"result": "FALSE value"}
    started = {event["data"]["node_id"] for event in events if event["event"] == "node_started"}
    assert "false-template" in started
    assert "false-end" in started
    assert "true-template" not in started
    assert "true-end" not in started


def test_if_else_condition_error_has_one_failed_terminal_event(tmp_path: Path) -> None:
    with authenticated_client(tmp_path) as client:
        events = run_branch(client, enabled=True, missing_selector=True)

    terminal = [event for event in events if event["event"] == "workflow_finished"]
    assert len(terminal) == 1
    assert terminal[0]["data"]["status"] == "failed"
    condition = next(
        event for event in events if event["event"] == "node_finished" and event["data"]["node_id"] == "condition"
    )
    assert condition["data"]["status"] == "failed"
    assert condition["data"]["error"]
