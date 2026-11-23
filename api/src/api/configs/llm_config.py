from typing import Any, Literal

from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings


class ConfiguredLLMModel(BaseModel):
    id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    name: str = Field(min_length=1)
    label: str | None = None
    api_key: SecretStr | None = None
    base_url: str | None = None
    mode: Literal["chat"] = "chat"
    features: list[Literal["vision", "structured-output"]] = Field(default_factory=list)
    context_length: int | None = Field(default=None, gt=0)
    max_output_tokens: int | None = Field(default=None, gt=0)
    completion_params: dict[str, Any] = Field(default_factory=dict)
    stop_sequences: list[str] = Field(default_factory=list)
    timeout: float = Field(default=60, gt=0)
    max_retries: int = Field(default=1, ge=0, le=10)
    is_default: bool = False

    @property
    def display_label(self) -> str:
        return self.label or self.name


class LLMConfig(BaseSettings):
    LLM_MODELS: list[ConfiguredLLMModel] = Field(
        default_factory=list,
        description="JSON array of models available to workflow LLM nodes.",
    )
