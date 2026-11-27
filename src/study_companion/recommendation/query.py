"""C 正式方案的只读查询入口（05 §6.2 的薄读取适配器）。

对应 04 的读服务：``get_plan`` / ``list_revisions`` / ``get_plan_reason`` /
``get_lesson_view`` / ``search_materials`` / ``prepare_revision_candidate``。

只读边界：
- 读取正式版本（``versions`` + ``formal_heads`` + ``publications``）与正式目录，
  不切换正式指针、不写业务事实、不重新生成方案正文或微课堂。
- :func:`prepare_revision_candidate` 只写一条候选（``proposals`` 行，``state=ready``），
  不创建 ``plan`` 版本、不动 ``formal_heads``；提交仍由教师确认后走 04 发布路径。
- 学生投影按 04/01 的答案保护裁掉参考答案与评分细则；教师内部信息不下发学生。

已知限制（写进各函数的 ``limitations``）：
- 04 尚未提供独立的 ``get_plan`` / ``list_revisions`` HTTP 读服务，本模块按
  04 §7—§9 的正式对象只读投影；正式读服务落地后应替换本实现。
- 候选入口目前只支持局部调整；需要 C 重新生成或新增微课堂的意图不在这里实现。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from study_companion.contracts import (
    DomainError,
    MaterialView,
    ProposalView,
    RecommendationBundle,
    RevisionChanges,
    RevisionIntent,
    RevisionSummary,
    Snapshot,
)
from study_companion.db import models
from study_companion.db import version_repository as repo

PAGE_DEFAULT = 20
PAGE_MAX = 50
#: 局部调整支持的意图种类；其余种类需要 C 重新生成，不在只读适配器里做。
_SUPPORTED_CHANGES = frozenset(
    {
        "reduce_questions",
        "remove_pending_activity",
        "reorder_pending",
        "replace_material",
        "adjust_goal",
        "update_intro",
    }
)


def _dataset(db: Session, dataset_id: str) -> models.Dataset:
    row = db.get(models.Dataset, dataset_id)
    if row is None:
        raise DomainError("NOT_FOUND", "数据集不存在")
    return row


def _clamp_page(page: Any, page_size: Any) -> tuple[int, int]:
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise DomainError("INVALID_ARGUMENT", "page 必须是 >=1 的整数")
    if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= PAGE_MAX:
        raise DomainError("INVALID_ARGUMENT", f"page_size 必须在 1..{PAGE_MAX} 之间")
    return page, page_size


def _plan_key(dataset: models.Dataset, student_id: str, node_id: str) -> repo.FormalKey:
    return repo.FormalKey(
        family_id=dataset.family_id or "",
        dataset_id=dataset.id,
        kind="plan",
        node_key=node_id,
        target_key=student_id,
    )


# --------------------------------------------------------------------------- #
# 正式方案
# --------------------------------------------------------------------------- #


def get_plan_version(
    db: Session,
    *,
    dataset_id: str,
    student_id: str,
    node_id: str | None = None,
    plan_version: str | None = None,
) -> models.Version:
    """读取指定或当前的正式方案版本；历史版本只按精确 ID 读取。"""

    dataset = _dataset(db, dataset_id)
    if plan_version:
        version = db.get(models.Version, plan_version)
        if version is None or version.kind != "plan" or version.dataset_id != dataset.id:
            raise DomainError("NOT_FOUND", "学习方案版本不存在")
        if version.target_key != student_id:
            raise DomainError("FORBIDDEN", "该方案不属于此学生")
        if node_id and version.node_key != node_id:
            raise DomainError("CONTEXT_MISMATCH", "方案版本与请求节点不一致")
        return version
    if node_id:
        version = repo.read_formal(db, _plan_key(dataset, student_id, node_id))
        if version is not None:
            return version
    heads = db.scalars(
        select(models.FormalHead)
        .where(
            models.FormalHead.dataset_id == dataset.id,
            models.FormalHead.kind == "plan",
            models.FormalHead.target_key == student_id,
            models.FormalHead.version_id.is_not(None),
        )
        .order_by(models.FormalHead.updated_at.desc())
    ).all()
    fallback: models.Version | None = None
    for head in heads:
        candidate = db.get(models.Version, head.version_id or "")
        if candidate is None or not candidate.payload_json:
            continue
        if fallback is None:
            fallback = candidate
        payload = candidate.payload_json or {}
        if any(item.get("activities") for item in payload.get("recommendations", []) or []):
            return candidate
    if fallback is not None:
        return fallback
    raise DomainError("RESULT_NOT_READY", "该学生尚无正式学习方案")


def plan_bundle(version: models.Version) -> RecommendationBundle:
    if not version.payload_json:
        raise DomainError("RESULT_NOT_READY", "方案版本正文不可读")
    try:
        return RecommendationBundle.model_validate(version.payload_json)
    except Exception as exc:
        raise DomainError("RESULT_NOT_READY", "方案版本正文不符合正式契约") from exc


def plan_projection(version: models.Version) -> dict[str, Any]:
    """正式方案的只读投影；不含参考答案与教师内部审核细节以外的内容。"""

    bundle = plan_bundle(version)
    return {
        "plan_version": version.version_id,
        "node_id": version.node_key,
        "student_id": version.target_key,
        "analysis_version": bundle.analysis_version,
        "catalog_version": bundle.catalog_version,
        "publication_status": bundle.publication_status,
        "review_status": (bundle.review_info.status if bundle.review_info else "draft"),
        "review_info": bundle.review_info.model_dump(mode="json") if bundle.review_info else None,
        "goal": (bundle.recommendations[0].goal if bundle.recommendations else ""),
        "reason": (bundle.recommendations[0].reason if bundle.recommendations else ""),
        "activities": [
            {
                "activity_id": activity.activity_id,
                "kind": activity.activity_kind,
                "material_id": activity.material_id,
                "content_version": activity.content_version,
                "group_id": activity.group_id,
                "title": activity.title,
                "minutes": activity.estimate_minutes,
                "exposure_mode": activity.exposure_mode,
                "state": activity.state,
            }
            for rec in bundle.recommendations
            for activity in rec.activities
        ],
        "limitations": list(bundle.limitations)
        + ["只读正式方案快照：不含参考答案与评分细则，活动状态以正式活动状态表为准"],
    }


# --------------------------------------------------------------------------- #
# 修订历史（04 §7）
# --------------------------------------------------------------------------- #


def _scope_keys(db: Session, dataset: models.Dataset, student_id: str) -> dict[str, str]:
    """把该学生在各节点的发布作用域键映射为节点，用于按学生过滤修订历史。"""

    from study_companion.recommendation.publication import scope_key_for

    nodes = db.scalars(select(models.Node).where(models.Node.dataset_id == dataset.id)).all()
    keys = {scope_key_for(dataset.id, student_id, node.node_id): node.node_id for node in nodes}
    heads = db.scalars(
        select(models.FormalHead).where(
            models.FormalHead.dataset_id == dataset.id,
            models.FormalHead.kind == "plan",
            models.FormalHead.target_key == student_id,
        )
    ).all()
    for head in heads:
        if head.node_key:
            keys.setdefault(scope_key_for(dataset.id, student_id, head.node_key), head.node_key)
    return keys


def _author_role(origin: str) -> str:
    if origin in {"teacher_direct", "manual_undo", "teacher"}:
        return "teacher"
    if origin in {"agent_revision", "agent"}:
        return "agent"
    return "system"


def list_plan_revisions(
    db: Session,
    *,
    dataset_id: str,
    student_id: str,
    role: str,
    page: int = 1,
    page_size: int = PAGE_DEFAULT,
) -> dict[str, Any]:
    """返回修订历史页；学生只看到已发布的结果版本。"""

    page, page_size = _clamp_page(page, page_size)
    dataset = _dataset(db, dataset_id)
    keys = _scope_keys(db, dataset, student_id)
    rows = (
        db.scalars(
            select(models.PlanRevision)
            .where(models.PlanRevision.scope_key.in_(tuple(keys)))
            .order_by(models.PlanRevision.created_at.desc())
        ).all()
        if keys
        else []
    )
    positions: dict[str, int] = {}
    ordered = sorted(rows, key=lambda row: (row.scope_key, row.created_at))
    for row in ordered:
        positions[row.scope_key] = positions.get(row.scope_key, 0) + 1

    summaries: list[RevisionSummary] = []
    for row in rows:
        result = db.get(models.Version, row.result_version) if row.result_version else None
        payload = dict((result.payload_json if result else None) or {})
        publication_status = payload.get("publication_status", "unpublished")
        if role == "student" and (result is None or publication_status != "published"):
            continue
        review_info = payload.get("review_info") or {}
        summaries.append(
            RevisionSummary(
                revision_id=row.revision_id,
                revision=max(1, positions.get(row.scope_key, 1)),
                plan_version=row.result_version or "",
                parent_plan_version=row.base_plan_version,
                reason=row.reason,
                created_at=row.created_at,
                review_status=review_info.get("status", "draft"),
                publication_status=publication_status,
                author_role=_author_role(row.origin),
            )
        )
    total = len(summaries)
    start = (page - 1) * page_size
    return {
        "student_id": student_id,
        "items": [item.model_dump(mode="json") for item in summaries[start : start + page_size]],
        "total": total,
        "page": page,
        "page_size": page_size,
        "limitations": [
            "修订历史只含正式修订记录；草稿与未提交方案不在此列出"
            if role == "teacher"
            else "只列出已发布到你本人学习方案的修订，不含草稿与教师内部说明",
            "修订序号按同一学生同一节点的时间顺序投影，不是业务编号",
        ],
    }


# --------------------------------------------------------------------------- #
# 已存方案理由（04 §7）
# --------------------------------------------------------------------------- #


def plan_reason_payload(
    db: Session,
    *,
    dataset_id: str,
    student_id: str,
    role: str,
    plan_version: str | None = None,
    node_id: str | None = None,
    group_id: str | None = None,
    activity_id: str | None = None,
) -> dict[str, Any]:
    """读取正式方案里已存的目标、策略与理由，不重新编造。"""

    version = get_plan_version(
        db, dataset_id=dataset_id, student_id=student_id, node_id=node_id, plan_version=plan_version
    )
    bundle = plan_bundle(version)
    groups: list[dict[str, Any]] = []
    found_activity = False
    for rec in bundle.recommendations:
        rec_group = rec.group_id or rec.recommendation_id
        matching = [a for a in rec.activities if activity_id and a.activity_id == activity_id]
        if activity_id:
            found_activity = found_activity or bool(matching)
        if group_id and rec_group != group_id:
            continue
        if activity_id and not matching:
            continue
        item: dict[str, Any] = {
            "group_id": rec_group,
            "recommendation_id": rec.recommendation_id,
            "indicator_id": rec.indicator_id,
            "goal": rec.goal,
            "reason": rec.reason,
            "student_intro": rec.student_intro,
            "adjustment_conditions": [
                condition.model_dump(mode="json") for condition in rec.adjustment_conditions
            ],
            "activities": [
                {
                    "activity_id": activity.activity_id,
                    "kind": activity.activity_kind,
                    "material_id": activity.material_id,
                    "title": activity.title,
                    "purpose": activity.purpose,
                    "minutes": activity.estimate_minutes,
                }
                for activity in (matching if activity_id else rec.activities)
            ],
        }
        if role == "teacher":
            item["finding_ids"] = list(rec.finding_ids)
            item["evidence_refs"] = [ref.model_dump(mode="json") for ref in rec.evidence_refs][:20]
        else:
            # 学生只拿与本人有关的建议句子；内部诊断编号与评分证据不下发。
            item.pop("finding_ids", None)
        groups.append(item)
    if activity_id and not found_activity:
        raise DomainError("NOT_FOUND", "该活动不在指定方案版本中")
    if group_id and not groups:
        raise DomainError("NOT_FOUND", "该任务组不在指定方案版本中")
    limitations = [
        "理由取自方案发布时保存的正式文本，未重新生成或补写",
        "方案正文不可变；若目标已调整，请以最新发布版本的 reason 为准",
    ]
    if role == "student":
        limitations.append("学生视图不含内部诊断编号、原始作答证据与教师内部说明")
    return {
        "student_id": student_id,
        "plan_version": version.version_id,
        "node_id": version.node_key,
        "review_status": (bundle.review_info.status if bundle.review_info else "draft"),
        "publication_status": bundle.publication_status,
        "groups": groups,
        "limitations": list(bundle.limitations) + limitations,
    }


# --------------------------------------------------------------------------- #
# 微课堂（04 §9.4）
# --------------------------------------------------------------------------- #


def micro_lesson_view(
    db: Session,
    *,
    dataset_id: str,
    student_id: str,
    activity_id: str,
    plan_version: str | None = None,
    node_id: str | None = None,
) -> dict[str, Any]:
    """读取已发布微课堂的安全正文；不重新生成、不计分。"""

    from study_companion.recommendation.lesson import lesson_view

    dataset = _dataset(db, dataset_id)
    version = get_plan_version(
        db, dataset_id=dataset_id, student_id=student_id, node_id=node_id, plan_version=plan_version
    )
    bundle = plan_bundle(version)
    activity = next(
        (
            activity
            for rec in bundle.recommendations
            for activity in rec.activities
            if activity.activity_id == activity_id
        ),
        None,
    )
    if activity is None:
        raise DomainError("NOT_FOUND", "该活动不在指定方案版本中")
    if activity.activity_kind != "micro_lesson":
        raise DomainError("INVALID_ARGUMENT", "该活动不是微课堂")
    content_version = activity.content_version
    if not content_version:
        raise DomainError("RESULT_NOT_READY", "该微课堂活动没有绑定内容版本")
    lesson = db.get(models.Version, content_version)
    if lesson is None or lesson.kind != "lesson" or lesson.dataset_id != dataset.id:
        raise DomainError("NOT_FOUND", "微课堂内容版本不存在")
    payload = dict(lesson.payload_json or {})
    content_id = payload.get("content_id") or lesson.target_key
    head = repo.read_head(
        db,
        repo.FormalKey(
            family_id=dataset.family_id or "",
            dataset_id=dataset.id,
            kind="lesson",
            target_key=content_id,
        ),
    )
    published = payload.get("publication_status") == "published"
    if head is not None and head.version_id == content_version:
        published = True
    if not published:
        raise DomainError("RESULT_NOT_READY", "该微课堂尚未发布，不能作为正式正文引用")

    view = lesson_view(payload)
    view["plan_version"] = version.version_id
    view["activity_id"] = activity.activity_id
    view["publication_status"] = "published"
    limitations = [
        "只读已发布版本；不重新生成正文、不重复计分",
        "微课堂正文按发布时的组件快照显示，组件内不包含答案",
    ]
    if head is None:
        limitations.append("该内容版本没有正式发布指针，按版本内 publication_status 判定为已发布")
    if payload.get("limitations"):
        limitations.extend(str(item) for item in payload["limitations"])
    return {
        "lesson": view,
        "limitations": limitations,
    }


# --------------------------------------------------------------------------- #
# 材料检索（04 search_materials / score_candidates 的只读投影）
# --------------------------------------------------------------------------- #


def _knowledge_indicators(
    *, dataset_id: str, catalog_version: str
) -> dict[str, set[str]] | None:
    """知识点到指标的正式映射；知识库不可用时返回 None，由调用方如实拒绝筛选。"""

    from study_companion.config import get_settings
    from study_companion.knowledge.store import KnowledgeStore

    try:
        kb, _manifest = KnowledgeStore(get_settings().data_root).load_version(dataset_id, catalog_version)
    except DomainError:
        return None
    return {point.kp_id: set(point.indicator_ids) for point in kb.points}


def search_formal_materials(
    db: Session,
    *,
    dataset_id: str,
    catalog_version: str,
    role: str,
    purpose: str,
    kp_ids: list[str] | None = None,
    question_type: str | None = None,
    difficulty: str | None = None,
    page: int = 1,
    page_size: int = PAGE_DEFAULT,
) -> dict[str, Any]:
    """在正式目录里按用途/知识点/题型/难度筛选材料；学生裁剪答案与解析。"""

    from study_companion.library.datasets import load_catalog

    page, page_size = _clamp_page(page, page_size)
    _dataset(db, dataset_id)
    catalog = load_catalog(db, catalog_version, dataset_id)
    limitations: list[str] = []
    wanted_indicators: set[str] | None = None
    if kp_ids:
        mapping = _knowledge_indicators(dataset_id=dataset_id, catalog_version=catalog_version)
        if mapping is None:
            raise DomainError(
                "RESULT_NOT_READY",
                "知识点映射不可用：当前目录没有正式知识库，无法按 kp_ids 筛选材料",
            )
        unknown = [kp for kp in kp_ids if kp not in mapping]
        if unknown:
            raise DomainError("NOT_FOUND", f"知识点不存在：{'、'.join(unknown)}")
        wanted_indicators = set()
        for kp in kp_ids:
            wanted_indicators |= mapping[kp]
        limitations.append("知识点筛选按正式知识库的知识点→指标映射解析，未重新判断归属")

    allowed_types = {"practice": {"question"}, "support": {"card", "textbook"}}.get(purpose)
    if allowed_types is None:
        raise DomainError("INVALID_ARGUMENT", "purpose 只能是 practice 或 support")

    matched = []
    for material in catalog.materials:
        if material.material_type not in allowed_types:
            continue
        if wanted_indicators is not None and not (wanted_indicators & set(material.indicator_ids)):
            continue
        if question_type and material.question_type != question_type:
            continue
        if difficulty:
            assessment = material.difficulty
            if assessment is None or assessment.level != difficulty:
                continue
            if assessment.status != "verified":
                limitations.append(f"材料 {material.material_id} 的难度未核验，仅按目录记录展示")
        matched.append(material)

    total = len(matched)
    start = (page - 1) * page_size
    items: list[MaterialView] = []
    teacher = role == "teacher"
    for material in matched[start : start + page_size]:
        payload: dict[str, Any] = {
            "material_id": material.material_id,
            "material_type": material.material_type,
            "catalog_version": catalog_version,
            "topic_ids": list(material.topic_ids),
            "indicator_ids": list(material.indicator_ids),
            "content": material.content,
            "options": list(material.options),
            "question_type": material.question_type,
            "source_label": material.source_label,
            "source_locator": material.source_locator.model_dump(mode="json"),
            "max_score": material.max_score,
            "difficulty_level": material.difficulty.level if material.difficulty else None,
            "difficulty_status": material.difficulty.status if material.difficulty else None,
            "display_order": material.display_order,
        }
        if teacher:
            payload.update(
                {
                    "reference_answer": material.reference_answer,
                    "reference_explanation": material.reference_explanation,
                    "rubric_points": [point.model_dump(mode="json") for point in material.rubric_points],
                    "reviewed_variant_tag": material.reviewed_variant_tag,
                }
            )
        items.append(MaterialView(**payload))

    limitations.extend(
        [
            "只列正式目录中已登记的材料；难度未核验的材料会单独标注",
            "检索结果只是候选，是否进入方案由 C 的选材与审核决定",
        ]
    )
    if not teacher:
        limitations.append("学生视图不含参考答案、题解与评分细则；未完成题不提供答案")
    projected: list[dict[str, Any]] = []
    for item in items:
        dumped = item.model_dump(mode="json")
        if not teacher:
            # 学生投影必须删字段而不是置空：null 也会暴露字段存在。
            for key in (
                "reference_answer",
                "reference_explanation",
                "rubric_points",
                "reviewed_variant_tag",
            ):
                dumped.pop(key, None)
        projected.append(dumped)
    return {
        "catalog_version": catalog_version,
        "purpose": purpose,
        "items": projected,
        "total": total,
        "page": page,
        "page_size": page_size,
        "limitations": limitations,
    }


# --------------------------------------------------------------------------- #
# 修订候选（04 §7 / Agent 协作，只生成候选）
# --------------------------------------------------------------------------- #


def _intent(value: RevisionIntent | dict[str, Any]) -> RevisionIntent:
    if isinstance(value, RevisionIntent):
        return value
    try:
        return RevisionIntent.model_validate(value)
    except Exception as exc:
        raise DomainError("INVALID_ARGUMENT", "修订意图不符合 RevisionIntent 契约") from exc


def _activities_for_intent(
    bundle: RecommendationBundle, intent: RevisionIntent
) -> tuple[RecommendationBundle, RevisionChanges, list[str]]:
    """把允许的局部调整映射成 04 的 RevisionChanges；不支持的意图直接拒绝。"""

    if not bundle.recommendations:
        raise DomainError("UNPROCESSABLE", "基础方案没有可调整的任务组")
    changes = intent.requested_changes or []
    if not changes:
        raise DomainError("INVALID_ARGUMENT", "修订意图没有任何 requested_changes")
    unsupported = sorted({str(item.get("kind")) for item in changes} - _SUPPORTED_CHANGES)
    if unsupported:
        raise DomainError(
            "TASK_NOT_AVAILABLE",
            "以下调整需要 C 重新生成方案或微课堂，当前只读候选入口不支持："
            + "、".join(unsupported),
        )

    target_group = next(
        (item.get("group_id") for item in changes if item.get("group_id")), None
    )
    base_rec = bundle.recommendations[0]
    if target_group:
        matched = next(
            (rec for rec in bundle.recommendations if (rec.group_id or rec.recommendation_id) == target_group),
            None,
        )
        if matched is None:
            raise DomainError("NOT_FOUND", f"任务组 {target_group} 不在该方案版本中")
        if matched is not base_rec:
            raise DomainError(
                "TASK_NOT_AVAILABLE",
                "候选入口当前只调整第一个任务组；其余任务组需要 C 重新生成方案",
            )
        base_rec = matched
    activities = list(base_rec.activities)
    goal = None
    student_intro = None
    summaries: list[str] = []
    for change in changes:
        kind = change.get("kind")
        if kind == "reduce_questions":
            limit = change.get("max_questions")
            if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 8:
                raise DomainError("INVALID_ARGUMENT", "max_questions 必须是 1..8 的整数")
            activities = activities[:limit]
            summaries.append(f"把任务组 {base_rec.recommendation_id} 的题量减到 {limit} 道")
        elif kind == "remove_pending_activity":
            activity_id = change.get("activity_id")
            remaining = [item for item in activities if item.activity_id != activity_id]
            if len(remaining) == len(activities):
                raise DomainError("NOT_FOUND", f"活动 {activity_id} 不在该任务组中")
            activities = remaining
            summaries.append(f"移除未开始活动 {activity_id}")
        elif kind == "reorder_pending":
            order = change.get("activity_ids") or []
            if not isinstance(order, list) or any(not isinstance(item, str) for item in order):
                raise DomainError("INVALID_ARGUMENT", "activity_ids 必须是活动编号数组")
            known = {item.activity_id: item for item in activities}
            if set(order) - set(known):
                raise DomainError("NOT_FOUND", "reorder_pending 引用了不属于该任务组的活动")
            head = [known[item] for item in order]
            activities = head + [item for item in activities if item.activity_id not in set(order)]
            summaries.append("调整未开始活动的顺序")
        elif kind == "replace_material":
            activity_id = change.get("activity_id")
            material_id = change.get("material_id")
            found = False
            replaced = []
            for item in activities:
                if item.activity_id != activity_id:
                    replaced.append(item)
                    continue
                found = True
                replaced.append(item.model_copy(update={"material_id": material_id}))
            if not found:
                raise DomainError("NOT_FOUND", f"活动 {activity_id} 不在该任务组中")
            activities = replaced
            summaries.append(f"把活动 {activity_id} 的材料换成 {material_id}")
        elif kind == "adjust_goal":
            goal = change.get("goal") or goal
            summaries.append("调整任务组目标")
        elif kind == "update_intro":
            student_intro = change.get("text") or student_intro
            summaries.append("更新学生端开场说明")

    changes_payload = RevisionChanges(
        goal=goal,
        reason=intent.reason,
        activities=activities,
        student_intro=student_intro,
    )
    return bundle, changes_payload, summaries


def prepare_revision_candidate(
    db: Session,
    *,
    dataset_id: str,
    student_id: str,
    actor: str,
    intent: RevisionIntent | dict[str, Any],
    base_plan_version: str | None = None,
    node_id: str | None = None,
) -> dict[str, Any]:
    """只生成候选：写一条 Proposal 行，不创建正式方案版本、不移动正式指针。"""

    from study_companion.recommendation.publication import preview_teacher_revision

    parsed_intent = _intent(intent)
    dataset = _dataset(db, dataset_id)
    current = get_plan_version(db, dataset_id=dataset_id, student_id=student_id, node_id=node_id)
    if base_plan_version and base_plan_version != current.version_id:
        raise DomainError(
            "DEPENDENCY_STALE",
            "基线方案版本不是当前正式版本，请重新读取后再准备候选",
        )
    node = node_id or current.node_key
    if not node:
        raise DomainError("CONTEXT_MISMATCH", "无法确定方案节点，不能准备修订候选")
    base_bundle = plan_bundle(current)
    _bundle, revision_changes, summaries = _activities_for_intent(base_bundle, parsed_intent)

    preview = preview_teacher_revision(
        db,
        dataset=dataset,
        student_id=student_id,
        node_id=node,
        base_plan_version=current.version_id,
        changes=revision_changes,
        reason=parsed_intent.reason,
        actor=actor,
    )
    proposal = db.get(models.Proposal, (preview.preview_id, 1))
    if proposal is None:
        raise DomainError("INTERNAL_ERROR", "候选未能保存")
    run_id = current.run_id or ""
    snapshot = Snapshot(
        dataset_family_id=dataset.family_id or "",
        dataset_id=dataset.id,
        run_id=run_id,
        student_id=student_id,
        node_id=node,
        catalog_version=base_bundle.catalog_version,
        knowledge_version=base_bundle.knowledge_version or "",
        analysis_version=base_bundle.analysis_version or "",
        event_cutoff=0,
        base_plan_version=current.version_id,
        config_hash="",
        rule_version=current.rule_version or "c-rules-v1",
    )
    view = ProposalView(
        proposal_id=proposal.proposal_id,
        student_id=student_id,
        base_plan_version=current.version_id,
        snapshot=snapshot,
        proposal_revision=proposal.proposal_revision,
        draft_hash=proposal.draft_hash,
        changes_summary=[{"kind": "local_adjustment", "summary": text} for text in summaries],
        candidate_options=[],
        warnings=[item.model_dump(mode="json") for item in preview.warnings],
        blocked_reasons=[],
        estimated_minutes=int((proposal.body_json or {}).get("estimated_minutes") or 0),
        expires_at=preview.expires_at,
        status="ready",
    )
    return {
        "proposal": view.model_dump(mode="json"),
        "intent_summary": {
            "goal": parsed_intent.goal,
            "reason": parsed_intent.reason,
            "requested_changes": [dict(item) for item in parsed_intent.requested_changes],
            "teacher_observations": list(parsed_intent.teacher_observations),
        },
        "limitations": [
            "这只是候选：未创建正式方案版本，也未移动正式指针；发布仍需教师确认与 04 审核",
            "候选入口当前只调整一个任务组，且只支持题量、顺序、材料、目标和开场说明的局部调整",
            "候选未下发 C 的备选材料清单，candidate_options 为空",
        ],
    }


__all__ = [
    "get_plan_version",
    "list_plan_revisions",
    "micro_lesson_view",
    "plan_bundle",
    "plan_projection",
    "plan_reason_payload",
    "prepare_revision_candidate",
    "search_formal_materials",
]
