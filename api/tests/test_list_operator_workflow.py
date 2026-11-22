import json
from pathlib import Path

from auth_helpers import authenticated_client
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
REFERENCE = json.loads((ROOT / "contracts/fixtures/reference-workflow.json").read_text())
APP_ID = REFERENCE["app_id"]


def list_graph(*, missing_selector: bool = False) -> dict:
    return {
        "nodes": [
            {
                "id": "start",
                "type": "custom",
                "data": {
                    "type": "start",
                    "title": "Start",
                    "variables": [
                        {
                            "variable": "items",
                            "label": "Items",
                            "type": "json_object",
                            "required": True,
                            "options": [],
                        }
                    ],
                },
            },
            {
                "id": "list",
                "type": "custom",
                "data": {
                    "type": "list-operator",
                    "title": "List",
                    "variable": ["start", "missing"] if missing_selector else ["start", "items", "values"],
                    "filter_by": {
                        "enabled": True,
                        "conditions": [{"comparison_operator": "contains", "value": "alpha"}],
                    },
                    "extract_by": {"enabled": False, "serial": "1"},
                    "order_by": {"enabled": True, "key": "", "value": "desc"},
                    "limit": {"enabled": True, "size": 1},
                },
            },
            {
                "id": "end",
                "type": "custom",
                "data": {
                    "type": "end",
                    "title": "End",
                    "outputs": [
                        {"variable": "result", "value_selector": ["list", "result"]},
                        {"variable": "first", "value_selector": ["list", "first_record"]},
                        {"variable": "last", "value_selector": ["list", "last_record"]},
                    ],
                },
            },
        ],
        "edges": [
            {"id": "start-list", "source": "start", "target": "list"},
            {"id": "list-end", "source": "list", "target": "end"},
        ],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }


def run_list(client: TestClient, items: list[str], *, missing_selector: bool = False) -> list[dict]:
    saved = client.post(
        f"/console/api/apps/{APP_ID}/workflows/draft",
        json={
            "graph": list_graph(missing_selector=missing_selector),
            "features": {},
            "conversation_variables": [],
            "hash": None,
        },
    )
    assert saved.status_code == 200, saved.text
    with client.stream(
        "POST",
        f"/console/api/apps/{APP_ID}/workflows/draft/run",
        json={"inputs": {"items": {"values": items}}, "files": []},
    ) as response:
        if response.status_code != 200:
            raise AssertionError(response.read().decode())
        return [json.loads(line[6:]) for line in response.iter_lines() if line.startswith("data: ")]


def test_list_operator_filters_orders_limits_and_exposes_first_and_last(tmp_path: Path) -> None:
    items = ["beta", "alphabet", "alpha", "gamma"]
    with authenticated_client(tmp_path) as client:
        events = run_list(client, items)

    assert events[-1]["data"]["status"] == "succeeded"
    assert events[-1]["data"]["outputs"] == {
        "result": ["alphabet"],
        "first": "alphabet",
        "last": "alphabet",
    }


def test_list_operator_preserves_empty_array_outputs(tmp_path: Path) -> None:
    with authenticated_client(tmp_path) as client:
        events = run_list(client, [])

    assert events[-1]["data"]["status"] == "succeeded"
    assert events[-1]["data"]["outputs"] == {"result": [], "first": None, "last": None}


def test_list_operator_missing_variable_fails_the_node_and_run(tmp_path: Path) -> None:
    with authenticated_client(tmp_path) as client:
        events = run_list(client, ["alpha"], missing_selector=True)

    assert events[-1]["data"]["status"] == "failed"
    failed = next(event for event in events if event["event"] == "node_finished" and event["data"]["node_id"] == "list")
    assert failed["data"]["status"] == "failed"
    assert "Variable not found" in failed["data"]["error"]
