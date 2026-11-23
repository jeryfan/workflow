#!/usr/bin/env python3

import json
import os
from uuid import uuid4

from verify_live_api import BASE_URL, LiveApiClient


def main() -> None:
    email = os.environ.get("WORKFLOW_ADMIN_EMAIL")
    password = os.environ.get("WORKFLOW_ADMIN_PASSWORD")
    if not email or not password:
        raise SystemExit(
            "Set WORKFLOW_ADMIN_EMAIL and WORKFLOW_ADMIN_PASSWORD for real-model verification"
        )

    client = LiveApiClient(BASE_URL)
    client.login(email, password)
    configured = client.json(
        "GET", "/console/api/workspaces/current/default-model?model_type=llm"
    )["data"]
    if configured is None:
        raise SystemExit("No default LLM is configured in LLM_MODELS")
    provider = configured["provider"]["provider"]
    model = configured["model"]
    left = int(uuid4().hex[:4], 16) % 40 + 10
    right = int(uuid4().hex[4:8], 16) % 40 + 10
    marker = str(left + right)
    graph = {
        "nodes": [
            {
                "id": "start",
                "type": "custom",
                "position": {"x": 80, "y": 160},
                "data": {"title": "Start", "type": "start", "variables": []},
            },
            {
                "id": "llm",
                "type": "custom",
                "position": {"x": 380, "y": 160},
                "data": {
                    "title": "Real model",
                    "type": "llm",
                    "model": {
                        "provider": provider,
                        "name": model,
                        "mode": "chat",
                        "completion_params": {"temperature": 0, "max_tokens": 48},
                    },
                    "prompt_template": [
                        {
                            "role": "user",
                            "text": f"What is {left} + {right}? Reply with only the integer.",
                        }
                    ],
                    "context": {"enabled": False, "variable_selector": []},
                    "vision": {"enabled": False},
                },
            },
            {
                "id": "end",
                "type": "custom",
                "position": {"x": 680, "y": 160},
                "data": {
                    "title": "End",
                    "type": "end",
                    "outputs": [
                        {"variable": "answer", "value_selector": ["llm", "text"]}
                    ],
                },
            },
        ],
        "edges": [
            {
                "id": "start-llm",
                "source": "start",
                "target": "llm",
                "sourceHandle": "source",
                "targetHandle": "target",
                "data": {"sourceType": "start", "targetType": "llm"},
            },
            {
                "id": "llm-end",
                "source": "llm",
                "target": "end",
                "sourceHandle": "source",
                "targetHandle": "target",
                "data": {"sourceType": "llm", "targetType": "end"},
            },
        ],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }
    created = client.json(
        "POST",
        "/console/api/apps",
        {
            "name": f"Real LLM Acceptance {uuid4().hex[:8]}",
            "mode": "workflow",
            "description": "",
        },
    )
    app_id = created["id"]
    try:
        saved = client.json(
            "POST",
            f"/console/api/apps/{app_id}/workflows/draft",
            {
                "graph": graph,
                "features": {},
                "conversation_variables": [],
                "hash": None,
            },
        )
        assert saved["result"] == "success", saved
        with client._open(
            "POST",
            f"/console/api/apps/{app_id}/workflows/draft/run",
            {"inputs": {}, "files": []},
        ) as response:
            assert response.headers.get_content_type() == "text/event-stream"
            events = [
                json.loads(line.decode()[6:])
                for line in response
                if line.decode().startswith("data: ")
            ]
        terminal = events[-1]
        assert terminal["event"] == "workflow_finished", terminal
        assert terminal["data"]["status"] == "succeeded", terminal
        answer = terminal["data"]["outputs"]["answer"].strip()
        assert marker in answer, {"expected_marker": marker, "answer": answer}
        run_id = terminal["workflow_run_id"]
        detail = client.json(
            "GET", f"/console/api/apps/{app_id}/workflow-runs/{run_id}"
        )
        assert detail["status"] == "succeeded", detail
        print(
            json.dumps(
                {
                    "base_url": BASE_URL,
                    "provider": provider,
                    "model": model,
                    "answer": answer,
                    "run_id": run_id,
                }
            )
        )
    finally:
        with client._open("DELETE", f"/console/api/apps/{app_id}") as response:
            assert response.status == 204


if __name__ == "__main__":
    main()
