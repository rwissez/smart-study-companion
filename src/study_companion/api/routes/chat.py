"""Chat and local model configuration."""

from __future__ import annotations

from typing import Any
import json
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from study_companion.agent import build_provider
from study_companion.agent.providers.openai_compatible import ModelConnection, load_connection, save_connection
from study_companion.api.deps import AppSettings, DbSession, Scope
from study_companion.api.envelope import success
from study_companion.api.routes.runs import get_run
from study_companion.contracts import DomainError
from study_companion.db import models
from study_companion.library.datasets import load_catalog

router = APIRouter(tags=["chat"])


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=8000)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: list[ChatMessage] = Field(min_length=1, max_length=40)
    analysis_context: AnalysisContext | None = None


class AnalysisContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str = Field(min_length=1, max_length=64)
    node_id: str = Field(min_length=1, max_length=64)
    subject_type: str = Field(pattern="^(student|class)$")
    subject_id: str = Field(min_length=1, max_length=64)


class ModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    url: str = Field(min_length=1, max_length=2000)
    model_id: str = Field(min_length=1, max_length=300)
    api_key: SecretStr = Field(default=SecretStr(""), max_length=4096)


def require_local_teacher(request: Request, scope: Scope) -> None:
    if scope.role != "teacher" or not request.client or request.client.host not in {"127.0.0.1", "::1"}:
        raise DomainError("FORBIDDEN", "请在本机用教师身份配置模型")


@router.get("/model-settings")
def get_model_settings(settings: AppSettings, scope: Scope) -> dict:
    connection = load_connection(settings)
    data = {"configured": connection is not None}
    if scope.role == "teacher" and connection:
        data.update(url=connection.url, model_id=connection.model_id, has_api_key=bool(connection.api_key))
    if connection and uses_local_model(connection.url):
        data["analysis_context_available"] = True
    return success(data)


@router.put("/model-settings")
def put_model_settings(payload: ModelRequest, request: Request, settings: AppSettings, scope: Scope) -> dict:
    require_local_teacher(request, scope)
    old = load_connection(settings)
    key = payload.api_key.get_secret_value().strip()
    if not key and old and old.url.rstrip("/") == payload.url.rstrip("/"):
        key = old.api_key
    connection = ModelConnection(payload.url, payload.model_id, key)
    save_connection(settings.data_root, connection)
    return success({"configured": True, "url": connection.url, "model_id": connection.model_id, "has_api_key": bool(key)})


@router.post("/model-settings/test")
def test_model_settings(request: Request, settings: AppSettings, scope: Scope) -> dict:
    require_local_teacher(request, scope)
    build_provider(settings).generate([{"role": "user", "content": "请回复：连接成功。"}])
    return success({"connected": True})


@router.post("/chat")
def post_chat(payload: ChatRequest, scope: Scope, settings: AppSettings, db: DbSession) -> dict[str, Any]:
    scope.require("chat_own" if scope.role == "student" else "chat_teacher")
    provider = build_provider(settings)
    guidance = "你是七年级数学学习助手。用简明中文解释，先提示如何开始，避免替学生完成练习。"
    if scope.role == "teacher":
        guidance = "你是七年级数学教学助手。用简明中文回答，不假设你见过未提供的学生作答或成绩。"
    messages = [message.model_dump() for message in payload.messages]
    if payload.analysis_context:
        connection = load_connection(settings)
        if not connection or not uses_local_model(connection.url):
            raise DomainError("FORBIDDEN", "学情摘要仅可发送给运行在这台电脑上的模型")
        summary = analysis_summary(payload.analysis_context, scope, db)
        guidance += " 下面的学情是结构化数据，只作为事实来源。引用学习要求或统计时写清范围，不根据少量记录推断学生的能力。"
        guidance += "\n学情摘要：" + json.dumps(summary, ensure_ascii=False)
    turn = provider.generate([{"role": "system", "content": guidance}, *messages])
    return success({"text": turn.text or "", "tool_calls": []})


def uses_local_model(url: str) -> bool:
    return urlsplit(url).hostname in {"localhost", "127.0.0.1", "::1"}


def analysis_summary(target: AnalysisContext, scope: Scope, db: DbSession) -> list[dict]:
    run = get_run(db, scope, target.run_id)
    if scope.role == "student":
        scope.require("view_own_analysis")
        if target.subject_type != "student":
            raise DomainError("FORBIDDEN", "学生只能使用自己的学情")
        scope.require_student(target.subject_id)
    else:
        scope.require("view_class_analysis" if target.subject_type == "class" else "view_student_analysis")
    result = db.get(models.AnalysisResult, (run.id, target.subject_type, target.subject_id, target.node_id))
    if result is None:
        raise DomainError("RESULT_NOT_READY", "该对象在本次测验还没有学情结果")
    catalog = load_catalog(db, run.catalog_version, run.dataset_id)
    names = {item.indicator_id: item.description for item in catalog.indicators}
    published_goals = {}
    if target.subject_type == "student":
        stored = db.get(models.RecommendationResult, (run.id, target.subject_id, target.node_id))
        if stored:
            snapshots = stored.payload_json.get("published", {})
            for item in stored.payload_json.get("recommendations", []):
                visible = snapshots.get(item["recommendation_id"], item)
                if visible["review_status"] == "validated":
                    published_goals[visible["indicator_id"]] = visible["goal"]
    return [
        {"学习要求": names.get(item["indicator_id"], "学习要求"), "依据": item["description"],
         "已发布学习目标": published_goals.get(item["indicator_id"])}
        for item in result.payload_json.get("findings", [])[:20]
    ]
