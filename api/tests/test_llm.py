import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from auth_helpers import authenticated_client
from pydantic import SecretStr
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.models.test import TestModel

from api.configs import app_config
from api.configs.llm_config import ConfiguredLLMModel
from api.core.graphon.model_runtime.entities.message_entities import SystemPromptMessage, UserPromptMessage
from api.services.llm_runtime import PydanticAIModel, create_pydantic_model
from api.services.model_catalog import ModelCatalog


def configured_model(**overrides) -> ConfiguredLLMModel:
    values = {
        "id": "primary",
        "provider": "test",
        "name": "test-model",
        "label": "Primary model",
        **overrides,
    }
    return ConfiguredLLMModel(**values)


def test_model_catalog_and_api_never_expose_credentials(tmp_path: Path, monkeypatch) -> None:
    model = configured_model(
        api_key=SecretStr("secret-key"),
        base_url="https://models.example.com",
        features=["structured-output"],
        context_length=128000,
        max_output_tokens=4096,
        is_default=True,
    )
    monkeypatch.setattr(app_config, "LLM_MODELS", [model])
    with authenticated_client(tmp_path) as client:
        summary = client.get("/console/api/workspaces/current/model-providers/summary")
        default = client.get("/console/api/workspaces/current/default-model", params={"model_type": "llm"})
        models = client.get("/console/api/workspaces/current/models/model-types/llm")
        flat = client.get("/console/api/models")
        credits = client.get("/console/api/workspaces/current/model-providers/credits")
        empty = client.get("/console/api/workspaces/current/models/model-types/text-embedding")
    assert summary.status_code == 200
    assert summary.json()["data"][0]["custom_configuration"]["current_credential_usable"] is True
    assert default.status_code == 200
    assert models.status_code == 200
    assert flat.status_code == 200
    assert credits.status_code == 200
    assert credits.json()["is_exhausted"] is True
    assert empty.json() == {"data": []}
    assert default.json()["data"]["model"] == "test-model"
    assert models.json()["data"] == [
        {
            "id": "primary",
            "name": "Primary model",
            "model": "test-model",
            "provider": "test",
            "mode": "chat",
            "context_length": 128000,
            "max_output_tokens": 4096,
            "capabilities": ["chat", "structured-output"],
            "is_default": True,
        }
    ]
    assert flat.json() == models.json()
    serialized = json.dumps([summary.json(), default.json(), models.json()])
    assert "secret-key" not in serialized
    assert "models.example.com" not in serialized


def test_native_providers_are_constructed_by_pydantic_ai() -> None:
    for provider, name, expected_type in (
        ("openai", "gpt-test", OpenAIResponsesModel),
        ("anthropic", "claude-test", AnthropicModel),
        ("google", "gemini-test", GoogleModel),
    ):
        model = create_pydantic_model(configured_model(provider=provider, name=name, api_key=SecretStr("test")))
        assert isinstance(model, expected_type)


def test_plain_and_structured_model_results_include_graphon_fields() -> None:
    prompts = [SystemPromptMessage(content="Be concise"), UserPromptMessage(content="Hello")]
    plain = PydanticAIModel(configured_model(), model=TestModel(custom_output_text="Hello world"))
    result = plain.invoke_llm(prompt_messages=prompts, model_parameters={}, tools=None, stop=None, stream=False)
    assert result.message.content == "Hello world"
    chunks = list(plain.invoke_llm(prompt_messages=prompts, model_parameters={}, tools=None, stop=None, stream=True))
    assert "".join(chunk.delta.message.content for chunk in chunks) == "Hello world"
    assert chunks[-1].delta.usage is not None

    schema = {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
        "additionalProperties": False,
    }
    structured = PydanticAIModel(configured_model(), model=TestModel(custom_output_args={"answer": "yes"}))
    result = structured.invoke_llm_with_structured_output(
        prompt_messages=prompts,
        json_schema=schema,
        model_parameters={},
        stop=None,
        stream=False,
    )
    assert result.structured_output == {"answer": "yes"}
    chunks = list(
        structured.invoke_llm_with_structured_output(
            prompt_messages=prompts,
            json_schema=schema,
            model_parameters={},
            stop=None,
            stream=True,
        )
    )
    assert chunks[-1].structured_output == {"answer": "yes"}


