"""学习闭环：开始活动、在线作答、三级提示、提交评分、完成与事件读取。

红线（01 §7、§10.3）：
* 服务端校验活动归属、计划版本、学生本人与活动状态；
* 独立性只由真实帮助记录决定，客户端不能申报；
* 未完成题目不下发答案、解析与评分细则；
* 每次状态推进写唯一事件账本，评分修订保留原作答。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Header, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from study_companion.analysis.grading import grade_attempt
from study_companion.api.deps import DbSession, Scope
from study_companion.api.envelope import success
from study_companion.api.idempotency import find_receipt, request_digest, store_receipt
from study_companion.contracts import (
    Attempt,
    DomainError,
    GradingResult,
    Material,
    RecommendationBundle,
)
from study_companion.db import models
from study_companion.db import version_repository as repo
from study_companion.library import learning_events as ledger
from study_companion.model_service import ModelService

router = APIRouter(tags=["learning"])

ENDPOINT_SUBMIT = "attempts.submit"
ENDPOINT_HINT = "attempts.hint"


class SubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    final_answer: str | None = Field(default=None, max_length=8000)
    solution_steps: str | None = Field(default=None, max_length=20000)
    unanswered: bool = False


class HintRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: int = Field(ge=1, le=3)
    allow_assisted: bool = False


# --------------------------------------------------------------------------- #
# 上下文解析
# --------------------------------------------------------------------------- #


def _plan_payload(db: DbSession, plan_version: str) -> RecommendationBundle:
    version = db.get(models.Version, plan_version)
    if version is None or version.kind != "plan" or version.payload_json is None:
        raise DomainError("NOT_FOUND", "学习方案版本不存在")
    return RecommendationBundle.model_validate(version.payload_json)


def _plan_activity(db: DbSession, plan_version: str, activity_id: str) -> models.PlanActivity:
    row = db.get(models.PlanActivity, (plan_version, activity_id))
    if row is None:
        raise DomainError("NOT_FOUND", "该方案版本中没有此活动")
    return row


def _student_ctx(db: DbSession, scope: Scope, dataset_id: str, student_id: str) -> tuple[str, models.Dataset]:
    if scope.role == "student" and scope.student_id != student_id:
        raise DomainError("FORBIDDEN", "学生会话只能访问本人学习数据")
    dataset = db.get(models.Dataset, dataset_id)
    if dataset is None:
        raise DomainError("NOT_FOUND", "数据集不存在")
    if scope.role == "student" and scope.dataset_id and scope.dataset_id != dataset_id:
        raise DomainError("FORBIDDEN", "学生会话只能访问本人数据集")
    if not dataset.family_id:
        raise DomainError("UNPROCESSABLE", "数据集缺少系列身份")
    return ledger.student_uid_for(db, dataset.family_id, student_id), dataset


def _activity_state(
    db: DbSession, activity_id: str, student_uid: str
) -> models.ActivityStateRow:
    row = db.get(models.ActivityStateRow, activity_id)
    if row is None or row.student_uid != student_uid:
        raise DomainError("FORBIDDEN", "当前学生没有该活动的学习记录")
    return row


def _node_for_plan(db: DbSession, path: RecommendationBundle) -> str:
    return path.node_id


def _attempt_contract(db: DbSession, row: models.AttemptRow, student_id: str) -> Attempt:
    payload = dict(row.answer_json or {})
    return Attempt(
        attempt_id=row.attempt_id,
        student_id=student_id,
        dataset_id=(db.get(models.Version, row.plan_version).dataset_id if db.get(models.Version, row.plan_version) else ""),
        plan_version=row.plan_version,
        activity_id=row.activity_id,
        material_id=row.material_id,
        submitted_at=row.submitted_at,
        final_answer=payload.get("final_answer"),
        solution_steps=payload.get("solution_steps"),
        status=row.status,  # type: ignore[arg-type]
        latest_grade_id=row.latest_grade_id,
        assistance_status=row.assistance_status,  # type: ignore[arg-type]
        submission_no=row.submission_no,
    )


def attempt_view(db: DbSession, row: models.AttemptRow, student_id: str) -> dict[str, Any]:
    payload = dict(row.answer_json or {})
    return {
        "attempt_id": row.attempt_id,
        "student_id": student_id,
        "plan_version": row.plan_version,
        "activity_id": row.activity_id,
        "material_id": row.material_id,
        "status": row.status,
        "submitted_at": row.submitted_at,
        "final_answer": payload.get("final_answer"),
        "solution_steps": payload.get("solution_steps"),
        "assistance_status": row.assistance_status,
        "hint_max_level": payload.get("hint_max_level"),
        "agent_help_count": payload.get("agent_help_count"),
        "submission_no": row.submission_no,
        "latest_grade_id": row.latest_grade_id,
    }


def grade_view(row: models.Grade, *, reveal: bool) -> dict[str, Any]:
    payload = dict(row.payload_json or {})
    if not reveal:
        return {
            "grade_id": row.grade_id,
            "attempt_id": row.attempt_id,
            "revision": row.revision,
            "status": row.status,
            "validation_status": row.validation_status,
            "score": None,
            "max_score": row.max_score,
            "rubric_scores": [],
            "method": payload.get("method", "rule"),
            "limitations": payload.get("limitations", []),
            "created_at": row.created_at,
        }
    return {
        "grade_id": row.grade_id,
        "attempt_id": row.attempt_id,
        "revision": row.revision,
        "status": row.status,
        "validation_status": row.validation_status,
        "score": row.score,
        "max_score": row.max_score,
        "rubric_scores": payload.get("rubric_scores", []),
        "method": payload.get("method", "rule"),
        "limits": payload.get("limitations", []),
        "limitations": payload.get("limitations", []),
        "created_at": row.created_at,
    }


def _material(db: DbSession, catalog_version: str, material_id: str) -> Material:
    row = db.get(models.Material, (catalog_version, material_id))
    if row is None:
        raise DomainError("NOT_FOUND", "题目材料不存在")
    return Material.model_validate(dict(row.payload_json))


# --------------------------------------------------------------------------- #
# 开始活动
# --------------------------------------------------------------------------- #


@router.post("/plans/{plan_version}/activities/{activity_id}/start")
def start_activity(
    plan_version: str,
    activity_id: str,
    db: DbSession,
    scope: Scope,
) -> dict:
    scope.require("view_own_plan") if scope.role == "student" else scope.require("view_student_analysis")
    version = db.get(models.Version, plan_version)
    if version is None or version.kind != "plan":
        raise DomainError("NOT_FOUND", "学习方案版本不存在")
    student_id = version.target_key
    student_uid, dataset = _student_ctx(db, scope, version.dataset_id, student_id)
    activity = _plan_activity(db, plan_version, activity_id)
    state = _activity_state(db, activity_id, student_uid)

    if state.state == "completed":
        return success({"activity_id": activity_id, "state": "completed", "event_id": None})
    if state.state == "cancelled":
        raise DomainError("PLAN_SUSPENDED", "该活动已暂停，不能开始")
    if activity.kind == "question":
        attempt = _open_attempt(db, activity, student_uid, plan_version)
        return success(
            {
                "activity_id": activity_id,
                "state": "in_progress",
                "event_id": None,
                "attempt": attempt_view(db, attempt, student_id),
            }
        )

    if state.state == "not_started":
        state.state = "in_progress"
        state.generation += 1
        state.updated_at = ledger.now_iso()
    payload: dict[str, Any] = {"activity_kind": activity.kind}
    if activity.kind == "micro_lesson":
        payload["content_ref"] = {"content_id": activity.content_version or "", "content_version": activity.content_version or ""}
    run_id = repo.active_run_id(db, dataset.id, version.node_key)
    event = ledger.append(
        db,
        family_id=dataset.family_id,
        dataset_id=dataset.id,
        run_id=run_id,
        student_uid=student_uid,
        node_id=version.node_key,
        plan_version=plan_version,
        group_id=activity.group_id,
        activity_id=activity_id,
        material_id=activity.material_id,
        event_type="activity_started",
        source="student",
        payload=payload,
        occurred_at=ledger.now_iso(),
    )
    return success({"activity_id": activity_id, "state": state.state, "event_id": event.event_id})


def _open_attempt(
    db: DbSession, activity: models.PlanActivity, student_uid: str, plan_version: str
) -> models.AttemptRow:
    existing = db.scalar(
        select(models.AttemptRow)
        .where(
            models.AttemptRow.activity_id == activity.activity_id,
            models.AttemptRow.student_uid == student_uid,
            models.AttemptRow.status == "in_progress",
        )
        .order_by(models.AttemptRow.created_at.desc())
    )
    if existing is not None:
        return existing
    row = models.AttemptRow(
        attempt_id=ledger.new_id("att"),
        plan_version=plan_version,
        activity_id=activity.activity_id,
        student_uid=student_uid,
        catalog_version=activity.catalog_version or "",
        material_id=activity.material_id or "",
        status="in_progress",
        created_at=ledger.now_iso(),
        answer_json={},
    )
    db.add(row)
    db.flush()
    return row


@router.post("/plans/{plan_version}/activities/{activity_id}/attempts", status_code=201)
def create_attempt(plan_version: str, activity_id: str, db: DbSession, scope: Scope) -> dict:
    version = db.get(models.Version, plan_version)
    if version is None or version.kind != "plan":
        raise DomainError("NOT_FOUND", "学习方案版本不存在")
    student_uid, dataset = _student_ctx(db, scope, version.dataset_id, version.target_key)
    activity = _plan_activity(db, plan_version, activity_id)
    if activity.kind != "question":
        raise DomainError("UNPROCESSABLE", "只有题目活动需要作答尝试")
    state = _activity_state(db, activity_id, student_uid)
    if state.state in {"completed", "cancelled"}:
        raise DomainError("PLAN_SUSPENDED", "该活动已完成或暂停，不能新建作答")
    attempt = _open_attempt(db, activity, student_uid, plan_version)
    return success(attempt_view(db, attempt, version.target_key))


# --------------------------------------------------------------------------- #
# 提交与评分
# --------------------------------------------------------------------------- #


@router.post("/attempts/{attempt_id}/submit")
def submit_attempt(
    attempt_id: str,
    payload: SubmitRequest,
    db: DbSession,
    scope: Scope,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=128)] = None,
) -> dict:
    row = db.get(models.AttemptRow, attempt_id)
    if row is None:
        raise DomainError("NOT_FOUND", "作答尝试不存在")
    version = db.get(models.Version, row.plan_version)
    if version is None:
        raise DomainError("NOT_FOUND", "学习方案版本不存在")
    student_uid, dataset = _student_ctx(db, scope, version.dataset_id, version.target_key)
    if row.student_uid != student_uid:
        raise DomainError("FORBIDDEN", "不能提交他人的作答")

    token = (idempotency_key or "").strip() or None
    digest = request_digest(
        {
            "attempt": attempt_id,
            "final_answer": payload.final_answer,
            "solution_steps": payload.solution_steps,
            "unanswered": payload.unanswered,
        }
    )
    # 幂等回执必须先于状态检查：同键重放要返回原结果，而不是"已提交"冲突。
    if token:
        cached = find_receipt(
            db, session_id=scope.session_id, endpoint=ENDPOINT_SUBMIT, key=token, digest=digest
        )
        if cached:
            return success(cached)
    if row.status != "in_progress":
        raise DomainError("CONFLICT", "该尝试已提交，不能重复提交")

    if payload.unanswered:
        final_answer = None
        solution_steps = None
    else:
        final_answer = (payload.final_answer or "").strip() or None
        solution_steps = (payload.solution_steps or "").strip() or None
        if not final_answer and not solution_steps:
            raise DomainError("VALIDATION_ERROR", "请填写答案或解题过程，或显式标记未作答")

    row.submission_no += 1
    row.status = "submitted"
    row.submitted_at = ledger.now_iso()
    row.answer_json = {
        **(row.answer_json or {}),
        "final_answer": final_answer,
        "solution_steps": solution_steps,
        "unanswered": payload.unanswered,
    }
    activity = _plan_activity(db, row.plan_version, row.activity_id)
    submitted = ledger.append(
        db,
        family_id=dataset.family_id,
        dataset_id=dataset.id,
        run_id=repo.active_run_id(db, dataset.id, version.node_key),
        student_uid=student_uid,
        node_id=version.node_key,
        plan_version=row.plan_version,
        group_id=activity.group_id,
        activity_id=row.activity_id,
        material_id=row.material_id,
        attempt_id=row.attempt_id,
        event_type="answer_submitted",
        source="student",
        payload={
            "attempt_id": row.attempt_id,
            "submission_no": row.submission_no,
            "final_answer": final_answer,
            "solution_steps": solution_steps,
        },
        occurred_at=row.submitted_at,
        dedupe={"submission_no": row.submission_no},
    )

    material = _material(db, activity.catalog_version or "", row.material_id)
    attempt = _attempt_contract(db, row, version.target_key)
    grade = grade_attempt(
        attempt,
        material,
        grade_id=ledger.new_id("grade"),
        revision=1,
        service=ModelService(),
    )
    grade_row = models.Grade(
        grade_id=grade.grade_id,
        attempt_id=row.attempt_id,
        revision=grade.revision,
        status=grade.status,
        validation_status=grade.validation_status,
        score=grade.score,
        max_score=grade.max_score,
        payload_json=grade.model_dump(mode="json"),
        created_at=ledger.now_iso(),
    )
    db.add(grade_row)
    row.latest_grade_id = grade.grade_id
    if grade.score is not None:
        row.status = "completed"
    ledger.append(
        db,
        family_id=dataset.family_id,
        dataset_id=dataset.id,
        run_id=repo.active_run_id(db, dataset.id, version.node_key),
        student_uid=student_uid,
        node_id=version.node_key,
        plan_version=row.plan_version,
        group_id=activity.group_id,
        activity_id=row.activity_id,
        material_id=row.material_id,
        attempt_id=row.attempt_id,
        event_type="grading_completed",
        source="system",
        payload={
            "grade_id": grade.grade_id,
            "attempt_id": row.attempt_id,
            "revision": grade.revision,
            "grading_status": grade.status,
            "score": grade.score,
            "max_score": grade.max_score,
            "rubric_scores": [item.model_dump(mode="json") for item in grade.rubric_scores],
            "validation_status": grade.validation_status,
        },
        occurred_at=ledger.now_iso(),
        dedupe={"revision": grade.revision},
    )

    group_state = None
    if grade.score is not None:
        group_state = _complete_activity(db, dataset, version, activity, student_uid, row.attempt_id)
    db.flush()

    receipt = {
        "attempt": attempt_view(db, row, version.target_key),
        "grade": grade_view(grade_row, reveal=grade.status == "graded"),
        "job_id": None,
        "feedback_status": grade.status,
        "group_status": group_state,
    }
    if token:
        store_receipt(
            db,
            session_id=scope.session_id,
            endpoint=ENDPOINT_SUBMIT,
            key=token,
            digest=digest,
            receipt=receipt,
            result_ref=row.attempt_id,
        )
    if group_state and group_state.get("group_completed"):
        from study_companion.jobs.worker import ensure_worker_started

        ensure_worker_started()
    return success(receipt)


def _complete_activity(
    db: DbSession,
    dataset: models.Dataset,
    version: models.Version,
    activity: models.PlanActivity,
    student_uid: str,
    attempt_id: str | None,
    *,
    completion_status: str = "completed",
) -> dict[str, Any]:
    state = _activity_state(db, activity.activity_id, student_uid)
    if state.state == "completed":
        return {"group_completed": False, "state": "completed"}
    state.state = "completed"
    state.generation += 1
    state.updated_at = ledger.now_iso()
    run_id = repo.active_run_id(db, dataset.id, version.node_key)
    ledger.append(
        db,
        family_id=dataset.family_id,
        dataset_id=dataset.id,
        run_id=run_id,
        student_uid=student_uid,
        node_id=version.node_key,
        plan_version=version.version_id,
        group_id=activity.group_id,
        activity_id=activity.activity_id,
        material_id=activity.material_id,
        attempt_id=attempt_id,
        event_type="activity_completed",
        source="system",
        payload={"completion_status": completion_status, "attempt_id": attempt_id},
        occurred_at=ledger.now_iso(),
    )
    required = ledger.group_required_activities(db, version.version_id, activity.group_id)
    completed = ledger.activity_completed_ids(db, student_uid, version.version_id)
    group_done = bool(required) and set(required) <= completed
    if group_done:
        ledger.finalize_group_completion(
            db,
            dataset=dataset,
            plan_version=version.version_id,
            node_id=version.node_key,
            group_id=activity.group_id,
            student_uid=student_uid,
            student_id=version.target_key,
            run_id=run_id,
        )
    db.flush()
    return {"group_completed": group_done, "state": state.state}


@router.post("/plans/{plan_version}/activities/{activity_id}/complete")
def complete_material(
    plan_version: str, activity_id: str, db: DbSession, scope: Scope
) -> dict:
    version = db.get(models.Version, plan_version)
    if version is None or version.kind != "plan":
        raise DomainError("NOT_FOUND", "学习方案版本不存在")
    student_uid, dataset = _student_ctx(db, scope, version.dataset_id, version.target_key)
    activity = _plan_activity(db, plan_version, activity_id)
    if activity.kind == "question":
        raise DomainError("UNPROCESSABLE", "题目活动请通过提交作答完成")
    state = _activity_state(db, activity_id, student_uid)
    if state.state == "cancelled":
        raise DomainError("PLAN_SUSPENDED", "该活动已暂停")
    if activity.kind == "micro_lesson":
        raise DomainError("UNPROCESSABLE", "微课堂请使用专用完成接口")
    run_id = repo.active_run_id(db, dataset.id, version.node_key)
    ledger.append(
        db,
        family_id=dataset.family_id,
        dataset_id=dataset.id,
        run_id=run_id,
        student_uid=student_uid,
        node_id=version.node_key,
        plan_version=plan_version,
        group_id=activity.group_id,
        activity_id=activity_id,
        material_id=activity.material_id,
        event_type="study_material_completed",
        source="student",
        payload={
            "catalog_version": activity.catalog_version or "",
            "material_id": activity.material_id or "",
        },
        occurred_at=ledger.now_iso(),
    )
    result = _complete_activity(db, dataset, version, activity, student_uid, None)
    if result["group_completed"]:
        from study_companion.jobs.worker import ensure_worker_started

        ensure_worker_started()
    return success(
        {
            "activity_id": activity_id,
            "state": result["state"],
            "event_id": None,
            "group_completed": result["group_completed"],
        }
    )


# --------------------------------------------------------------------------- #
# 三级提示
# --------------------------------------------------------------------------- #


@router.post("/attempts/{attempt_id}/hints")
def request_hint(
    attempt_id: str,
    payload: HintRequest,
    db: DbSession,
    scope: Scope,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=128)] = None,
) -> dict:
    row = db.get(models.AttemptRow, attempt_id)
    if row is None:
        raise DomainError("NOT_FOUND", "作答尝试不存在")
    version = db.get(models.Version, row.plan_version)
    if version is None:
        raise DomainError("NOT_FOUND", "学习方案版本不存在")
    student_uid, dataset = _student_ctx(db, scope, version.dataset_id, version.target_key)
    if row.student_uid != student_uid:
        raise DomainError("FORBIDDEN", "不能请求他人的作答提示")
    if row.status != "in_progress":
        raise DomainError("CONFLICT", "已提交的作答不能继续请求提示")

    activity = _plan_activity(db, row.plan_version, row.activity_id)
    plan = _plan_payload(db, row.plan_version)
    hint_levels: list[str] = []
    for rec in plan.recommendations:
        for item in rec.activities:
            if item.activity_id == row.activity_id:
                hint_levels = list(item.hint_levels)
    if payload.level > len(hint_levels):
        raise DomainError("NOT_FOUND", "该题没有这一级提示")
    hint_text = hint_levels[payload.level - 1]

    token = (idempotency_key or "").strip() or None
    digest = request_digest({"attempt": attempt_id, "level": payload.level})
    if token:
        cached = find_receipt(
            db, session_id=scope.session_id, endpoint=ENDPOINT_HINT, key=token, digest=digest
        )
        if cached:
            return success(cached)

    answer = dict(row.answer_json or {})
    current_max = int(answer.get("hint_max_level") or 0)
    if payload.level > current_max + 1:
        raise DomainError("UNPROCESSABLE", "提示需要逐级释放")
    if payload.level > current_max:
        answer["hint_max_level"] = payload.level
        row.answer_json = answer

    event = ledger.append(
        db,
        family_id=dataset.family_id,
        dataset_id=dataset.id,
        run_id=repo.active_run_id(db, dataset.id, version.node_key),
        student_uid=student_uid,
        node_id=version.node_key,
        plan_version=row.plan_version,
        group_id=activity.group_id,
        activity_id=row.activity_id,
        material_id=row.material_id,
        attempt_id=row.attempt_id,
        event_type="hint_requested",
        source="student",
        payload={"level": payload.level},
        occurred_at=ledger.now_iso(),
        dedupe={"level": payload.level},
    )
    if payload.allow_assisted or payload.level > current_max:
        # 实际释放了帮助（不论是否由学生显式声明受助），独立性判定据此记账。
        row.assistance_status = "assisted"
    db.flush()
    receipt = {"event_id": event.event_id, "level": payload.level, "hint": hint_text}
    if token:
        store_receipt(
            db,
            session_id=scope.session_id,
            endpoint=ENDPOINT_HINT,
            key=token,
            digest=digest,
            receipt=receipt,
            result_ref=event.event_id,
        )
    return success(receipt)


@router.get("/attempts/{attempt_id}")
def get_attempt(attempt_id: str, db: DbSession, scope: Scope) -> dict:
    row = db.get(models.AttemptRow, attempt_id)
    if row is None:
        raise DomainError("NOT_FOUND", "作答尝试不存在")
    version = db.get(models.Version, row.plan_version)
    if version is None:
        raise DomainError("NOT_FOUND", "学习方案版本不存在")
    student_uid, _dataset = _student_ctx(db, scope, version.dataset_id, version.target_key)
    if row.student_uid != student_uid and scope.role == "student":
        raise DomainError("FORBIDDEN", "不能查看他人的作答")
    grade_row = db.get(models.Grade, row.latest_grade_id) if row.latest_grade_id else None
    reveal = bool(grade_row and grade_row.status == "graded" and grade_row.validation_status == "validated")
    return success(
        {
            "attempt": attempt_view(db, row, version.target_key),
            "grade": grade_view(grade_row, reveal=reveal) if grade_row else None,
            "feedback": None,
            "group_status": None,
            "update_job_id": None,
        }
    )


# --------------------------------------------------------------------------- #
# 学习事件读取
# --------------------------------------------------------------------------- #


@router.get("/datasets/{dataset_id}/students/{student_id}/learning-events")
def list_learning_events(
    dataset_id: str,
    student_id: str,
    db: DbSession,
    scope: Scope,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
) -> dict:
    if scope.role == "student":
        scope.require("view_own_events")
    else:
        scope.require("view_learning_events")
    student_uid, _dataset = _student_ctx(db, scope, dataset_id, student_id)
    rows = db.scalars(
        select(models.LearningEventRow)
        .where(models.LearningEventRow.student_uid == student_uid)
        .order_by(models.LearningEventRow.offset.desc())
    ).all()
    start = (page - 1) * page_size
    items = [ledger.view(row, student_id) for row in rows[start : start + page_size]]
    return success({"items": items, "total": len(rows), "page": page, "page_size": page_size})
