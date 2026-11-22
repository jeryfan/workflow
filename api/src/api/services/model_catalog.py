from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from api.configs import app_config
from api.configs.llm_config import ConfiguredLLMModel


class ModelNotConfigured(ValueError):
    pass


class ModelCatalog:
    def __init__(self, models: Sequence[ConfiguredLLMModel] | None = None) -> None:
        self._models = tuple(models if models is not None else app_config.LLM_MODELS)
        ids = [model.id for model in self._models]
        if len(ids) != len(set(ids)):
            raise ValueError("LLM model ids must be unique")
        if sum(model.is_default for model in self._models) > 1:
            raise ValueError("Only one LLM model can be marked as default")

    @property
    def models(self) -> tuple[ConfiguredLLMModel, ...]:
        return self._models

    def get(self, provider: str, name: str) -> ConfiguredLLMModel:
        model = next((item for item in self._models if item.provider == provider and item.name == name), None)
        if model is None:
            raise ModelNotConfigured(f"Model is not configured: {provider}/{name}")
        return model

    def default(self) -> ConfiguredLLMModel | None:
        return next((model for model in self._models if model.is_default), self._models[0] if self._models else None)

    def providers_with_models(self) -> list[dict[str, Any]]:
        grouped: dict[str, list[ConfiguredLLMModel]] = {}
        for model in self._models:
            grouped.setdefault(model.provider, []).append(model)
        return [
            {
                "provider": provider,
                "label": {"en_US": provider, "zh_Hans": provider},
                "status": "active",
                "tenant_id": "local",
                "models": [self._model_response(model) for model in models],
            }
            for provider, models in grouped.items()
        ]

    def provider_summary(self) -> dict[str, Any]:
        return {
            "data": [
                {
                    "provider": provider,
                    "provider_name": provider,
                    "label": {"en_US": provider, "zh_Hans": provider},
                    "supported_model_types": ["llm"],
                    "tenant_id": "local",
                    "configurate_methods": ["predefined-model"],
                    "preferred_provider_type": "custom",
                    "plugin_id": provider,
                    "is_configured": True,
                    "system_configuration": {"enabled": False},
                    "custom_configuration": {
                        "status": "active",
                        "available_credentials": [],
                        "current_credential_usable": True,
                        "has_custom_models": False,
                    },
                }
                for provider in dict.fromkeys(model.provider for model in self._models)
            ],
            "plugins": {},
        }

    def default_response(self) -> dict[str, Any] | None:
        model = self.default()
        if model is None:
            return None
        return {
            "model": model.name,
            "model_type": "llm",
            "provider": {
                "provider": model.provider,
                "provider_name": model.provider,
                "label": {"en_US": model.provider, "zh_Hans": model.provider},
                "supported_model_types": ["llm"],
                "tenant_id": "local",
            },
        }

    @staticmethod
    def _model_response(model: ConfiguredLLMModel) -> dict[str, Any]:
        return {
            "model": model.name,
            "label": {"en_US": model.display_label, "zh_Hans": model.display_label},
            "model_type": "llm",
            "features": list(model.features),
            "fetch_from": "predefined-model",
            "model_properties": {"mode": model.mode},
            "status": "active",
            "deprecated": False,
        }
