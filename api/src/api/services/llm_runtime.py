from __future__ import annotations

import base64
import time
from collections.abc import Generator, Mapping, Sequence
from typing import Any, Literal, overload

from pydantic import ValidationError
from pydantic_ai import Agent, StructuredDict, UnexpectedModelBehavior
from pydantic_ai.messages import (
    AudioUrl,
    BinaryContent,
    DocumentUrl,
    ImageUrl,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    UserPromptPart,
    VideoUrl,
)
from pydantic_ai.models import Model, infer_model
from pydantic_ai.models.test import TestModel
from pydantic_ai.providers import infer_provider_class
from pydantic_ai.providers.openai_compatible import OpenAICompatibleProvider

from api.configs import app_config
from api.configs.llm_config import ConfiguredLLMModel
from api.core.graphon.model_runtime.entities.llm_entities import (
    LLMResult,
    LLMResultChunk,
    LLMResultChunkDelta,
    LLMResultChunkWithStructuredOutput,
    LLMResultWithStructuredOutput,
    LLMUsage,
)
from api.core.graphon.model_runtime.entities.message_entities import (
    AssistantPromptMessage,
    MultiModalPromptMessageContent,
    PromptMessage,
    PromptMessageRole,
    PromptMessageTool,
    TextPromptMessageContent,
)
from api.core.graphon.model_runtime.entities.model_entities import (
    AIModelEntity,
    FetchFrom,
    ModelFeature,
    ModelPropertyKey,
    ModelType,
)


def create_pydantic_model(config: ConfiguredLLMModel) -> Model:
    if config.provider == "test":
        if app_config.APP_ENV == "production":
            raise ValueError("The test model is not available in production")
        return TestModel(custom_output_text="Test model response")
    api_key = config.api_key.get_secret_value() if config.api_key is not None else None
    if config.provider == "openai-compatible":
        if not config.base_url:
            raise ValueError("OpenAI-compatible models require base_url")
        from pydantic_ai.models.openai import OpenAIChatModel

        return OpenAIChatModel(
            config.name,
            provider=OpenAICompatibleProvider(config.id, base_url=config.base_url, api_key=api_key),
        )
    provider_kwargs = {key: value for key, value in {"api_key": api_key, "base_url": config.base_url}.items() if value}
    try:
        provider = infer_provider_class(config.provider)(**provider_kwargs)
        return infer_model(f"{config.provider}:{config.name}", provider_factory=lambda _name: provider)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Invalid configuration for LLM model {config.id}: {error}") from error


def _user_content(content: Any) -> Any:
    if content is None or isinstance(content, str):
        return content or ""
    result = []
    for item in content:
        if isinstance(item, TextPromptMessageContent):
            result.append(item.data)
            continue
        if not isinstance(item, MultiModalPromptMessageContent):
            continue
        if item.base64_data:
            result.append(BinaryContent(data=base64.b64decode(item.base64_data), media_type=item.mime_type))
            continue
        url_type = {
            "image": ImageUrl,
            "audio": AudioUrl,
            "document": DocumentUrl,
            "video": VideoUrl,
        }.get(item.type.value)
        if url_type is None:
            raise ValueError(f"Unsupported prompt content type: {item.type}")
        result.append(url_type(url=item.data))
    return result


def _message_history(prompt_messages: Sequence[PromptMessage]) -> list[ModelRequest | ModelResponse]:
    history: list[ModelRequest | ModelResponse] = []
    for message in prompt_messages:
        if message.role == PromptMessageRole.SYSTEM:
            history.append(ModelRequest(parts=[SystemPromptPart(message.get_text_content())]))
        elif message.role == PromptMessageRole.USER:
            history.append(ModelRequest(parts=[UserPromptPart(_user_content(message.content))]))
        elif message.role == PromptMessageRole.ASSISTANT:
            history.append(ModelResponse(parts=[TextPart(message.get_text_content())]))
        else:
            raise ValueError(f"Unsupported prompt role: {message.role}")
    return history


