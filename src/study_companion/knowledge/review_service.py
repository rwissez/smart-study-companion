"""A 知识审核的写入与决策（02 §9、01 §9.2、06 §4.3）。

分工与边界：

* 一个"审核对象"是一行 ``reviews``：某个候选知识版本里的一个知识点、关系或关联；
* ``approve`` 生成**新的不可变** knowledge 版本，只纳入已通过对象，随后经
  ``version_repository.publish`` 的 CAS 切换正式指针，并写 ``publications``、
  ``reviews`` 决策与 ``system_audit``；
* ``reject`` 只记录决定，候选仍留在历史，正式指针不动；
* ``edit`` 生成新的**候选**版本并重新做程序校验，不发布，等教师再次决定。

``reviews`` 物理表没有 candidate_version 列（06 §4.3），而 02 §9 要求审核记录
保存 candidate_version。这里把它和 object_id、摘要一起写在该行 ``checks`` 的
``REVIEW_CONTEXT`` 项里：既不改共享表结构，又能让同一候选中同一对象的审核对象
稳定复用。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from study_companion.api import idempotency as idempotency_api
from study_companion.contracts import DomainError
from study_companion.db import models
from study_companion.db import version_repository as repo

#: 审核对象类型 → (知识版本 payload 中的字段, 对象 ID 字段)
OBJECT_FIELDS: dict[str, tuple[str, str]] = {
    "point": ("points", "kp_id"),
    "relation": ("relations", "relation_id"),
    "annotation": ("annotations", "annotation_id"),
}

#: edit 决策的字段白名单（01 §9.2、02 §12）。
EDITABLE_FIELDS: dict[str, frozenset[str]] = {
    "point": frozenset(
        {
            "name",
            "aliases",
            "definition",
            "kind",
            "scope_role",
            "topic_ids",
            "indicator_ids",
            "source_locators",
        }
    ),
    "relation": frozenset({"type", "source_ids", "target_id", "basis", "note"}),
    "annotation": frozenset({"target_ids", "knowledge_role", "basis", "evidence"}),
}

RELATION_TYPES = frozenset(
    {"prerequisite", "has_property", "derives_from", "composed_of", "special_case"}
)

CONTEXT_CODE = "REVIEW_CONTEXT"
PENDING_STATUS = "needs_review"
APPROVED_STATUS = "human_confirmed"
REJECTED_STATUS = "rejected"

DECISIONS = frozenset({"approve", "reject", "edit"})

ENDPOINT = "knowledge.review.decide"


# --------------------------------------------------------------------------- #
# 幂等上下文（路由与服务工作在同一 (session, endpoint, key) 记账）
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class IdempotencyContext:
    session_id: str
    endpoint: str
    key: str
    digest: str


def idempotency_context(
    scope: Any, endpoint: str, key: str | None, payload: Any
) -> IdempotencyContext | None:
    token = idempotency_api.optional_key(key)
    if token is None:
        return None
    return IdempotencyContext(
        session_id=scope.session_id,
        endpoint=endpoint,
        key=token,
        digest=idempotency_api.request_digest(payload),
    )


def _replay(db: Session, context: IdempotencyContext | None) -> dict[str, Any] | None:
    if context is None:
        return None
    return idempotency_api.find_receipt(
        db,
        session_id=context.session_id,
        endpoint=context.endpoint,
        key=context.key,
        digest=context.digest,
    )


def _remember(
    db: Session,
    context: IdempotencyContext | None,
    receipt: dict[str, Any],
    *,
    result_ref: str | None = None,
) -> None:
    if context is None:
        return
    idempotency_api.store_receipt(
        db,
        session_id=context.session_id,
        endpoint=context.endpoint,
        key=context.key,
        digest=context.digest,
        receipt=receipt,
        result_ref=result_ref,
    )


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #


def _hash(value: Any) -> str:
    return repo.digest(value)


def _review_status(obj: dict[str, Any]) -> str:
    info = obj.get("review_info") or {}
    if not isinstance(info, dict):
        return PENDING_STATUS
    return str(info.get("status") or PENDING_STATUS)


def _review_method(obj: dict[str, Any]) -> str:
    info = obj.get("review_info") or {}
    if not isinstance(info, dict):
        return ""
    return str(info.get("method") or "")


def subject_key(dataset_id: str, object_id: str) -> str:
    """``reviews.subject_key`` 约定：``<dataset_id>:<object_id>``。

    既有读取路由按 ``subject_key.startswith(dataset_id)`` 过滤，因此这里必须保留
    数据集前缀；``object_id`` 原值写在 REVIEW_CONTEXT 里。
    """

    return f"{dataset_id}:{object_id}"


def _object_id_of_key(dataset_id: str, key: str) -> str:
    prefix = f"{dataset_id}:"
    return key[len(prefix) :] if key.startswith(prefix) else key


def _context(review: models.Review) -> dict[str, Any]:
    for item in review.checks_json or []:
        if isinstance(item, dict) and item.get("code") == CONTEXT_CODE:
            return dict(item)
    return {}


def _checks_with_context(
    checks: list[Any], context: dict[str, Any], *, extra: list[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    rest = [
        dict(item)
        for item in checks or []
        if not (isinstance(item, dict) and item.get("code") == CONTEXT_CODE)
    ]
    return [dict(context), *rest, *(extra or [])]


def _objects(payload: dict[str, Any], object_type: str) -> list[dict[str, Any]]:
    list_key, _ = OBJECT_FIELDS[object_type]
    return [item for item in payload.get(list_key) or [] if isinstance(item, dict)]


def find_object(
    payload: dict[str, Any], object_type: str, object_id: str
) -> dict[str, Any] | None:
    _, id_field = OBJECT_FIELDS[object_type]
    for item in _objects(payload, object_type):
        if item.get(id_field) == object_id:
            return item
    return None


def _candidate_objects(payload: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    rows: list[tuple[str, dict[str, Any]]] = []
    for object_type in OBJECT_FIELDS:
        rows.extend((object_type, item) for item in _objects(payload, object_type))
    return rows


def _invalid(field: str, reason: str) -> None:
    raise DomainError(
        "VALIDATION_ERROR", "审核请求不符合要求", [{"field": field, "reason": reason}]
    )


def _unprocessable(field: str, reason: str) -> None:
    raise DomainError(
        "UNPROCESSABLE", "候选对象未通过程序校验", [{"field": field, "reason": reason}]
    )


# --------------------------------------------------------------------------- #
# 审核对象投影
# --------------------------------------------------------------------------- #


def review_view(review: models.Review) -> dict[str, Any]:
    """01 §9.2 的 KnowledgeReviewItem 投影。"""

    context = _context(review)
    dataset_id = review.subject_key.split(":", 1)[0]
    object_id = context.get("object_id") or _object_id_of_key(dataset_id, review.subject_key)
    issues = list(review.issues_json or [])
    return {
        "review_id": review.review_id,
        "object_type": review.subject_kind,
        "object_id": object_id,
        "candidate_version": context.get("candidate_version", ""),
        "review_info": {
            "status": review.status,
            "method": review.method,
            "checks": list(review.checks_json or []),
            "issues": issues,
            "reviewed_at": context.get("reviewed_at"),
            "reviewer_session_id": review.actor_ref,
        },
        "summary": context.get("reason", ""),
        "source_locators": list(context.get("source_locators") or []),
        "payload": {},
    }


def list_pending(
    db: Session,
    dataset_id: str,
    *,
    status: str | None = PENDING_STATUS,
    object_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """列出某数据集的审核对象；默认只列仍待处理的。"""

    query = select(models.Review).where(
        models.Review.subject_key.startswith(f"{dataset_id}:")
    )
    if status:
        query = query.where(models.Review.status == status)
    if object_type:
        query = query.where(models.Review.subject_kind == object_type)
    rows = db.scalars(
        query.order_by(models.Review.created_at.desc()).offset(offset).limit(limit)
    ).all()
    return [review_view(row) for row in rows]


# --------------------------------------------------------------------------- #
# 审核对象的产生
# --------------------------------------------------------------------------- #


def ensure_review_item(
    db: Session,
    dataset_id: str,
    knowledge_version: str,
    object_type: str,
    object_id: str,
    reason: str,
) -> models.Review:
    """为候选版本中的一个对象登记待审项；相同候选/对象复用同一行。

    已决策的行（人工拒绝或通过）保持原决定，不被后续构建悄悄推翻；只有候选
    版本或对象内容变化时才会得到新的审核对象。
    """

    if object_type not in OBJECT_FIELDS:
        _invalid("object_type", f"对象类型必须是 {sorted(OBJECT_FIELDS)} 之一")
    version = db.get(models.Version, knowledge_version)
    if version is None or version.kind != "knowledge":
        raise DomainError("KB_NOT_FOUND", "知识版本不存在")
    if version.dataset_id != dataset_id:
        raise DomainError("KB_NOT_FOUND", "知识版本不属于该数据集")
    payload = dict(version.payload_json or {})
    obj = find_object(payload, object_type, object_id)
    if obj is None:
        raise DomainError("NOT_FOUND", "待审对象不在该候选版本中")

    key = subject_key(dataset_id, object_id)
    existing = db.scalar(
        select(models.Review)
        .where(
            models.Review.subject_kind == object_type,
            models.Review.subject_key == key,
        )
        .order_by(models.Review.created_at.desc())
    )
    if existing is not None and _context(existing).get("candidate_version") == knowledge_version:
        return existing

    locators = [dict(item) for item in obj.get("source_locators") or [] if isinstance(item, dict)]
    context = {
        "code": CONTEXT_CODE,
        "passed": True,
        "reason": reason or "候选对象等待教师审核",
        "candidate_version": knowledge_version,
        "object_type": object_type,
        "object_id": object_id,
        "source_locators": locators,
    }
    row = models.Review(
        review_id=repo.new_id("krev"),
        subject_kind=object_type,
        subject_key=key,
        subject_hash=_hash(obj),
        status=PENDING_STATUS,
        method="rule",
        checks_json=[context],
        issues_json=[],
        actor_ref=None,
        job_id=None,
        created_at=repo.now_iso(),
    )
    db.add(row)
    db.flush()
    return row


# --------------------------------------------------------------------------- #
# 决策编排
# --------------------------------------------------------------------------- #


def decide(
    db: Session,
    *,
    dataset: models.Dataset,
    review: models.Review,
    decision: str,
    reason: str,
    actor: str,
    changes: dict[str, Any] | None = None,
    idempotency: IdempotencyContext | None = None,
    base_candidate_version: str | None = None,
) -> dict[str, Any]:
    """对单个审核对象做 approve/reject/edit，返回接口回执。"""

    if decision not in DECISIONS:
        _invalid("decision", "决策必须是 approve/reject/edit 之一")
    if decision != "edit" and changes:
        _invalid("changes", "只有 edit 决策可以携带 changes")
    reason_text = (reason or "").strip()
    if not reason_text or len(reason_text) > 500:
        _invalid("reason", "审核理由必须是 1 到 500 个字符")
    if not actor:
        raise DomainError("FORBIDDEN", "审核必须来自已授权的教师会话")
    if review.subject_kind not in OBJECT_FIELDS:
        _unprocessable("object_type", "审核对象的类型不在白名单内")

    replay = _replay(db, idempotency)
    if replay is not None:
        return replay

    if review.status != PENDING_STATUS:
        raise DomainError("CONFLICT", "该审核对象已处理，不能重复决策")

    context = _context(review)
    context_candidate = str(context.get("candidate_version") or "")
    # 兼容直接写入的审核行：没有 REVIEW_CONTEXT 时以请求体声明的候选版本为基准。
    candidate_id = context_candidate or str(base_candidate_version or "").strip()
    if not candidate_id:
        raise DomainError("UNPROCESSABLE", "审核对象缺少候选版本信息，请重新构建后再审核")
    if base_candidate_version is not None and base_candidate_version != candidate_id:
        raise DomainError(
            "REVISION_CONFLICT",
            "候选版本已更新，请刷新审核列表后再提交",
        )
    if not dataset.family_id:
        raise DomainError("UNPROCESSABLE", "数据集缺少系列身份，无法发布知识版本")

    candidate = db.get(models.Version, candidate_id)
    if candidate is None or candidate.kind != "knowledge":
        raise DomainError("KB_NOT_FOUND", "候选知识版本不存在")
    if candidate.dataset_id != dataset.id:
        raise DomainError("KB_NOT_FOUND", "候选知识版本不属于该数据集")
    payload = dict(candidate.payload_json or {})
    object_type = review.subject_kind
    object_id = str(
        context.get("object_id") or _object_id_of_key(dataset.id, review.subject_key)
    )
    obj = find_object(payload, object_type, object_id)
    if obj is None:
        raise DomainError("NOT_FOUND", "待审对象不在候选版本中")
    # 只有在审核行自带候选上下文时才做内容 hash 复核；直接写入的历史行没有可信摘要。
    if context_candidate and review.subject_hash and _hash(obj) != review.subject_hash:
        raise DomainError(
            "REVISION_CONFLICT", "候选对象内容已变化，请重新读取后再审核"
        )

    if decision == "approve":
        receipt = _approve(
            db,
            dataset=dataset,
            review=review,
            candidate=candidate,
            payload=payload,
            obj=obj,
            object_type=object_type,
            object_id=object_id,
            context=context,
            reason=reason_text,
            actor=actor,
        )
    elif decision == "reject":
        receipt = _reject(
            db,
            dataset=dataset,
            review=review,
            candidate=candidate,
            context=context,
            object_type=object_type,
            object_id=object_id,
            reason=reason_text,
            actor=actor,
        )
    else:
        receipt = _edit(
            db,
            dataset=dataset,
            review=review,
            candidate=candidate,
            payload=payload,
            obj=obj,
            object_type=object_type,
            object_id=object_id,
            context=context,
            changes=changes,
            reason=reason_text,
            actor=actor,
        )

    db.flush()
    _remember(
        db,
        idempotency,
        receipt,
        result_ref=str(receipt.get("knowledge_version") or receipt.get("candidate_version") or ""),
    )
    return receipt


# --------------------------------------------------------------------------- #
# approve
# --------------------------------------------------------------------------- #


def _merge_objects(
    *,
    candidate_list: list[dict[str, Any]],
    formal_list: list[dict[str, Any]],
    id_field: str,
    target_id: str | None,
    target_object: dict[str, Any] | None,
    include_validated: bool,
) -> list[dict[str, Any]]:
    """正式对象优先保留；再并入候选里已通过程序校验的新对象；最后放本次通过的对象。"""

    by_id: dict[str, dict[str, Any]] = {}
    ordered: list[str] = []

    def put(object_id: str, item: dict[str, Any]) -> None:
        if object_id not in by_id:
            ordered.append(object_id)
        by_id[object_id] = item

    for item in formal_list:
        value = item.get(id_field)
        if value:
            put(str(value), item)
    for item in candidate_list:
        value = item.get(id_field)
        if not value:
            continue
        key = str(value)
        if target_id is not None and key == target_id:
            continue
        if include_validated and key not in by_id and _review_status(item) == "validated":
            put(key, item)
    if target_id is not None and target_object is not None:
        put(target_id, target_object)
    return [by_id[item] for item in ordered]


def _relation_missing(obj: dict[str, Any], point_ids: set[str]) -> list[str]:
    missing = [
        str(source)
        for source in obj.get("source_ids") or []
        if str(source) not in point_ids
    ]
    target = obj.get("target_id")
    if target is not None and str(target) not in point_ids:
        missing.append(str(target))
    return missing


def _annotation_missing(obj: dict[str, Any], point_ids: set[str]) -> list[str]:
    return [
        str(target)
        for target in obj.get("target_ids") or []
        if str(target) not in point_ids
    ]


def _human_object(obj: dict[str, Any], reason: str, actor: str, now: str) -> dict[str, Any]:
    updated = copy.deepcopy(obj)
    info = dict(updated.get("review_info") or {})
    checks = [dict(item) for item in info.get("checks") or [] if isinstance(item, dict)]
    info.update(
        {
            "status": APPROVED_STATUS,
            "method": "human",
            "checks": [
                *checks,
                {
                    "code": "HUMAN_APPROVED",
                    "passed": True,
                    "reason": reason,
                    "evidence_refs": [],
                },
            ],
            "reviewed_at": now,
            "reviewer_session_id": actor,
            "rule_version": info.get("rule_version") or "a-human-review",
        }
    )
    info.pop("issues", None)
    updated["review_info"] = info
    return updated


def _approve(
    db: Session,
    *,
    dataset: models.Dataset,
    review: models.Review,
    candidate: models.Version,
    payload: dict[str, Any],
    obj: dict[str, Any],
    object_type: str,
    object_id: str,
    context: dict[str, Any],
    reason: str,
    actor: str,
) -> dict[str, Any]:
    now = repo.now_iso()
    key = repo.FormalKey(
        family_id=dataset.family_id or "", dataset_id=dataset.id, kind="knowledge"
    )
    formal = repo.read_formal(db, key)
    formal_payload = dict(formal.payload_json or {}) if formal is not None else {}
    if find_object(formal_payload, object_type, object_id) is not None:
        raise DomainError("CONFLICT", "该对象已在正式知识版本中")

    approved = _human_object(obj, reason, actor, now)
    points = _merge_objects(
        candidate_list=_objects(payload, "point"),
        formal_list=[
            item for item in formal_payload.get("points") or [] if isinstance(item, dict)
        ],
        id_field="kp_id",
        target_id=object_id if object_type == "point" else None,
        target_object=approved if object_type == "point" else None,
        include_validated=True,
    )
    point_ids = {str(item.get("kp_id")) for item in points if item.get("kp_id")}

    if object_type == "relation":
        missing = _relation_missing(approved, point_ids)
        if missing:
            _unprocessable(
                "source_ids/target_id",
                "关系端点尚未进入正式知识点集合：" + "、".join(missing),
            )
    if object_type == "annotation":
        missing = _annotation_missing(approved, point_ids)
        if missing:
            _unprocessable(
                "target_ids",
                "关联目标尚未进入正式知识点集合：" + "、".join(missing),
            )

    relations = _merge_objects(
        candidate_list=_objects(payload, "relation"),
        formal_list=[
            item for item in formal_payload.get("relations") or [] if isinstance(item, dict)
        ],
        id_field="relation_id",
        target_id=object_id if object_type == "relation" else None,
        target_object=approved if object_type == "relation" else None,
        include_validated=True,
    )
    relations = [
        item
        for item in relations
        if not _relation_missing(item, point_ids)
        or (
            object_type == "relation"
            and item.get("relation_id") == object_id
        )
    ]

    annotations = _merge_objects(
        candidate_list=_objects(payload, "annotation"),
        formal_list=[
            item for item in formal_payload.get("annotations") or [] if isinstance(item, dict)
        ],
        id_field="annotation_id",
        target_id=object_id if object_type == "annotation" else None,
        target_object=approved if object_type == "annotation" else None,
        include_validated=True,
    )
    annotations = [
        item
        for item in annotations
        if not _annotation_missing(item, point_ids)
        or (
            object_type == "annotation"
            and item.get("annotation_id") == object_id
        )
    ]

    new_version_id = repo.new_id("kn")
    decision_record = {
        "review_id": review.review_id,
        "decision": "approve",
        "object_type": object_type,
        "object_id": object_id,
        "actor_ref": actor,
        "reason": reason,
        "decided_at": now,
    }
    summary = _formal_summary(
        candidate_payload=payload,
        points=points,
        relations=relations,
        annotations=annotations,
        candidate_version=candidate.version_id,
        new_version=new_version_id,
        now=now,
        decision=decision_record,
    )
    limitations = list(payload.get("limitations") or [])
    if summary["counts"]["unresolved_items"]:
        limitations.append("部分发布：仅纳入已通过审核的对象，其余候选仍待教师审核")
    new_payload = {
        "dataset_id": dataset.id,
        "catalog_version": payload.get("catalog_version", ""),
        "knowledge_version": new_version_id,
        "parent_version": candidate.version_id,
        "points": points,
        "relations": relations,
        "annotations": annotations,
        "review_summary": summary,
        "limitations": limitations,
        "review_info": {
            "status": "human_confirmed",
            "method": "human",
            "checks": [
                {
                    "code": "DEPENDENCY_CLOSURE",
                    "passed": True,
                    "reason": "纳入对象的关系端点与关联目标均在正式知识点集合内",
                },
                {
                    "code": "HUMAN_APPROVED",
                    "passed": True,
                    "reason": reason,
                },
            ],
            "reviewed_at": now,
            "reviewer_session_id": actor,
            "rule_version": candidate.rule_version or "a-human-review",
        },
    }

    version = repo.create_version(
        db,
        kind="knowledge",
        key=key,
        payload=new_payload,
        rule_version=candidate.rule_version or "a-human-review",
        input_digest=repo.digest(new_payload),
        version_id=new_version_id,
        origin_kind="human",
    )
    repo.register_edge(db, version, candidate, role="parent_knowledge")
    if formal is not None and formal.version_id != candidate.version_id:
        repo.register_edge(db, version, formal, role="previous_formal")

    head = repo.read_head(db, key)
    publication = repo.publish(
        db,
        key=key,
        version=version,
        path="teacher_direct",
        basis="teacher_confirmation",
        review_id=review.review_id,
        actor_ref=actor,
        expected_version_id=head.version_id if head is not None else None,
        expected_generation=head.generation if head is not None else 0,
    )

    review.status = APPROVED_STATUS
    review.method = "human"
    review.actor_ref = actor
    review.checks_json = _checks_with_context(
        review.checks_json,
        {**context, "reviewed_at": now},
        extra=[
            {
                "code": "HUMAN_APPROVE",
                "passed": True,
                "reason": reason,
                "evidence_refs": [],
            }
        ],
    )
    _audit(
        db,
        dataset=dataset,
        action="knowledge_review_approve",
        actor=actor,
        subject_ref=review.review_id,
        before_ref=candidate.version_id,
        after_ref=new_version_id,
        details={
            "decision": "approve",
            "object_type": object_type,
            "object_id": object_id,
            "reason": reason,
            "base_candidate_version": candidate.version_id,
            "publication_id": publication.publication_id,
            "review_summary": {
                "counts": summary["counts"],
                "audit": summary["audit"],
            },
        },
    )
    return {
        "review_id": review.review_id,
        "review_status": APPROVED_STATUS,
        "decision": "approve",
        "candidate_version": candidate.version_id,
        "knowledge_version": new_version_id,
        "publication_id": publication.publication_id,
        "job_id": None,
        "published": True,
    }


# --------------------------------------------------------------------------- #
# reject
# --------------------------------------------------------------------------- #


def _reject(
    db: Session,
    *,
    dataset: models.Dataset,
    review: models.Review,
    candidate: models.Version,
    context: dict[str, Any],
    object_type: str,
    object_id: str,
    reason: str,
    actor: str,
) -> dict[str, Any]:
    now = repo.now_iso()
    key = repo.FormalKey(
        family_id=dataset.family_id or "", dataset_id=dataset.id, kind="knowledge"
    )
    formal = repo.read_formal(db, key)

    review.status = REJECTED_STATUS
    review.method = "human"
    review.actor_ref = actor
    review.checks_json = _checks_with_context(
        review.checks_json,
        {**context, "reviewed_at": now},
        extra=[
            {
                "code": "HUMAN_REJECT",
                "passed": False,
                "reason": reason,
                "evidence_refs": [],
            }
        ],
    )
    review.issues_json = [
        *list(review.issues_json or []),
        {
            "code": "HUMAN_REJECTED",
            "message": reason,
            "severity": "warning",
        },
    ]
    _audit(
        db,
        dataset=dataset,
        action="knowledge_review_reject",
        actor=actor,
        subject_ref=review.review_id,
        before_ref=candidate.version_id,
        after_ref=formal.version_id if formal is not None else None,
        details={
            "decision": "reject",
            "object_type": object_type,
            "object_id": object_id,
            "reason": reason,
            "base_candidate_version": candidate.version_id,
            "formal_unchanged": True,
        },
    )
    return {
        "review_id": review.review_id,
        "review_status": REJECTED_STATUS,
        "decision": "reject",
        "candidate_version": candidate.version_id,
        "knowledge_version": formal.version_id if formal is not None else None,
        "publication_id": None,
        "job_id": None,
        "published": False,
    }


# --------------------------------------------------------------------------- #
# edit
# --------------------------------------------------------------------------- #


def _text(value: Any, field: str, *, max_length: int = 2000, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        _invalid(field, "必须是字符串")
    if not allow_empty and not value.strip():
        _invalid(field, "不能为空")
    if len(value) > max_length:
        _invalid(field, f"最多 {max_length} 个字符")
    return value


def _text_list(value: Any, field: str, *, min_items: int = 0) -> list[str]:
    if not isinstance(value, list):
        _invalid(field, "必须是字符串数组")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            _invalid(field, "数组元素必须是非空字符串")
        result.append(item)
    if len(result) < min_items:
        _invalid(field, f"至少需要 {min_items} 项")
    return result


def _locators(value: Any, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        _invalid(field, "必须是对象数组")
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            _invalid(field, "每个来源必须是对象")
        source_file = item.get("source_file")
        if not isinstance(source_file, str) or not source_file.strip():
            _invalid(field, "来源必须写明 source_file")
        page = item.get("pdf_page")
        if page is not None and (not isinstance(page, int) or isinstance(page, bool) or page < 1):
            _invalid(field, "pdf_page 必须是正整数")
        state = item.get("verification_status", "unverified")
        if state not in {"verified", "unverified"}:
            _invalid(field, "verification_status 只能是 verified/unverified")
        result.append(dict(item))
    return result


def _apply_changes(
    *,
    object_type: str,
    obj: dict[str, Any],
    changes: dict[str, Any],
    payload: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """按白名单应用编辑并重新做结构校验，返回（新对象，程序检查项）。"""

    if not isinstance(changes, dict) or not changes:
        _invalid("changes", "edit 决策必须提供至少一个可编辑字段")
    allowed = EDITABLE_FIELDS[object_type]
    unknown = sorted(set(changes) - allowed)
    if unknown:
        _invalid("changes", f"{object_type} 不允许编辑字段：{'、'.join(unknown)}")

    updated = copy.deepcopy(obj)
    checks: list[dict[str, Any]] = [
        {
            "code": "FIELD_ALLOWED",
            "passed": True,
            "reason": f"只修改了 {object_type} 白名单字段",
        }
    ]
    point_ids = {
        str(item.get("kp_id"))
        for item in _objects(payload, "point")
        if item.get("kp_id")
    }

    if object_type == "point":
        if "name" in changes:
            updated["name"] = _text(changes["name"], "name", max_length=500)
        if "definition" in changes:
            updated["definition"] = _text(changes["definition"], "definition")
        if "aliases" in changes:
            updated["aliases"] = _text_list(changes["aliases"], "aliases")
        if "kind" in changes:
            updated["kind"] = _text(changes["kind"], "kind", max_length=64)
        if "scope_role" in changes:
            updated["scope_role"] = _text(changes["scope_role"], "scope_role", max_length=32)
        if "topic_ids" in changes:
            updated["topic_ids"] = _text_list(changes["topic_ids"], "topic_ids")
        if "indicator_ids" in changes:
            updated["indicator_ids"] = _text_list(changes["indicator_ids"], "indicator_ids")
        if "source_locators" in changes:
            updated["source_locators"] = _locators(changes["source_locators"], "source_locators")
        if not str(updated.get("name") or "").strip():
            _invalid("name", "知识点名称不能为空")
        if not str(updated.get("definition") or "").strip():
            _invalid("definition", "知识点定义不能为空")
        checks.append(
            {
                "code": "POINT_SCOPE",
                "passed": True,
                "reason": "名称、定义、范围与来源字段均为非空合法值",
            }
        )

    elif object_type == "relation":
        if "type" in changes:
            relation_type = changes["type"]
            if not isinstance(relation_type, str) or relation_type not in RELATION_TYPES:
                _invalid("type", f"关系类型必须是 {sorted(RELATION_TYPES)} 之一")
            updated["type"] = relation_type
        if "source_ids" in changes:
            updated["source_ids"] = _text_list(changes["source_ids"], "source_ids", min_items=1)
        if "target_id" in changes:
            updated["target_id"] = _text(changes["target_id"], "target_id", max_length=128)
        if "basis" in changes:
            updated["basis"] = _text(changes["basis"], "basis", max_length=64)
        if "note" in changes:
            updated["note"] = _text(changes["note"], "note", allow_empty=True)
        source_ids = [str(item) for item in updated.get("source_ids") or []]
        target_id = str(updated.get("target_id") or "")
        if not source_ids:
            _invalid("source_ids", "关系至少需要一个来源")
        if not target_id:
            _invalid("target_id", "关系必须写明目标")
        if target_id in source_ids:
            _unprocessable("target_id", "关系不能以自身为端点")
        if updated.get("type") == "composed_of" and len(set(source_ids)) < 2:
            _unprocessable("source_ids", "合并创造关系至少需要两个不同来源")
        missing = [item for item in [*source_ids, target_id] if item not in point_ids]
        if missing:
            _unprocessable(
                "source_ids/target_id",
                "关系端点不在候选知识点集合中：" + "、".join(dict.fromkeys(missing)),
            )
        checks.append(
            {
                "code": "RELATION_VALID",
                "passed": True,
                "reason": "关系类型、方向、来源数与端点存在性均通过程序校验",
            }
        )

    else:  # annotation
        if "target_ids" in changes:
            updated["target_ids"] = _text_list(changes["target_ids"], "target_ids", min_items=1)
        if "knowledge_role" in changes:
            role = changes["knowledge_role"]
            if role is not None:
                role = _text(role, "knowledge_role", max_length=32)
            updated["knowledge_role"] = role
        if "basis" in changes:
            updated["basis"] = _text(changes["basis"], "basis", max_length=64)
        if "evidence" in changes:
            updated["evidence"] = _text(changes["evidence"], "evidence", allow_empty=True)
        targets = [str(item) for item in updated.get("target_ids") or []]
        if not targets:
            _invalid("target_ids", "关联至少需要一个目标")
        missing = [item for item in targets if item not in point_ids]
        if missing:
            _unprocessable(
                "target_ids",
                "关联目标不在候选知识点集合中：" + "、".join(dict.fromkeys(missing)),
            )
        updated["target_ids"] = sorted(set(targets))
        checks.append(
            {
                "code": "MAPPING_VALID",
                "passed": True,
                "reason": "关联目标均存在于候选知识点集合",
            }
        )

    info = dict(updated.get("review_info") or {})
    info["status"] = PENDING_STATUS
    info.pop("reviewed_at", None)
    info.pop("reviewer_session_id", None)
    updated["review_info"] = info
    return updated, checks


def _edit(
    db: Session,
    *,
    dataset: models.Dataset,
    review: models.Review,
    candidate: models.Version,
    payload: dict[str, Any],
    obj: dict[str, Any],
    object_type: str,
    object_id: str,
    context: dict[str, Any],
    changes: dict[str, Any] | None,
    reason: str,
    actor: str,
) -> dict[str, Any]:
    if changes is None:
        _invalid("changes", "edit 决策必须提供 changes")
    now = repo.now_iso()
    updated, checks = _apply_changes(
        object_type=object_type, obj=obj, changes=changes, payload=payload
    )

    key = repo.FormalKey(
        family_id=dataset.family_id or "", dataset_id=dataset.id, kind="knowledge"
    )
    list_key, id_field = OBJECT_FIELDS[object_type]
    new_version_id = repo.new_id("kn")
    new_payload = dict(payload)
    new_payload[list_key] = [
        updated if item.get(id_field) == object_id else item
        for item in _objects(payload, object_type)
    ]
    new_payload["knowledge_version"] = new_version_id
    new_payload["parent_version"] = candidate.version_id
    new_payload["review_summary"] = _candidate_summary(
        payload=payload,
        new_version=new_version_id,
        now=now,
        decision={
            "review_id": review.review_id,
            "decision": "edit",
            "object_type": object_type,
            "object_id": object_id,
            "actor_ref": actor,
            "reason": reason,
            "decided_at": now,
        },
    )

    version = repo.create_version(
        db,
        kind="knowledge",
        key=key,
        payload=new_payload,
        rule_version=candidate.rule_version or "a-human-review",
        input_digest=repo.digest(new_payload),
        version_id=new_version_id,
        origin_kind="human",
    )
    repo.register_edge(db, version, candidate, role="parent_knowledge")

    review.method = "human"
    review.actor_ref = actor
    review.subject_hash = _hash(updated)
    review.checks_json = _checks_with_context(
        review.checks_json,
        {
            **context,
            "candidate_version": new_version_id,
            "object_id": object_id,
            "reason": context.get("reason") or reason,
            "reviewed_at": now,
        },
        extra=[
            {
                "code": "EDIT_APPLIED",
                "passed": True,
                "reason": reason,
                "evidence_refs": [],
            },
            *checks,
        ],
    )
    review.issues_json = []
    _audit(
        db,
        dataset=dataset,
        action="knowledge_review_edit",
        actor=actor,
        subject_ref=review.review_id,
        before_ref=candidate.version_id,
        after_ref=new_version_id,
        details={
            "decision": "edit",
            "object_type": object_type,
            "object_id": object_id,
            "reason": reason,
            "changed_fields": sorted(changes),
            "base_candidate_version": candidate.version_id,
            "published": False,
        },
    )
    return {
        "review_id": review.review_id,
        "review_status": PENDING_STATUS,
        "decision": "edit",
        "candidate_version": new_version_id,
        "previous_candidate_version": candidate.version_id,
        "knowledge_version": None,
        "publication_id": None,
        "job_id": None,
        "published": False,
        "checks": checks,
    }


# --------------------------------------------------------------------------- #
# 质量摘要与审计
# --------------------------------------------------------------------------- #


def _formal_summary(
    *,
    candidate_payload: dict[str, Any],
    points: list[dict[str, Any]],
    relations: list[dict[str, Any]],
    annotations: list[dict[str, Any]],
    candidate_version: str,
    new_version: str,
    now: str,
    decision: dict[str, Any],
) -> dict[str, Any]:
    base = dict(candidate_payload.get("review_summary") or {})
    candidate_objects = _candidate_objects(candidate_payload)
    official = {
        ("point", str(item.get("kp_id")))
        for item in points
        if item.get("kp_id")
    } | {
        ("relation", str(item.get("relation_id")))
        for item in relations
        if item.get("relation_id")
    } | {
        ("annotation", str(item.get("annotation_id")))
        for item in annotations
        if item.get("annotation_id")
    }
    unresolved = 0
    for object_type, item in candidate_objects:
        _, id_field = OBJECT_FIELDS[object_type]
        identity = str(item.get(id_field) or "")
        if (object_type, identity) in official:
            continue
        if _review_status(item) == REJECTED_STATUS:
            continue
        unresolved += 1

    final_objects = [*points, *relations, *annotations]
    first_pass = sum(
        1
        for item in final_objects
        if _review_status(item) == "validated" and _review_method(item) != "human"
    )
    repaired = sum(1 for item in final_objects if _review_status(item) == "repaired")
    human_pass = sum(1 for item in final_objects if _review_method(item) == "human")
    rejected = sum(
        1 for _, item in candidate_objects if _review_status(item) == REJECTED_STATUS
    )

    counts = dict(base.get("counts") or {})
    counts["formal_points"] = len(points)
    counts["formal_relations"] = len(relations)
    counts["formal_annotations"] = len(annotations)
    counts["unresolved_items"] = unresolved

    audit = dict(base.get("audit") or {})
    audit["reviewed_objects"] = len(candidate_objects)
    audit["first_pass"] = first_pass
    audit["auto_repaired_pass"] = repaired
    audit["human_pass"] = human_pass
    audit["rejected"] = rejected
    audit["needs_review"] = unresolved
    audit["not_processed"] = max(
        len(candidate_objects) - (first_pass + repaired + human_pass + rejected + unresolved),
        0,
    )

    base["knowledge_version"] = new_version
    base["candidate_version"] = candidate_version
    base["generated_at"] = now
    base["counts"] = counts
    base["audit"] = audit
    base["last_review"] = decision
    return base


def _candidate_summary(
    *,
    payload: dict[str, Any],
    new_version: str,
    now: str,
    decision: dict[str, Any],
) -> dict[str, Any]:
    base = dict(payload.get("review_summary") or {})
    objects = _candidate_objects(payload)
    statuses = [_review_status(item) for _, item in objects]
    unresolved = sum(
        1
        for status in statuses
        if status not in {"validated", APPROVED_STATUS, REJECTED_STATUS, "repaired"}
    )

    counts = dict(base.get("counts") or {})
    counts["formal_points"] = len(_objects(payload, "point"))
    counts["formal_relations"] = len(_objects(payload, "relation"))
    counts["formal_annotations"] = len(_objects(payload, "annotation"))
    counts["unresolved_items"] = unresolved

    audit = dict(base.get("audit") or {})
    audit["reviewed_objects"] = len(statuses)
    audit["first_pass"] = statuses.count("validated")
    audit["auto_repaired_pass"] = statuses.count("repaired")
    audit["human_pass"] = statuses.count(APPROVED_STATUS)
    audit["rejected"] = statuses.count(REJECTED_STATUS)
    audit["needs_review"] = unresolved
    audit["not_processed"] = max(
        len(statuses)
        - sum(
            (
                audit["first_pass"],
                audit["auto_repaired_pass"],
                audit["human_pass"],
                audit["rejected"],
                unresolved,
            )
        ),
        0,
    )

    base["knowledge_version"] = new_version
    base["candidate_version"] = new_version
    base["generated_at"] = now
    base["counts"] = counts
    base["audit"] = audit
    base["last_review"] = decision
    return base


def _audit(
    db: Session,
    *,
    dataset: models.Dataset,
    action: str,
    actor: str,
    subject_ref: str,
    before_ref: str | None,
    after_ref: str | None,
    details: dict[str, Any],
) -> None:
    db.add(
        models.SystemAudit(
            audit_id=repo.new_id("audit"),
            scope_key=f"knowledge:{dataset.id}",
            actor_ref=actor,
            action=action,
            subject_ref=subject_ref,
            before_ref=before_ref,
            after_ref=after_ref,
            details_json=details,
            created_at=repo.now_iso(),
        )
    )
