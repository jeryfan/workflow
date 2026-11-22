import json
from pathlib import Path

from auth_helpers import authenticated_client

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads((ROOT / "contracts/fixtures/reference-workflow.json").read_text(encoding="utf-8"))
TOOL_CREATE = "/console/api/workspaces/current/tool-provider/workflow/create"
TOOL_LIST = "/console/api/workspaces/current/tools/workflow"


def create_hello_tool(client) -> dict:
    saved = client.post(
        "/console/api/apps/00000000-0000-0000-0000-000000000001/workflows/draft",
        json={"graph": FIXTURE["graph"], "features": {}, "conversation_variables": [], "hash": None},
    )
    assert saved.status_code == 200, saved.text
    published = client.post(
        "/console/api/apps/00000000-0000-0000-0000-000000000001/workflows/publish",
        json={},
    )
    assert published.status_code == 200, published.text
    created = client.post(
        TOOL_CREATE,
        json={
            "workflow_app_id": "00000000-0000-0000-0000-000000000001",
            "name": "hello_workflow",
            "label": "Hello Workflow",
            "description": "Return a greeting",
            "icon": {"content": "🤖", "background": "#FFEAD5"},
            "parameters": [
                {
                    "name": "name",
                    "description": "Name to greet",
                    "form": "llm",
                    "required": True,
                    "type": "text-input",
                }
            ],
            "labels": [],
            "privacy_policy": "",
        },
    )
    assert created.status_code == 200, created.text
    return created.json()


def caller_graph(tool: dict) -> dict:
    return {
        "nodes": [
            {
                "id": "start",
                "type": "custom",
                "position": {"x": 80, "y": 160},
                "data": {
                    "title": "Start",
                    "type": "start",
                    "variables": [
                        {
                            "variable": "name",
                            "label": "Name",
                            "type": "text-input",
                            "required": True,
                            "options": [],
                        }
                    ],
                },
            },
            {
                "id": "tool",
                "type": "custom",
                "position": {"x": 400, "y": 160},
                "data": {
                    "title": "Hello Workflow",
                    "type": "tool",
                    "provider_id": tool["workflow_tool_id"],
                    "provider_type": "workflow",
                    "provider_name": "hello_workflow",
                    "tool_name": "hello_workflow",
                    "tool_label": "Hello Workflow",
                    "tool_parameters": {"name": {"type": "variable", "value": ["start", "name"]}},
                    "tool_configurations": {},
                    "tool_node_version": "2",
                },
            },
            {
                "id": "end",
                "type": "custom",
                "position": {"x": 720, "y": 160},
                "data": {
                    "title": "End",
                    "type": "end",
                    "outputs": [{"variable": "message", "value_selector": ["tool", "message"]}],
                },
            },
        ],
        "edges": [
            {
                "id": "start-tool",
                "source": "start",
                "target": "tool",
                "sourceHandle": "source",
                "targetHandle": "target",
                "data": {"sourceType": "start", "targetType": "tool"},
            },
            {
                "id": "tool-end",
                "source": "tool",
                "target": "end",
                "sourceHandle": "source",
                "targetHandle": "target",
                "data": {"sourceType": "tool", "targetType": "end"},
            },
        ],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }


def run_caller(client, app_id: str, graph: dict) -> list[dict]:
    saved = client.post(
        f"/console/api/apps/{app_id}/workflows/draft",
        json={"graph": graph, "features": {}, "conversation_variables": [], "hash": None},
    )
    assert saved.status_code == 200, saved.text
    response = client.post(
        f"/console/api/apps/{app_id}/workflows/draft/run",
        json={"inputs": {"name": "World"}, "files": []},
    )
    assert response.status_code == 200, response.text
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


def test_workflow_tool_is_listed_and_executes_the_published_workflow(tmp_path: Path) -> None:
    with authenticated_client(tmp_path) as client:
        tool = create_hello_tool(client)
        listed = client.get(TOOL_LIST)
        caller = client.post(
            "/console/api/apps",
            json={"name": "Caller", "mode": "workflow", "description": ""},
        ).json()
        events = run_caller(client, caller["id"], caller_graph(tool))

    providers = listed.json()
    assert listed.status_code == 200
    assert providers[0]["id"] == tool["workflow_tool_id"]
    assert providers[0]["type"] == "workflow"
    assert providers[0]["tools"][0]["parameters"][0]["name"] == "name"
    assert providers[0]["tools"][0]["output_schema"]["properties"]["message"]["type"] == "string"
    tool_events = [
        event for event in events if event["event"] == "node_finished" and event["data"]["node_id"] == "tool"
    ]
    assert tool_events[0]["data"]["status"] == "succeeded"
    assert tool_events[0]["data"]["outputs"]["message"] == "Hello World"
    assert events[-1]["data"]["status"] == "succeeded"
    assert events[-1]["data"]["outputs"] == {"message": "Hello World"}


def test_missing_workflow_tool_fails_the_caller_explicitly(tmp_path: Path) -> None:
    with authenticated_client(tmp_path) as client:
        tool = create_hello_tool(client)
        caller = client.post(
            "/console/api/apps",
            json={"name": "Caller", "mode": "workflow", "description": ""},
        ).json()
        graph = caller_graph(tool)
        saved = client.post(
            f"/console/api/apps/{caller['id']}/workflows/draft",
            json={"graph": graph, "features": {}, "conversation_variables": [], "hash": None},
        )
        assert saved.status_code == 200, saved.text
        deleted = client.delete("/console/api/apps/00000000-0000-0000-0000-000000000001")
        assert deleted.status_code == 204, deleted.text
        response = client.post(
            f"/console/api/apps/{caller['id']}/workflows/draft/run",
            json={"inputs": {"name": "World"}, "files": []},
        )
        assert response.status_code == 200, response.text
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]

    tool_events = [
        event for event in events if event["event"] == "node_finished" and event["data"]["node_id"] == "tool"
    ]
    assert tool_events[0]["data"]["status"] == "failed"
    assert "unavailable" in tool_events[0]["data"]["error"]
    assert events[-1]["data"]["status"] == "failed"
