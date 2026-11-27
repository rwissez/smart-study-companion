"""模型设置与连通性测试（教师本地能力）。

* 读取永不返回密钥，只返回 ``has_api_key``；
* 缺省字段保留原值，清空密钥必须用 ``clear_api_key=true`` 显式声明；
* 连通性测试不发送任何赛事内容或学生作答。
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter

from study_companion.api.deps import AppSettings, Scope
from study_companion.api.envelope import success
from study_companion.contracts import DomainError, ModelSettingsUpdate
from study_companion.model_service import ModelService
from study_companion.model_settings import ModelOverride, effective_settings, load_override, save_override

router = APIRouter(tags=["model"])


@router.get("/model-settings")
def get_model_settings(scope: Scope, settings: AppSettings) -> dict:
    scope.require("model_settings")
    service = ModelService(effective_settings(settings))
    payload = service.describe()
    return success(
        {
            "configured": payload["configured"],
            "provider": payload["provider"],
            "url": payload["url"],
            "model_id": payload["model_id"],
            "has_api_key": payload["has_api_key"],
        }
    )


@router.put("/model-settings")
def update_model_settings(payload: ModelSettingsUpdate, scope: Scope, settings: AppSettings) -> dict:
    scope.require("model_settings")
    scope.require_teacher()
    if payload.url and not (payload.url.startswith("http://") or payload.url.startswith("https://")):
        raise DomainError(
            "VALIDATION_ERROR",
            "模型地址必须是 http(s) 端点",
            [{"field": "url", "reason": "只接受 http:// 或 https://"}],
        )
    current = load_override(settings)
    override = ModelOverride(
        provider=payload.provider or current.provider,
        base_url=payload.url or current.base_url,
        model_name=payload.model_id or current.model_name,
        api_key="" if payload.clear_api_key else (payload.api_key if payload.api_key is not None else current.api_key),
    )
    save_override(override, settings)
    service = ModelService(effective_settings(settings))
    described = service.describe()
    return success(
        {
            "configured": described["configured"],
            "provider": described["provider"],
            "url": described["url"],
            "model_id": described["model_id"],
            "has_api_key": described["has_api_key"],
        }
    )


@router.post("/model-settings/test")
def test_model_settings(scope: Scope, settings: AppSettings) -> dict:
    scope.require("model_settings")
    effective = effective_settings(settings)
    if not effective.model_configured:
        raise DomainError("MODEL_NOT_CONFIGURED", "尚未配置模型地址与模型名")
    url = effective.model_base_url.rstrip("/") + "/models"
    headers = {"Authorization": f"Bearer {effective.model_api_key}"} if effective.model_api_key else {}
    try:
        with httpx.Client(timeout=min(effective.model_timeout_seconds, 15)) as client:
            response = client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        return success({"connected": False, "message": f"连接失败：{type(exc).__name__}"})
    connected = response.status_code < 500
    message = "接口可连通（仅说明地址可达，不代表教学输出已验收）" if connected else f"服务返回 {response.status_code}"
    return success({"connected": connected, "message": message})
