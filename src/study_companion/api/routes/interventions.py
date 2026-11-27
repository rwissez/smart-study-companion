"""教师介入案例的读取与结案（01 §9.3、04 §7、06 §4.4）。

要点：

* 案例按 ``(scope, target, reason_code)`` upsert 开放行，历史不覆盖；
* 结案只对同一 run/node/student 的 open 案例生效，按 ``expected_last_cutoff``
  做乐观并发校验，冲突在 details 里回报当前水位；
* 结案写状态、``resolved_at`` 与 ``system_audit``，**不改**评分、学情与已发布
  方案；处理轨迹从审计读取，不另存一份历史。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Header, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from study_companion.api import idempotency as idempotency_api
from study_companion.api.deps import DbSession, Scope
from study_companion.api.envelope import success
from study_companion.contracts import DomainError
from study_companion.db import models
from study_companion.db import version_repository as repo
from study_companion.knowledge.review_service import idempotency_context

router = APIRouter(tags=["interventions"])

ENDPOINT_RESOLVE = "intervention.resolve"

OPEN = "open"
RESOLVED = "resolved"


class ResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(min_length=1, max_length=64)
    expected_last_cutoff: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=500)


# --------------------------------------------------------------------------- #
# 案例产生（供测试与后续 C 调用）
# --------------------------------------------------------------------------- #


def scope_key_of(scope: dict[str, Any]) -> str:
    """稳定可读的作用域键：按字段名排序的 ``k=v`` 串。"""

    return ";".join(f"{key}={scope[key]}" for key in sorted(scope))


def ensure_intervention(
    db: Session,
    dataset: models.Dataset,
    student_uid: str,
    scope: dict[str, Any],
    target: str,
    reason_code: str,
    payload: dict[str, Any] | None = None,
) -> models.InterventionRow:
    """按 (scope,target,reason) upsert 开放介入案例（04 §7）。

    已存在开放案例时合并新的证据与建议，不覆盖历史；``last_cutoff`` 只前进不
    回退，避免旧任务把水位写低。
    """

    if not student_uid:
        raise DomainError("VALIDATION_ERROR", "介入案例必须绑定学生身份")
    if not reason_code or len(reason_code) > 64:
        raise DomainError("VALIDATION_ERROR", "介入原因码必须是 1 到 64 个字符")
    if not isinstance(scope, dict) or not scope:
        raise DomainError("VALIDATION_ERROR", "介入作用域不能为空")
    if not target or len(target) > 256:
        raise DomainError("VALIDATION_ERROR", "介入目标必须是 1 到 256 个字符")
    payload = dict(payload or {})
    _validate_evidence_refs(payload.get("evidence_refs") or [])

    normalized_scope = {"dataset_id": dataset.id, **scope}
    key = scope_key_of(normalized_scope)
    existing = db.scalar(
        select(models.InterventionRow).where(
            models.InterventionRow.student_uid == student_uid,
            models.InterventionRow.scope_key == key,
            models.InterventionRow.target_key == target,
            models.InterventionRow.reason_code == reason_code,
            models.InterventionRow.status == OPEN,
        )
    )
    cutoff = int(payload.get("last_cutoff") or 0)
    if existing is not None:
        merged = dict(existing.payload_json or {})
        for field, value in payload.items():
            if field == "last_cutoff":
                continue
            if field in {"evidence_refs", "trial_ids", "observed_effects", "suggested_actions"}:
                merged[field] = _merge_unique(merged.get(field), value)
            elif value not in (None, ""):
                merged[field] = value
        merged["scope"] = normalized_scope
        existing.payload_json = merged
        existing.last_cutoff = max(existing.last_cutoff or 0, cutoff)
        db.flush()
        return existing

    row = models.InterventionRow(
        case_id=repo.new_id("case"),
        student_uid=student_uid,
        scope_key=key,
        target_key=target,
        reason_code=reason_code,
        payload_json={**payload, "scope": normalized_scope, "last_cutoff": cutoff},
        status=OPEN,
        last_cutoff=cutoff,
        opened_at=repo.now_iso(),
        resolved_at=None,
    )
    db.add(row)
    db.flush()
    _audit(
        db,
        scope_key=key,
        actor_ref=None,
        action="intervention_open",
        subject_ref=row.case_id,
        details={
            "reason_code": reason_code,
            "target_key": target,
            "student_uid": student_uid,
            "last_cutoff": cutoff,
        },
    )
    db.flush()
    return row


def _merge_unique(current: Any, extra: Any) -> list[Any]:
    values = list(current) if isinstance(current, list) else []
    for item in extra if isinstance(extra, list) else []:
        if item not in values:
            values.append(item)
    return values


def _validate_evidence_refs(items: Any) -> None:
    if not isinstance(items, list):
        raise DomainError("VALIDATION_ERROR", "evidence_refs 必须是数组")
    from study_companion.contracts import EvidenceRef

    for item in items:
        try:
            EvidenceRef.model_validate(item)
        except Exception as exc:  # pydantic.ValidationError
            raise DomainError(
                "VALIDATION_ERROR",
                "证据引用不合法",
                [{"field": "evidence_refs", "reason": str(exc)[:200]}],
            ) from exc


# --------------------------------------------------------------------------- #
# 授权与投影
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _CaseContext:
    dataset: models.Dataset
    student: models.Student
    student_uid: str
    run_id: str


def _dataset_for_run(db: Session, scope: Scope, run_id: str) -> models.Dataset:
    run = db.get(models.AnalysisRun, run_id)
    if run is None:
        raise DomainError("NOT_FOUND", "分析运行不存在")
    dataset = db.get(models.Dataset, run.dataset_id)
    if dataset is None:
        raise DomainError("NOT_FOUND", "数据集不存在")
    if scope.dataset_id is not None and scope.dataset_id != dataset.id:
        raise DomainError("FORBIDDEN", "当前会话无权访问该运行")
    if scope.dataset_family_id and dataset.family_id and scope.dataset_family_id != dataset.family_id:
        raise DomainError("CONTEXT_MISMATCH", "分析运行不属于当前授权的数据系列")
    return dataset


def _member(
    db: Session, scope: Scope, dataset: models.Dataset, student_id: str
) -> tuple[models.Student, str]:
    student = db.get(models.Student, (dataset.id, student_id))
    if student is None:
        raise DomainError("NOT_FOUND", "该数据集中不存在此学生")
    scope.require_class(student.class_id)
    student_uid = student.student_uid
    if not student_uid:
        identity = db.scalar(
            select(models.StudentIdentity).where(
                models.StudentIdentity.family_id == dataset.family_id,
                models.StudentIdentity.external_id == student_id,
            )
        )
        student_uid = identity.student_uid if identity is not None else None
    if not student_uid:
        raise DomainError("CONTEXT_MISMATCH", "该学生缺少稳定身份，不能关联介入案例")
    return student, student_uid


def _case_scope(row: models.InterventionRow) -> dict[str, Any]:
    payload = dict(row.payload_json or {})
    scope = payload.get("scope")
    if isinstance(scope, dict):
        return dict(scope)
    result: dict[str, Any] = {}
    for fragment in (row.scope_key or "").split(";"):
        if "=" in fragment:
            key, value = fragment.split("=", 1)
            result[key] = value
    return result


def _load_case(
    db: Session,
    *,
    scope: Scope,
    run_id: str,
    student_id: str,
    case_id: str,
    node_id: str,
) -> tuple[models.InterventionRow, _CaseContext]:
    dataset = _dataset_for_run(db, scope, run_id)
    student, student_uid = _member(db, scope, dataset, student_id)
    row = db.get(models.InterventionRow, case_id)
    if row is None:
        raise DomainError("NOT_FOUND", "介入案例不存在")
    if row.student_uid != student_uid:
        raise DomainError("FORBIDDEN", "该介入案例不属于该学生")
    case_scope = _case_scope(row)
    if case_scope.get("run_id") != run_id:
        raise DomainError("CONTEXT_MISMATCH", "介入案例不属于该分析运行")
    if case_scope.get("node_id") != node_id:
        raise DomainError("CONTEXT_MISMATCH", "介入案例不属于该节点")
    if case_scope.get("dataset_id") not in (None, dataset.id):
        raise DomainError("CONTEXT_MISMATCH", "介入案例不属于该数据集")
    return row, _CaseContext(
        dataset=dataset, student=student, student_uid=student_uid, run_id=run_id
    )


def _handling_history(db: Session, case_id: str) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(models.SystemAudit)
        .where(
            models.SystemAudit.subject_ref == case_id,
            models.SystemAudit.action == "intervention_resolved",
        )
        .order_by(models.SystemAudit.created_at)
    ).all()
    history: list[dict[str, Any]] = []
    for row in rows:
        details = dict(row.details_json or {})
        history.append(
            {
                "handled_at": row.created_at,
                "action": "resolved",
                "reason": str(details.get("reason") or ""),
                "actor_ref": row.actor_ref,
            }
        )
    return history


def _intervention_view(db: Session, row: models.InterventionRow) -> dict[str, Any]:
    payload = dict(row.payload_json or {})
    identity = db.get(models.StudentIdentity, row.student_uid)
    student_id = identity.external_id if identity is not None else ""
    status: Literal["open", "resolved"] = RESOLVED if row.status == RESOLVED else OPEN
    view: dict[str, Any] = {
        "case_id": row.case_id,
        "scope": _case_scope(row),
        "target_key": row.target_key,
        "reason_code": row.reason_code,
        "status": status,
        "evidence_refs": [
            item for item in payload.get("evidence_refs") or [] if isinstance(item, dict)
        ],
        "trial_ids": [str(item) for item in payload.get("trial_ids") or []],
        "observed_effects": [str(item) for item in payload.get("observed_effects") or []],
        "unresolved_question": str(payload.get("unresolved_question") or ""),
        "suggested_actions": [str(item) for item in payload.get("suggested_actions") or []],
        "opened_at": row.opened_at,
        "last_cutoff": row.last_cutoff,
        "resolved_at": row.resolved_at,
        "student_id": student_id,
        "handling_history": _handling_history(db, row.case_id),
    }
    try:
        from study_companion.contracts import Intervention

        return Intervention.model_validate(view).model_dump(mode="json")
    except Exception:
        # 历史行可能带非契约字段；投影仍返回可读结构，不因一条旧记录整页失败。
        return view


def _audit(
    db: Session,
    *,
    scope_key: str,
    actor_ref: str | None,
    action: str,
    subject_ref: str,
    details: dict[str, Any],
) -> None:
    db.add(
        models.SystemAudit(
            audit_id=repo.new_id("audit"),
            scope_key=scope_key,
            actor_ref=actor_ref,
            action=action,
            subject_ref=subject_ref,
            before_ref=None,
            after_ref=None,
            details_json=details,
            created_at=repo.now_iso(),
        )
    )


# --------------------------------------------------------------------------- #
# HTTP 接口
# --------------------------------------------------------------------------- #


@router.get("/runs/{run_id}/students/{student_id}/interventions/{case_id}")
def get_intervention(
    run_id: str,
    student_id: str,
    case_id: str,
    db: DbSession,
    scope: Scope,
    node_id: str = Query(min_length=1, max_length=64),
) -> dict:
    scope.require("view_student_analysis")
    scope.require_teacher()
    row, _ = _load_case(
        db,
        scope=scope,
        run_id=run_id,
        student_id=student_id,
        case_id=case_id,
        node_id=node_id,
    )
    return success(_intervention_view(db, row))


@router.post("/runs/{run_id}/students/{student_id}/interventions/{case_id}/resolve")
def resolve_intervention(
    run_id: str,
    student_id: str,
    case_id: str,
    payload: ResolveRequest,
    db: DbSession,
    scope: Scope,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", max_length=128)
    ] = None,
) -> dict:
    scope.require("resolve_intervention")
    scope.require_teacher()
    row, _ = _load_case(
        db,
        scope=scope,
        run_id=run_id,
        student_id=student_id,
        case_id=case_id,
        node_id=payload.node_id,
    )

    idempotency = idempotency_context(
        scope,
        ENDPOINT_RESOLVE,
        idempotency_key,
        {
            "run_id": run_id,
            "student_id": student_id,
            "case_id": case_id,
            "node_id": payload.node_id,
            "expected_last_cutoff": payload.expected_last_cutoff,
            "reason": payload.reason,
        },
    )
    if idempotency is not None:
        cached = idempotency_api.find_receipt(
            db,
            session_id=idempotency.session_id,
            endpoint=idempotency.endpoint,
            key=idempotency.key,
            digest=idempotency.digest,
        )
        if cached is not None:
            return success(cached)

    if row.status != OPEN:
        raise DomainError("CONFLICT", "该介入案例已结案，不能重复结案")
    if payload.expected_last_cutoff != (row.last_cutoff or 0):
        raise DomainError(
            "REVISION_CONFLICT",
            "介入案例的证据水位已更新，请刷新后再结案",
            [
                {
                    "field": "expected_last_cutoff",
                    "reason": f"当前 last_cutoff 为 {row.last_cutoff or 0}",
                }
            ],
        )

    reason = payload.reason.strip()
    if not reason:
        raise DomainError("VALIDATION_ERROR", "结案理由不能为空")

    previous_status = row.status
    row.status = RESOLVED
    row.resolved_at = repo.now_iso()
    _audit(
        db,
        scope_key=row.scope_key,
        actor_ref=scope.session_id,
        action="intervention_resolved",
        subject_ref=row.case_id,
        details={
            "reason": reason,
            "previous_status": previous_status,
            "status": RESOLVED,
            "node_id": payload.node_id,
            "expected_last_cutoff": payload.expected_last_cutoff,
        },
    )
    db.flush()
    receipt = _intervention_view(db, row)
    if idempotency is not None:
        idempotency_api.store_receipt(
            db,
            session_id=idempotency.session_id,
            endpoint=idempotency.endpoint,
            key=idempotency.key,
            digest=idempotency.digest,
            receipt=receipt,
            result_ref=row.case_id,
        )
    return success(receipt)