def _model_settings(config: ConfiguredLLMModel, parameters: Mapping[str, Any], stop: Sequence[str] | None):
    settings = dict(config.completion_params)
    settings.update(parameters)
    selected_stop = list(stop if stop is not None else config.stop_sequences)
    if selected_stop:
        settings["stop_sequences"] = selected_stop
    settings["request_timeout"] = config.timeout
    settings["max_retries"] = config.max_retries
    return settings


def _usage(usage: Any, started_at: float) -> LLMUsage:
    return LLMUsage.from_metadata(
        {
            "prompt_tokens": usage.input_tokens,
            "completion_tokens": usage.output_tokens,
            "total_tokens": usage.input_tokens + usage.output_tokens,
            "total_price": usage.cost or 0,
            "currency": "USD",
            "latency": time.perf_counter() - started_at,
        }
    )


class PydanticAIModel:
    def __init__(self, config: ConfiguredLLMModel, *, model: Model | None = None) -> None:
        self._config = config
        self._model = model or create_pydantic_model(config)
        self._parameters = dict(config.completion_params)

    @property
    def provider(self) -> str:
        return self._config.provider

    @property
    def model_name(self) -> str:
        return self._config.name

    @property
    def parameters(self) -> Mapping[str, Any]:
        return dict(self._parameters)

    @parameters.setter
    def parameters(self, value: Mapping[str, Any]) -> None:
        self._parameters = dict(value)

    @property
    def stop(self) -> Sequence[str] | None:
        return list(self._config.stop_sequences) or None

    def get_model_schema(self) -> AIModelEntity:
        features = {ModelFeature(feature) for feature in self._config.features}
        features.add(ModelFeature.STRUCTURED_OUTPUT)
        return AIModelEntity(
            model=self._config.name,
            label={"en_US": self._config.display_label, "zh_Hans": self._config.display_label},
            model_type=ModelType.LLM,
            features=list(features),
            fetch_from=FetchFrom.PREDEFINED_MODEL,
            model_properties={ModelPropertyKey.MODE: self._config.mode},
        )

    def get_llm_num_tokens(self, prompt_messages: Sequence[PromptMessage]) -> int:
        return sum(max(1, (len(message.get_text_content()) + 3) // 4) for message in prompt_messages)

    @overload
    def invoke_llm(
        self,
        *,
        prompt_messages: Sequence[PromptMessage],
        model_parameters: Mapping[str, Any],
        tools: Sequence[PromptMessageTool] | None,
        stop: Sequence[str] | None,
        stream: Literal[False],
    ) -> LLMResult: ...

    @overload
    def invoke_llm(
        self,
        *,
        prompt_messages: Sequence[PromptMessage],
        model_parameters: Mapping[str, Any],
        tools: Sequence[PromptMessageTool] | None,
        stop: Sequence[str] | None,
        stream: Literal[True],
    ) -> Generator[LLMResultChunk]: ...

    def invoke_llm(
        self,
        *,
        prompt_messages: Sequence[PromptMessage],
        model_parameters: Mapping[str, Any],
        tools: Sequence[PromptMessageTool] | None,
        stop: Sequence[str] | None,
        stream: bool,
    ) -> LLMResult | Generator[LLMResultChunk]:
        if tools:
            raise ValueError("LLM nodes do not accept ad-hoc tools")
        if stream:
            return self._stream(prompt_messages, model_parameters, stop, None)
        return self._run(prompt_messages, model_parameters, stop, None)

    @overload
    def invoke_llm_with_structured_output(
        self,
        *,
        prompt_messages: Sequence[PromptMessage],
        json_schema: Mapping[str, Any],
        model_parameters: Mapping[str, Any],
        stop: Sequence[str] | None,
        stream: Literal[False],
    ) -> LLMResultWithStructuredOutput: ...

    @overload
    def invoke_llm_with_structured_output(
        self,
        *,
        prompt_messages: Sequence[PromptMessage],
        json_schema: Mapping[str, Any],
        model_parameters: Mapping[str, Any],
        stop: Sequence[str] | None,
        stream: Literal[True],
    ) -> Generator[LLMResultChunkWithStructuredOutput]: ...

    def invoke_llm_with_structured_output(
        self,
        *,
        prompt_messages: Sequence[PromptMessage],
        json_schema: Mapping[str, Any],
        model_parameters: Mapping[str, Any],
        stop: Sequence[str] | None,
        stream: bool,
    ) -> LLMResultWithStructuredOutput | Generator[LLMResultChunkWithStructuredOutput]:
        if stream:
            return self._stream(prompt_messages, model_parameters, stop, json_schema)
        return self._run(prompt_messages, model_parameters, stop, json_schema)

    def is_structured_output_parse_error(self, error: Exception) -> bool:
        return isinstance(error, UnexpectedModelBehavior | ValidationError)

    def _agent(self):
        return Agent(self._model, retries=self._config.max_retries)

    def _run(
        self,
        prompt_messages: Sequence[PromptMessage],
        parameters: Mapping[str, Any],
        stop: Sequence[str] | None,
        json_schema: Mapping[str, Any] | None,
    ) -> LLMResult | LLMResultWithStructuredOutput:
        started_at = time.perf_counter()
        output_type = StructuredDict(dict(json_schema), name="WorkflowOutput") if json_schema is not None else str
        result = self._agent().run_sync(
            message_history=_message_history(prompt_messages),
            output_type=output_type,
            model_settings=_model_settings(self._config, parameters, stop),
            timeout=self._config.timeout,
        )
        usage = _usage(result.usage, started_at)
        message = AssistantPromptMessage(content=result.output if isinstance(result.output, str) else "")
        values = {
            "model": self.model_name,
            "prompt_messages": list(prompt_messages),
            "message": message,
            "usage": usage,
            "system_fingerprint": None,
        }
        if json_schema is None:
            return LLMResult(**values)
        return LLMResultWithStructuredOutput(**values, structured_output=dict(result.output))

    def _stream(
        self,
        prompt_messages: Sequence[PromptMessage],
        parameters: Mapping[str, Any],
        stop: Sequence[str] | None,
        json_schema: Mapping[str, Any] | None,
    ) -> Generator[LLMResultChunk]:
        started_at = time.perf_counter()
        output_type = StructuredDict(dict(json_schema), name="WorkflowOutput") if json_schema is not None else str
        with self._agent().run_stream_sync(
            message_history=_message_history(prompt_messages),
            output_type=output_type,
            model_settings=_model_settings(self._config, parameters, stop),
            timeout=self._config.timeout,
        ) as result:
            if json_schema is None:
                for text in result.stream_text(delta=True, debounce_by=None):
                    yield self._chunk(text)
            else:
                for output in result.stream_output(debounce_by=None):
                    yield self._chunk("", structured_output=dict(output))
            usage = _usage(result.usage, started_at)
            yield self._chunk(
                "",
                usage=usage,
                finish_reason=result.response.finish_reason,
                structured_output=result.get_output() if json_schema is not None else None,
            )

    def _chunk(
        self,
        text: str,
        *,
        usage: LLMUsage | None = None,
        finish_reason: str | None = None,
        structured_output: Mapping[str, Any] | None = None,
    ) -> LLMResultChunk:
        values = {
            "model": self.model_name,
            "prompt_messages": [],
            "delta": LLMResultChunkDelta(
                index=0,
                message=AssistantPromptMessage(content=text),
                usage=usage,
                finish_reason=finish_reason,
            ),
        }
        if structured_output is not None:
            return LLMResultChunkWithStructuredOutput(**values, structured_output=structured_output)
        return LLMResultChunk(**values)


class UnsupportedLLMFileSaver:
    def save_binary_string(self, *_args, **_kwargs):
        raise ValueError("Multimodal model output is not supported")

    def save_remote_url(self, *_args, **_kwargs):
        raise ValueError("Multimodal model output is not supported")


class PromptMessageSerializer:
    def serialize(self, *, prompt_messages: Sequence[PromptMessage], **_kwargs) -> list[dict[str, Any]]:
        return [message.model_dump(mode="json") for message in prompt_messages]
