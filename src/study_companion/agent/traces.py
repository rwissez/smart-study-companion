"""Agent 工具轨迹持久化（05 §11.2、06 §4.5）。

只记"调用过什么、参数摘要、结果引用、状态"，不落完整参数与返回正文：
轨迹用于复盘与审计，不是第二份业务数据。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from study_companion.db import models
from study_companion.db import version_repository as repo


def _result_refs(result: Any) -> list[str]:
    """从返回值里挑出可定位的引用键，不深拷贝内容。"""

    refs: list[str] = []
    if isinstance(result, dict):
        for key in ("record_ids", "record_id", "metric_ids", "indicator_ids", "finding_ids"):
            value = result.get(key)
            if isinstance(value, list):
                refs.extend(str(item) for item in value if isinstance(item, (str, int)))
            elif isinstance(value, (str, int)):
                refs.append(str(value))
    return sorted(set(refs))[:50]


def record_tool_traces(
    db: Session,
    *,
    traces: list[Any],
    conversation_id: str,
    scope_payload: dict[str, Any],
) -> int:
    """把一轮对话里的工具调用写入 ``tool_traces``；同一 call_id 不重复。"""

    written = 0
    scope_digest = repo.digest(scope_payload)
    for index, trace in enumerate(traces):
        call_id = getattr(trace, "call_id", "") or f"{conversation_id}-{index}"
        existing = db.get(models.ToolTrace, call_id)
        if existing is not None:
            continue
        error = getattr(trace, "error", None) or {}
        db.add(
            models.ToolTrace(
                call_id=call_id,
                job_id=None,
                step_key=f"chat:{conversation_id}",
                tool_name=getattr(trace, "tool_name", "") or "",
                args_digest=repo.digest(getattr(trace, "arguments", {}) or {}),
                scope_digest=scope_digest,
                result_refs_json=_result_refs(getattr(trace, "result", None)),
                evidence_refs_json=list(getattr(trace, "evidence_refs", []) or []),
                status=getattr(trace, "status", "succeeded") or "succeeded",
                error_code=(error.get("code") if isinstance(error, dict) else None),
                message=str(error.get("message", "")) if isinstance(error, dict) else "",
                elapsed_ms=0,
                created_at=repo.now_iso(),
            )
        )
        written += 1
    if written:
        db.flush()
    return written
