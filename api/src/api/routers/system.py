from __future__ import annotations

import json
from functools import cache
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.configs import PROJECT_ROOT
from api.dependencies.db import get_db
from api.dependencies.workflows import authenticated_actor
from api.models.app import App
from api.models.entities import Actor

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
        },
    }


@router.get("/features")
async def get_features(
    _actor: Actor = Depends(authenticated_actor),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    app_count = await db.scalar(select(func.count()).select_from(App))
    return {
        **frontend_contract()["features"],
        "apps": {"size": app_count or 0, "limit": 0},
    }


@router.get("/files/upload")
async def get_upload_config(_actor: Actor = Depends(authenticated_actor)) -> dict[str, Any]:
    return frontend_contract()["upload_config"]


@router.get("/workspaces/current/tools/builtin")
@router.get("/workspaces/current/tools/api")
@router.get("/workspaces/current/tools/mcp")
@router.get("/workspaces/current/agent-providers")
@router.get("/workspaces/current/triggers")
@router.get("/spec/schema-definitions")
async def get_excluded_capability_catalogs(_actor: Actor = Depends(authenticated_actor)) -> list:
    return []


@router.get("/workspaces/current/model-providers/summary")
async def get_model_provider_summary(_actor: Actor = Depends(authenticated_actor)) -> dict[str, Any]:
    return {"data": [], "plugins": {}}


@router.get("/app-dsl-version")
async def get_app_dsl_version(_actor: Actor = Depends(authenticated_actor)) -> dict[str, str]:
    return {"app_dsl_version": "0.7.0"}


@router.get("/workspaces/current/default-model")
async def get_default_model(_actor: Actor = Depends(authenticated_actor)) -> dict[str, None]:
    return {"data": None}


@router.get("/workspaces/current/models/model-types/{model_type}")
async def get_available_models(
    model_type: str,
    _actor: Actor = Depends(authenticated_actor),
) -> dict[str, list]:
    return {"data": []}
