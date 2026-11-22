from __future__ import annotations

import json
from functools import cache
from typing import Any

from fastapi import APIRouter, Request

from api.config import PROJECT_ROOT
from api.dependencies.auth import require_principal
from api.dependencies.workflows import authenticated_actor

router = APIRouter()
FRONTEND_CONTRACT_PATH = PROJECT_ROOT / "contracts" / "frontend-runtime.json"


@cache
def frontend_contract() -> dict[str, Any]:
    return json.loads(FRONTEND_CONTRACT_PATH.read_text(encoding="utf-8"))


@router.get("/system-features")
def get_system_features() -> dict[str, Any]:
    return {
        **frontend_contract()["system_features"],
        "enable_email_password_login": True,
        "rbac_enabled": True,
        "branding": {
            "enabled": False,
            "application_title": "Dify",
            "favicon": "",
            "login_page_logo": "",
            "workspace_logo": "",
        },
        "license": {"status": "none"},
        "enable_app_deploy": False,
        "enable_change_email": False,
        "enable_creators_platform": False,
        "enable_email_code_login": False,
        "enable_explore_banner": False,
        "enable_learn_app": False,
        "enable_social_oauth_login": False,
        "enable_step_by_step_tour": False,
        "is_allow_register": False,
        "is_email_setup": False,
        "plugin_installation_permission": {
            "plugin_installation_scope": "none",
            "restrict_to_marketplace_only": False,
        },
        "sso_enforced_for_signin": False,
        "webapp_auth": {
            "enabled": False,
            "allow_email_code_login": False,
            "allow_email_password_login": False,
            "allow_public_access": False,
            "allow_sso": False,
            "sso_config": {"protocol": None},
        },
    }


@router.get("/features")
def get_features(request: Request) -> dict[str, Any]:
    require_principal(request)
    app_count = len(request.app.state.store.list_app_records())
    return {
        **frontend_contract()["features"],
        "apps": {"size": app_count, "limit": 0},
    }


@router.get("/files/upload")
def get_upload_config(request: Request) -> dict[str, Any]:
    authenticated_actor(request)
    return frontend_contract()["upload_config"]


@router.get("/workspaces/current/tools/builtin")
@router.get("/workspaces/current/tools/api")
@router.get("/workspaces/current/tools/mcp")
@router.get("/workspaces/current/agent-providers")
@router.get("/workspaces/current/triggers")
@router.get("/spec/schema-definitions")
def get_excluded_capability_catalogs(request: Request) -> list:
    authenticated_actor(request)
    return []


@router.get("/workspaces/current/model-providers/summary")
def get_model_provider_summary(request: Request) -> dict[str, Any]:
    authenticated_actor(request)
    return {"data": [], "plugins": {}}


@router.get("/app-dsl-version")
def get_app_dsl_version(request: Request) -> dict[str, str]:
    authenticated_actor(request)
    return {"app_dsl_version": "0.7.0"}


@router.get("/workspaces/current/default-model")
def get_default_model(request: Request) -> dict[str, None]:
    authenticated_actor(request)
    return {"data": None}


@router.get("/workspaces/current/models/model-types/{model_type}")
def get_available_models(model_type: str, request: Request) -> dict[str, list]:
    authenticated_actor(request)
    return {"data": []}