def test_openai_compatible_provider_consumes_real_streaming_http() -> None:
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("content-length", "0"))
            requests.append(
                {
                    "path": self.path,
                    "authorization": self.headers.get("Authorization"),
                    "body": json.loads(self.rfile.read(length)),
                }
            )
            chunks = [
                {"choices": [{"index": 0, "delta": {"role": "assistant", "content": "Hello"}, "finish_reason": None}]},
                {"choices": [{"index": 0, "delta": {"content": " world"}, "finish_reason": None}]},
                {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
                {"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}},
            ]
            response = {"id": "test", "object": "chat.completion.chunk", "model": "stub-model"}
            payload = "".join(f"data: {json.dumps({**chunk, **response})}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload.encode())
            self.close_connection = True

        def log_message(self, *_args) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        config = configured_model(
            provider="openai-compatible",
            name="stub-model",
            base_url=f"http://127.0.0.1:{server.server_port}/v1",
            api_key=SecretStr("test-key"),
            max_retries=0,
        )
        runtime = PydanticAIModel(config)
        chunks = list(
            runtime.invoke_llm(
                prompt_messages=[SystemPromptMessage(content="Be concise"), UserPromptMessage(content="Hello")],
                model_parameters={},
                tools=None,
                stop=None,
                stream=True,
            )
        )
        assert "".join(chunk.delta.message.content for chunk in chunks) == "Hello world"
        assert chunks[-1].delta.usage.total_tokens == 5
        assert requests[0]["path"] == "/v1/chat/completions"
        assert requests[0]["authorization"] == "Bearer test-key"
        assert requests[0]["body"]["messages"] == [
            {"role": "system", "content": "Be concise"},
            {"role": "user", "content": "Hello"},
        ]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_complete_workflow_executes_llm_node(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(app_config, "LLM_MODELS", [configured_model()])
    with authenticated_client(tmp_path) as client:
        app = client.post(
            "/console/api/apps",
            json={"name": "LLM workflow", "mode": "workflow", "description": ""},
        ).json()
        graph = {
            "nodes": [
                {
                    "id": "start",
                    "type": "custom",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "title": "Start",
                        "type": "start",
                        "variables": [
                            {"variable": "name", "label": "Name", "type": "text-input", "required": True, "options": []}
                        ],
                    },
                },
                {
                    "id": "llm",
                    "type": "custom",
                    "position": {"x": 200, "y": 0},
                    "data": {
                        "title": "LLM",
                        "type": "llm",
                        "model": {
                            "id": "primary",
                            "provider": "test",
                            "name": "test-model",
                            "mode": "chat",
                            "completion_params": {},
                        },
                        "prompt_template": [{"role": "user", "text": "Hello {{#start.name#}}"}],
                        "context": {"enabled": False, "variable_selector": []},
                        "vision": {"enabled": False},
                    },
                },
                {
                    "id": "end",
                    "type": "custom",
                    "position": {"x": 400, "y": 0},
                    "data": {
                        "title": "End",
                        "type": "end",
                        "outputs": [{"variable": "answer", "value_selector": ["llm", "text"]}],
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
        saved = client.post(
            f"/console/api/apps/{app['id']}/workflows/draft",
            json={"graph": graph, "features": {}, "conversation_variables": [], "hash": None},
        )
        assert saved.status_code == 200, saved.text
        response = client.post(
            f"/console/api/apps/{app['id']}/workflows/draft/run",
            json={"inputs": {"name": "World"}, "files": []},
        )
        assert response.status_code == 200, response.text
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        assert events[-1]["event"] == "workflow_finished"
        assert events[-1]["data"]["status"] == "succeeded", events[-1]
        assert events[-1]["data"]["outputs"] == {"answer": "Test model response"}
        run_id = events[-1]["workflow_run_id"]
        nodes = client.get(f"/console/api/apps/{app['id']}/workflow-runs/{run_id}/node-executions").json()["data"]
        llm = next(node for node in nodes if node["node_id"] == "llm")
        assert llm["inputs"]["model_provider"] == "test"
        assert llm["inputs"]["model_name"] == "test-model"
        assert llm["outputs"]["text"] == "Test model response"


def test_catalog_rejects_duplicate_ids_and_unknown_models() -> None:
    model = configured_model()
    try:
        ModelCatalog([model, model])
    except ValueError as error:
        assert "unique" in str(error)
    else:
        raise AssertionError("Duplicate model ids must be rejected")
    try:
        ModelCatalog([model]).get("test", "missing")
    except ValueError as error:
        assert "not configured" in str(error)
    else:
        raise AssertionError("Unknown models must be rejected")


def test_catalog_accepts_dify_plugin_provider_identity() -> None:
    model = configured_model(provider="deepseek", name="deepseek-chat")
    catalog = ModelCatalog([model])

    assert catalog.get("deepseek", "deepseek-chat") is model
    assert catalog.get("langgenius/deepseek/deepseek", "deepseek-chat") is model
    assert catalog.get_by_id("primary") is model
    assert catalog.model_options()[0]["provider"] == "deepseek"
    assert catalog.providers_with_models()[0]["provider"] == "deepseek"
    assert catalog.provider_summary()["data"][0]["provider"] == "deepseek"
    assert catalog.default_response()["provider"]["provider"] == "deepseek"


def test_retriever_resource_event_is_forwarded_without_failing_the_run() -> None:
    from types import SimpleNamespace

    from api.core.graphon.engine_events import NodeRunRetrieverResourceEvent
    from api.models.entities import ContainerEvent
    from api.services.engine import GraphonExecution

    execution = object.__new__(GraphonExecution)
    execution._snapshot = SimpleNamespace(identity="run-1")
    execution._sequence = 0
    event = NodeRunRetrieverResourceEvent(
        id="node-run-1",
        node_id="llm",
        node_type="llm",
        retriever_resources=[],
        context="Hello",
    )

    mapped = execution._map_event(event)

    assert isinstance(mapped, ContainerEvent)
    assert mapped.event == "retriever_resources"
    assert mapped.data["context"] == "Hello"
    assert mapped.data["node_id"] == "llm"
