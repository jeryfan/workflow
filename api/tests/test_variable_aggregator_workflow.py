import json
from pathlib import Path

from auth_helpers import authenticated_client
from fastapi.testclient import TestClient
from test_if_else_workflow import branch_graph

ROOT = Path(__file__).resolve().parents[2]
REFERENCE = json.loads((ROOT / "contracts/fixtures/reference-workflow.json").read_text())
APP_ID = REFERENCE["app_id"]


def aggregated_graph(*, grouped: bool = False) -> dict:
    graph = branch_graph()
    graph["nodes"] = [node for node in graph["nodes"] if node["id"] not in {"true-end", "false-end"}]
    graph["edges"] = [edge for edge in graph["edges"] if edge["id"] not in {"true-end-edge", "false-end-edge"}]
    variables = [["true-template", "output"], ["false-template", "output"]]
    graph["nodes"].extend(
        [
            {
                "id": "aggregate",
                "type": "custom",
                "data": {
                    "type": "variable-aggregator",
                    "title": "Aggregate",
                    "output_type": "string",
                    "variables": variables,
                    "advanced_settings": {
                        "group_enabled": True,
                        "groups": [
                            {
                                "group_name": "selected",
                                "output_type": "string",
                                "variables": variables,
                            }
                        ],
                    }
                    if grouped
                    else None,
                },
            },
            {
                "id": "end",
                "type": "custom",
                "data": {
                    "type": "end",
                    "title": "End",
                    "outputs": [
                        {
                            "variable": "result",
                            "value_selector": ["aggregate", "selected", "output"]
                            if grouped
                            else ["aggregate", "output"],
                        }
                    ],
                },
            },
        ]
    )
    graph["edges"].extend(
        [
            {"id": "true-aggregate", "source": "true-template", "target": "aggregate"},
            {"id": "false-aggregate", "source": "false-template", "target": "aggregate"},
            {"id": "aggregate-end", "source": "aggregate", "target": "end"},
        ]
    )
    return graph


def run_aggregate(client: TestClient, *, enabled: bool, grouped: bool = False) -> list[dict]:
    saved = client.post(
        f"/console/api/apps/{APP_ID}/workflows/draft",
        json={
            "graph": aggregated_graph(grouped=grouped),
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


def test_variable_aggregator_uses_the_selected_true_branch(tmp_path: Path) -> None:
    with authenticated_client(tmp_path) as client:
        events = run_aggregate(client, enabled=True)

    assert events[-1]["data"]["status"] == "succeeded"
    assert events[-1]["data"]["outputs"] == {"result": "TRUE value"}


def test_variable_aggregator_uses_the_selected_false_branch(tmp_path: Path) -> None:
    with authenticated_client(tmp_path) as client:
        events = run_aggregate(client, enabled=False)

    assert events[-1]["data"]["status"] == "succeeded"
    assert events[-1]["data"]["outputs"] == {"result": "FALSE value"}


def test_variable_aggregator_supports_dify_grouped_output(tmp_path: Path) -> None:
    with authenticated_client(tmp_path) as client:
        events = run_aggregate(client, enabled=True, grouped=True)

    assert events[-1]["data"]["status"] == "succeeded"
    assert events[-1]["data"]["outputs"] == {"result": "TRUE value"}
