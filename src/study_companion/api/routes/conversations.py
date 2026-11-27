"""对话列表与历史消息（01 §9.4）。

会话属于一个稳定的身份键（owner key），而不是某一次 demo session：换标签页
重新登录后仍是同一个 owner。服务端在 chat 路由按身份派生该键，这里只接受
与会话身份一致的值；旧会话仍按创建它的 demo session 或旧归属键兼容读取。

每次读取都重新校验所有权与数据集范围，历史文本不会被当作新任务的事实或
授权依据。
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import and_, or_, select

from study_companion.api.deps import DbSession, Scope
from study_companion.api.envelope import success
from study_companion.api.routes.chat import (
    OwnerKey,
    conversation_owned_by,
    legacy_owner_scope_key,
)
from study_companion.contracts import DomainError
from study_companion.db import models

router = APIRouter(tags=["conversations"])


def _owned_filter(scope, owner_key: str):
    """SQL 侧的归属条件，与 ``conversation_owned_by`` 保持同一套规则。"""

    return or_(
        models.Conversation.scope_key == owner_key,
        models.Conversation.owner_ref == scope.session_id,
        and_(
            models.Conversation.scope_key == legacy_owner_scope_key(scope),
            models.Conversation.role == scope.role,
            models.Conversation.dataset_id == scope.dataset_id,
        ),
    )


@router.get("/conversations")
def list_conversations(
    db: DbSession,
    scope: Scope,
    owner_key: OwnerKey,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
) -> dict:
    rows = db.scalars(
        select(models.Conversation)
        .where(_owned_filter(scope, owner_key))
        .order_by(models.Conversation.updated_at.desc())
    ).all()
    start = (page - 1) * page_size
    items = [
        {
            "conversation_id": row.conversation_id,
            "title": row.title,
            "role": row.role,
            "dataset_id": row.dataset_id,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "last_message": None,
        }
        for row in rows[start : start + page_size]
    ]
    # owner_key 回给前端持久化；它由服务端按身份派生，客户端改不了归属。
    return success(
        {
            "items": items,
            "total": len(rows),
            "page": page,
            "page_size": page_size,
            "owner_key": owner_key,
        }
    )


@router.get("/conversations/{conversation_id}/messages")
def list_messages(
    conversation_id: str,
    db: DbSession,
    scope: Scope,
    owner_key: OwnerKey,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> dict:
    conversation = db.get(models.Conversation, conversation_id)
    if conversation is None or not conversation_owned_by(
        conversation, scope, owner_key
    ):
        raise DomainError("NOT_FOUND", "会话不存在或不属于当前身份")
    rows = db.scalars(
        select(models.ChatMessageRow)
        .where(models.ChatMessageRow.conversation_id == conversation_id)
        .order_by(models.ChatMessageRow.sequence)
    ).all()
    start = (page - 1) * page_size
    items = []
    for row in rows[start : start + page_size]:
        payload = dict(row.payload_json or {})
        items.append(
            {
                "message_id": row.message_id,
                "conversation_id": row.conversation_id,
                "sequence": row.sequence,
                "role": row.role,
                "content": row.content,
                "status": row.status,
                "created_at": row.created_at,
                "segments": row.segments_json or [],
                "job_id": row.job_id,
                "tool_trace": payload.get("tool_trace", []),
                "evidence_refs": payload.get("evidence_refs", []),
                "confirmation": payload.get("confirmation"),
                "assisted_confirmation": payload.get("assisted_confirmation"),
                "clarification": payload.get("clarification"),
                "operation_results": payload.get("operation_results", []),
            }
        )
    return success(
        {"items": items, "total": len(rows), "page": page, "page_size": page_size}
    )
