"""本机模型设置：写在后端本地文件，不进入 Git、不回显密钥。

环境变量是初始配置，教师通过 ``PUT /model-settings`` 覆盖后写入
``data/model-settings.json``（仅本机）。密钥只保存于后端，读取接口只返回
``has_api_key`` 布尔值。
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from study_companion.config import Settings, get_settings


class ModelOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str | None = None
    base_url: str | None = None
    model_name: str | None = None
    api_key: str | None = Field(default=None, repr=False)


def _path(settings: Settings | None = None) -> Path:
    root = (settings or get_settings()).data_root
    return root / "model-settings.json"


def load_override(settings: Settings | None = None) -> ModelOverride:
    path = _path(settings)
    if not path.is_file():
        return ModelOverride()
    try:
        return ModelOverride.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:
        return ModelOverride()


def save_override(override: ModelOverride, settings: Settings | None = None) -> None:
    path = _path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(override.model_dump(exclude_none=True), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def effective_settings(settings: Settings | None = None) -> Settings:
    base = settings or get_settings()
    override = load_override(base)
    updates: dict[str, object] = {}
    if override.provider:
        updates["model_provider"] = override.provider
    if override.base_url:
        updates["model_base_url"] = override.base_url
    if override.model_name:
        updates["model_name"] = override.model_name
    if override.api_key is not None:
        updates["model_api_key"] = override.api_key
    return base.model_copy(update=updates) if updates else base
