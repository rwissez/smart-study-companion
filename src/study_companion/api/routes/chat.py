"""Student chat: wired end to end, honest about the missing model.

The route exists now so the frontend can build against a real URL, and it
fails with ``MODEL_NOT_CONFIGURED`` (503) rather than returning invented text.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from study_companion.agent import build_provider
from study_companion.api.deps import AppSettings, Scope
from study_companion.api.envelope import success

router = APIRouter(tags=["chat"])


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str = Field(pattern="^(system|user|assistant)$")
    content: str = Field(min_length=1, max_length=8000)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: list[ChatMessage] = Field(min_length=1, max_length=40)


@router.post("/chat")
def post_chat(payload: ChatRequest, scope: Scope, settings: AppSettings) -> dict[str, Any]:
    # Teachers and students both chat, under separate capabilities: a student
    # may only ever discuss their own data, a teacher their bound dataset.
    scope.require("chat_own" if scope.role == "student" else "chat_teacher")
    provider = build_provider(settings)
    turn = provider.generate([message.model_dump() for message in payload.messages])
    # Unreachable while the only provider is the unconfigured one.
    return success({"text": turn.text or "", "tool_calls": []})
