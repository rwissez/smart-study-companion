"""05 §6 里"只读但当前缺失"的 Agent 工具（schema 闭合、角色/任务受限、最小投影）。

本模块补齐 10 个工具：

* ``get_student_progress`` —— 基于 B 正式分析版本比较（03 §10），无基线时如实说不可比
* ``compare_classes`` —— 仅教师，复用 B 的班级比较键对齐规则（03 §12.4），禁止自算总分差
* ``resolve_evidence`` —— 按 EvidenceRef 解析安全投影，复用统一证据仓储（01 §6）
* ``get_quality_report`` —— A/B/C 质量报告只读投影，仅教师
* ``get_plan_history`` / ``get_plan_reason`` —— 从正式方案版本读历史与已存理由
* ``search_learning_materials`` —— 正式目录内按知识点/用途筛选，学生裁剪答案
* ``get_lesson_view`` —— 读已发布微课堂安全正文，不重新生成
* ``prepare_plan_revision`` —— 只生成候选（无正式副作用），需校验学生/基线/意图
* ``submit_plan_revision`` —— 把已有候选交给教师确认，不发布、不可学生调用、重复提交幂等
* ``get_revision_status`` —— 按候选/修订/确认查 draft/pending/confirmed/rejected，只读

共同约束：handler 重新鉴权、最小投影、分页默认 20 最大 50；结果附
``evidence_refs`` 与 ``limitations``；失败抛 ``DomainError``，不返回未授权数据。

数据来源说明（已知限制）：``ServerContext`` 目前只携带服务端预取的只读快照，
没有注入数据库会话。本模块在工具被调用时才开一个短生命周期的会话
（``extras["db_session"]`` 可注入，测试与将来的路由装配都走这条通道）。
查询用完即关、从不提交；提交只写候选的审核记录与待确认记录，正式发布仍由
教师确认后走 04 的 ``agent_revision`` 路径。等路由把会话显式注入 ``extras``
后应删掉这里的兜底。
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable, Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session

from study_companion.agent.loop import RegisteredTool, ToolRegistry
from study_companion.agent.tasks import TaskSpec
from study_companion.analysis import queries as analysis_queries
from study_companion.contracts import (
    DomainError,
    EvidenceRef,
    RecommendationBundle,
    ReviewInfo,
)
from study_companion.recommendation import quality as c_quality
from study_companion.recommendation import query as c_query
from study_companion.tools.b_analysis import ServerContext

#: extras 里可选注入的只读会话；测试与将来的路由装配使用同一个键。
DB_SESSION_KEY = "db_session"

PAGE_DEFAULT = 20
PAGE_MAX = 50

#: task_key -> 该任务允许的扩展工具。只有出现在这里且角色满足的工具才会注册。
TASK_TOOLS: dict[str, tuple[str, ...]] = {
    "student_progress": ("get_student_progress",),
    "evidence_lookup": ("resolve_evidence",),
    "student_loss_explanation": ("resolve_evidence",),
    "class_analysis": ("compare_classes", "resolve_evidence"),
    "data_quality": ("get_quality_report",),
    "class_data_quality": ("get_quality_report",),
    "study_plan": (
        "get_plan_history",
        "get_plan_reason",
        "search_learning_materials",
        "prepare_plan_revision",
        "submit_plan_revision",
        "get_revision_status",
    ),
    "student_learning_advice": (
        "get_plan_history",
        "get_plan_reason",
        "search_learning_materials",
    ),
    "observation_review": ("get_lesson_view",),
    "knowledge_lookup": ("search_learning_materials",),
}

#: 仅教师可用的扩展工具。修订候选的提交与状态查询都属于 plan_modify（05 §4），
#: 学生端保持只读，不注册、也不放行这两个工具。
TEACHER_ONLY = frozenset(
    {
        "compare_classes",
        "get_quality_report",
        "prepare_plan_revision",
        "submit_plan_revision",
        "get_revision_status",
    }
)


def _schema(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: list[str],
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


@dataclass(frozen=True)
class _ReadScope:
    """给统一证据仓储用的最小作用域对象（只读）。"""

    role: str
    dataset_id: str | None
    student_id: str | None


@contextmanager
def _read_session(ctx: ServerContext) -> Iterator[Session]:
    db = ctx.extras.get(DB_SESSION_KEY)
    if db is not None:
        yield db
        return
    from study_companion.db.session import get_sessionmaker

    session = get_sessionmaker()()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def _write_session(ctx: ServerContext) -> Iterator[Session]:
    """候选提交用的事务边界：注入会话不自行提交，自开会话才提交。

    路由把请求事务注入 ``extras`` 时由路由统一提交；测试同样复用同一会话。
    只有工具在无路由环境自行开会话时才提交，避免把读写混在一起。
    """

    db = ctx.extras.get(DB_SESSION_KEY)
    if db is not None:
        yield db
        return
    from study_companion.db.session import get_sessionmaker

    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _ok(
    data: Any,
    *,
    evidence_refs: list[dict[str, Any]] | None = None,
    limitations: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "status": "succeeded",
        "data": data,
        "error": None,
        "evidence_refs": evidence_refs or [],
        "limitations": limitations or [],
    }


def _page(args: dict[str, Any]) -> tuple[int, int]:
    page = args.get("page", 1)
    page_size = args.get("page_size", PAGE_DEFAULT)
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise DomainError("INVALID_ARGUMENT", "page 必须是 >=1 的整数")
    if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= PAGE_MAX:
        raise DomainError("INVALID_ARGUMENT", f"page_size 必须在 1..{PAGE_MAX} 之间")
    return page, page_size


def _dataset(ctx: ServerContext) -> str:
    if not ctx.dataset_id:
        raise DomainError("CONTEXT_MISMATCH", "工具必须绑定数据集")
    return ctx.dataset_id


def _student(ctx: ServerContext, role: str, requested: str | None) -> str:
    """重新鉴权学生目标：学生只能是自己，教师必须在本轮授权范围内。"""

    if role == "student":
        if requested and requested != ctx.bound_student_id:
            raise DomainError("FORBIDDEN", "学生会话只能访问本人数据")
        student_id = ctx.bound_student_id
        if not student_id and len(ctx.allowed_student_ids) == 1:
            student_id = next(iter(ctx.allowed_student_ids))
        if not student_id:
            raise DomainError("CONTEXT_MISMATCH", "学生会话缺少绑定学生")
        if student_id not in ctx.allowed_student_ids:
            raise DomainError("SCOPE_DENIED", "学生不在当前会话范围内")
        return student_id
    student_id = requested or ctx.bound_student_id
    if not student_id:
        raise DomainError("INVALID_ARGUMENT", "教师查询必须指定学生")
    if student_id not in ctx.allowed_student_ids:
        raise DomainError("SCOPE_DENIED", f"学生 {student_id} 不在当前会话允许范围内")
    return student_id


def _classes(ctx: ServerContext, class_ids: list[str]) -> list[str]:
    if len(class_ids) != 2 or len({*class_ids}) != 2:
        raise DomainError("INVALID_ARGUMENT", "compare_classes 需要两个不同的班级")
    allowed = ctx.allowed_class_ids
    if allowed is None:
        raise DomainError("SCOPE_DENIED", "当前会话没有班级读取范围")
    for class_id in class_ids:
        if class_id not in allowed:
            raise DomainError("SCOPE_DENIED", f"班级 {class_id} 不在当前会话允许范围内")
    return class_ids


def _node(ctx: ServerContext, requested: str | None) -> str:
    node_id = requested or ctx.bound_node_id
    if not node_id:
        raise DomainError("INVALID_ARGUMENT", "该工具需要 node_id")
    return node_id


# --------------------------------------------------------------------------- #
# get_student_progress
# --------------------------------------------------------------------------- #


def _progress(ctx: ServerContext, role: str, args: dict[str, Any]) -> dict[str, Any]:
    dataset_id = _dataset(ctx)
    student_id = _student(ctx, role, args.get("student_id"))
    node_id = _node(ctx, args.get("node_id"))
    with _read_session(ctx) as db:
        payload = analysis_queries.compare_student_progress(
            db,
            dataset_id=dataset_id,
            student_id=student_id,
            node_id=node_id,
            baseline_analysis_version=args.get("baseline_analysis_version"),
            current_analysis_version=args.get("current_analysis_version"),
            scope=args.get("scope"),
        )
        comparisons = payload.get("comparisons") or []
        refs = analysis_queries.comparison_evidence_refs(comparisons, dataset_id=dataset_id)
    data = {
        "student_id": payload["student_id"],
        "node_id": payload["node_id"],
        "comparable": payload["comparable"],
        "source": payload["source"],
        "current_analysis_version": payload["current_analysis_version"],
        "baseline_analysis_version": payload["baseline_analysis_version"],
        "comparisons": [
            {
                "comparison_id": item.comparison_id,
                "classification": item.classification,
                "comparable": item.comparable,
                "metric_name": item.metric_name,
                "indicator_id": item.scope.indicator_id,
                "kp_id": item.scope.kp_id,
                "baseline_value": item.baseline_value,
                "current_value": item.current_value,
                "description": item.description,
                "limitations": list(item.limitations),
            }
            for item in comparisons
        ],
        "limitations": list(payload.get("limitations") or []),
    }
    if not payload["comparable"]:
        data["limitations"].append("两次结果不可比或缺少基线：不宣称进步，也不得把数值差解释为改善")
    return _ok(data, evidence_refs=refs, limitations=data["limitations"])


# --------------------------------------------------------------------------- #
# compare_classes
# --------------------------------------------------------------------------- #


def _compare_classes(ctx: ServerContext, role: str, args: dict[str, Any]) -> dict[str, Any]:
    if role != "teacher":
        raise DomainError("FORBIDDEN", "班级横向比较仅教师可用")
    dataset_id = _dataset(ctx)
    class_ids = _classes(ctx, args["class_ids"])
    node_id = _node(ctx, args.get("node_id"))
    if not ctx.run_id:
        raise DomainError("CONTEXT_MISMATCH", "班级比较必须绑定一次已完成的分析运行")
    with _read_session(ctx) as db:
        view = analysis_queries.compare_formal_classes(
            db,
            dataset_id=dataset_id,
            run_id=ctx.run_id,
            node_id=node_id,
            class_ids=class_ids,
            comparison_scope=args.get("comparison_scope"),
        )
    data = view.model_dump(mode="json")
    limitations = list(data.get("limitations") or []) + [
        "只按同口径指标逐项比较并展示两个班级的人数与有效观测人数；不提供总分差，也不据此评价教师教学",
    ]
    data["limitations"] = limitations
    return _ok(data, evidence_refs=list(data.get("evidence_refs") or []), limitations=limitations)


# --------------------------------------------------------------------------- #
# resolve_evidence
# --------------------------------------------------------------------------- #


def _resolve_evidence(ctx: ServerContext, role: str, args: dict[str, Any]) -> dict[str, Any]:
    from study_companion.db import evidence_repository

    dataset_id = _dataset(ctx)
    raw_refs = args.get("refs")
    if not isinstance(raw_refs, list) or not 1 <= len(raw_refs) <= 20:
        raise DomainError("INVALID_ARGUMENT", "refs 必须是 1..20 个 EvidenceRef")
    refs: list[EvidenceRef] = []
    for item in raw_refs:
        if not isinstance(item, dict):
            raise DomainError("INVALID_ARGUMENT", "每个 ref 必须是对象")
        try:
            ref = EvidenceRef.model_validate(item)
        except Exception as exc:
            raise DomainError("INVALID_ARGUMENT", "证据引用不符合 EvidenceRef 契约") from exc
        if ref.dataset_id is not None and ref.dataset_id != dataset_id:
            raise DomainError("SCOPE_DENIED", "证据引用不属于当前数据集")
        refs.append(ref)

    scope = _ReadScope(
        role=role,
        dataset_id=dataset_id,
        student_id=ctx.bound_student_id if role == "student" else None,
    )
    details: list[dict[str, Any]] = []
    limitations: list[str] = []
    with _read_session(ctx) as db:
        for ref in refs:
            detail = evidence_repository.resolve(db, ref, None, scope)
            details.append(
                {
                    "ref": ref.model_dump(mode="json"),
                    "title": detail.title,
                    "detail_type": detail.detail_type,
                    "summary": detail.summary,
                    "visibility": detail.visibility,
                    "detail": detail.detail,
                    "related_refs": [item.model_dump(mode="json") for item in detail.related_refs][:20],
                    "limitations": list(detail.limitations),
                }
            )
            limitations.extend(str(item) for item in detail.limitations)
    limitations.extend(
        [
            "只解析本轮引用别名对应的正式对象；不按名称猜测，不做模糊匹配",
            "学生投影已去掉参考答案、题解与评分点描述",
        ]
    )
    return _ok(
        {"items": details},
        evidence_refs=[ref.model_dump(mode="json") for ref in refs],
        limitations=limitations,
    )


# --------------------------------------------------------------------------- #
# get_quality_report
# --------------------------------------------------------------------------- #


def _quality_a(db: Session, dataset_id: str, catalog_version: str | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    from study_companion.db import models
    from study_companion.db import version_repository as repo

    dataset = db.get(models.Dataset, dataset_id)
    if dataset is None:
        raise DomainError("NOT_FOUND", "数据集不存在")
    key = repo.FormalKey(
        family_id=dataset.family_id or "",
        dataset_id=dataset_id,
        kind="knowledge",
        target_key=catalog_version or "",
    )
    version = repo.read_formal(db, key)
    if version is None or not version.payload_json:
        raise DomainError("RESULT_NOT_READY", "尚未构建正式知识体系，暂无 A 质量报告")
    summary = (version.payload_json or {}).get("review_summary") or {}
    if not summary:
        raise DomainError("RESULT_NOT_READY", "该知识版本没有质量报告")
    data = {
        "module": "A",
        "knowledge_version": version.version_id,
        "report": summary,
        "limitations": [
            "A 质量报告取自已发布知识版本的审核汇总，不重新统计",
            "知识库尚未发布时没有可读报告，不使用候选或草稿数据",
        ],
    }
    return data, []


def _quality_b(
    ctx: ServerContext, db: Session, dataset_id: str, node_id: str, page: int, page_size: int
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    from study_companion.db import version_repository as repo

    run_id = ctx.run_id
    if not run_id:
        run_id = repo.latest_run_id(db, dataset_id) or ""
    if not run_id:
        raise DomainError("RESULT_NOT_READY", "该数据集还没有已完成的分析运行")
    report = analysis_queries.b_quality_report(
        db,
        dataset_id=dataset_id,
        run_id=run_id,
        node_id=node_id,
        generated_at=repo.now_iso(),
    )
    issues = list(report.issues)
    start = (page - 1) * page_size
    chunk = issues[start : start + page_size]
    refs = [
        {"kind": "record", "id": record_id, "run_id": run_id, "dataset_id": dataset_id}
        for issue in chunk
        for record_id in issue.record_ids
    ]
    data = {
        "module": "B",
        "run_id": run_id,
        "node_id": node_id,
        "report": {
            "scope": report.scope,
            "counts": report.counts,
            "coverage": [item.model_dump(mode="json") for item in report.coverage],
            "issues": [item.model_dump(mode="json") for item in chunk],
            "issues_total": len(issues),
            "page": page,
            "page_size": page_size,
            "generated_at": report.generated_at,
            "rule_version": report.rule_version,
        },
        "limitations": list(report.limitations),
    }
    return data, refs


def _quality_c(
    db: Session, dataset_id: str, node_id: str, class_id: str | None, window_days: int, page: int, page_size: int
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    from datetime import UTC, datetime, timedelta

    end = datetime.now(UTC)
    try:
        window = c_quality.QualityWindow(start=end - timedelta(days=window_days), end=end)
    except ValueError as exc:  # pragma: no cover - 参数已校验
        raise DomainError("INVALID_ARGUMENT", str(exc)) from exc
    report = c_quality.build_report(
        db, dataset_id=dataset_id, node_id=node_id, window=window, class_id=class_id
    )
    items = list((report.issues or {}).get("items") or [])
    start = (page - 1) * page_size
    chunk = items[start : start + page_size]
    refs = [
        {
            "kind": "recommendation",
            "id": str(item.get("issue_id")),
            "plan_version": item.get("plan_version"),
            "dataset_id": dataset_id,
        }
        for item in chunk
        if item.get("plan_version")
    ]
    data = {
        "module": "C",
        "node_id": node_id,
        "class_id": class_id,
        "report": {
            "scope": report.scope,
            "window": report.window,
            "counts": report.counts,
            "coverage": report.coverage,
            "issues": chunk,
            "issues_total": len(items),
            "page": page,
            "page_size": page_size,
            "generated_at": report.generated_at,
            "rule_version": report.rule_version,
        },
        "limitations": list(report.limitations),
    }
    return data, refs


def _quality_report(ctx: ServerContext, role: str, args: dict[str, Any]) -> dict[str, Any]:
    if role != "teacher":
        raise DomainError("FORBIDDEN", "数据质量报告仅教师可用")
    dataset_id = _dataset(ctx)
    module = args.get("module")
    if module not in {"A", "B", "C"}:
        raise DomainError("INVALID_ARGUMENT", "module 只能是 A/B/C")
    page, page_size = _page(args)
    node_id = args.get("node_id") or ctx.bound_node_id
    class_id = args.get("class_id")
    if class_id is not None and ctx.allowed_class_ids is not None and class_id not in ctx.allowed_class_ids:
        raise DomainError("SCOPE_DENIED", "班级不在当前会话允许范围内")
    window_days = args.get("window_days", 30)
    if isinstance(window_days, bool) or not isinstance(window_days, int) or not 1 <= window_days <= 365:
        raise DomainError("INVALID_ARGUMENT", "window_days 必须在 1..365 之间")

    with _read_session(ctx) as db:
        if module == "A":
            data, refs = _quality_a(db, dataset_id, ctx.catalog_version or None)
            data["limitations"].append("A 报告当前没有可解析到 EvidenceRef 的引用，只给汇总计数")
        else:
            if not node_id:
                raise DomainError("INVALID_ARGUMENT", "B/C 质量报告需要 node_id")
            if module == "B":
                data, refs = _quality_b(ctx, db, dataset_id, node_id, page, page_size)
            else:
                data, refs = _quality_c(db, dataset_id, node_id, class_id, window_days, page, page_size)
    limitations = list(data.get("limitations") or []) + [
        "质量报告只统计已发生的事实；分母为 0 时不给百分比",
        "报告按角色裁剪，仅教师可读",
    ]
    data["limitations"] = limitations
    return _ok(data, evidence_refs=refs[:20], limitations=limitations)


# --------------------------------------------------------------------------- #
# get_plan_history / get_plan_reason
# --------------------------------------------------------------------------- #


def _plan_refs(payload: dict[str, Any], dataset_id: str) -> list[dict[str, Any]]:
    plan_version = payload.get("plan_version")
    if not plan_version:
        return []
    return [
        {
            "kind": "recommendation",
            "id": plan_version,
            "plan_version": plan_version,
            "dataset_id": dataset_id,
        }
    ]


def _plan_history(ctx: ServerContext, role: str, args: dict[str, Any]) -> dict[str, Any]:
    dataset_id = _dataset(ctx)
    student_id = _student(ctx, role, args.get("student_id"))
    page, page_size = _page(args)
    with _read_session(ctx) as db:
        payload = c_query.list_plan_revisions(
            db, dataset_id=dataset_id, student_id=student_id, role=role, page=page, page_size=page_size
        )
    refs = [
        {
            "kind": "recommendation",
            "id": item["plan_version"],
            "plan_version": item["plan_version"],
            "dataset_id": dataset_id,
        }
        for item in payload["items"]
        if item.get("plan_version")
    ]
    limitations = list(payload.get("limitations") or [])
    if role == "student":
        limitations.append("学生只能查看本人已发布方案的修订历史，不含草稿与教师内部说明")
    return _ok(payload, evidence_refs=refs, limitations=limitations)


def _plan_reason(ctx: ServerContext, role: str, args: dict[str, Any]) -> dict[str, Any]:
    dataset_id = _dataset(ctx)
    student_id = _student(ctx, role, args.get("student_id"))
    with _read_session(ctx) as db:
        payload = c_query.plan_reason_payload(
            db,
            dataset_id=dataset_id,
            student_id=student_id,
            role=role,
            plan_version=args.get("plan_version"),
            node_id=args.get("node_id"),
            group_id=args.get("group_id"),
            activity_id=args.get("activity_id"),
        )
    refs: list[dict[str, Any]] = []
    for group in payload.get("groups", []):
        if role != "teacher":
            break
        refs.extend(group.get("evidence_refs") or [])
    refs = refs[:20]
    if payload.get("plan_version"):
        refs.append(
            {
                "kind": "recommendation",
                "id": payload["plan_version"],
                "plan_version": payload["plan_version"],
                "dataset_id": dataset_id,
            }
        )
    return _ok(payload, evidence_refs=refs, limitations=list(payload.get("limitations") or []))


# --------------------------------------------------------------------------- #
# search_learning_materials
# --------------------------------------------------------------------------- #


def _search_materials(ctx: ServerContext, role: str, args: dict[str, Any]) -> dict[str, Any]:
    dataset_id = _dataset(ctx)
    if not ctx.catalog_version:
        raise DomainError("CONTEXT_MISMATCH", "材料检索必须绑定课程目录版本")
    page, page_size = _page(args)
    kp_ids = args.get("kp_ids")
    if kp_ids is not None:
        if not isinstance(kp_ids, list) or not 1 <= len(kp_ids) <= 4 or any(
            not isinstance(item, str) or not item for item in kp_ids
        ):
            raise DomainError("INVALID_ARGUMENT", "kp_ids 必须是 1..4 个非空字符串")
    with _read_session(ctx) as db:
        payload = c_query.search_formal_materials(
            db,
            dataset_id=dataset_id,
            catalog_version=ctx.catalog_version,
            role=role,
            purpose=args.get("purpose", "practice"),
            kp_ids=kp_ids,
            question_type=args.get("question_type"),
            difficulty=args.get("difficulty"),
            page=page,
            page_size=page_size,
        )
    refs = [
        {
            "kind": "material",
            "id": item["material_id"],
            "catalog_version": ctx.catalog_version,
            "dataset_id": dataset_id,
        }
        for item in payload["items"]
    ]
    return _ok(payload, evidence_refs=refs, limitations=list(payload.get("limitations") or []))


# --------------------------------------------------------------------------- #
# get_lesson_view
# --------------------------------------------------------------------------- #


def _lesson_view(ctx: ServerContext, role: str, args: dict[str, Any]) -> dict[str, Any]:
    dataset_id = _dataset(ctx)
    student_id = _student(ctx, role, args.get("student_id"))
    activity_id = args.get("activity_id")
    if not activity_id or not isinstance(activity_id, str):
        raise DomainError("INVALID_ARGUMENT", "activity_id 不能为空")
    with _read_session(ctx) as db:
        payload = c_query.micro_lesson_view(
            db,
            dataset_id=dataset_id,
            student_id=student_id,
            activity_id=activity_id,
            plan_version=args.get("plan_version"),
            node_id=args.get("node_id"),
        )
    lesson = payload.get("lesson") or {}
    refs = [
        {
            "kind": "recommendation",
            "id": activity_id,
            "plan_version": lesson.get("plan_version"),
            "dataset_id": dataset_id,
        }
    ]
    return _ok(payload, evidence_refs=refs, limitations=list(payload.get("limitations") or []))


# --------------------------------------------------------------------------- #
# prepare_plan_revision
# --------------------------------------------------------------------------- #


def _prepare_revision(ctx: ServerContext, role: str, args: dict[str, Any]) -> dict[str, Any]:
    if role != "teacher":
        raise DomainError("FORBIDDEN", "只有教师可以准备方案修订候选")
    dataset_id = _dataset(ctx)
    requested = args.get("student_id")
    if not requested or not isinstance(requested, str):
        raise DomainError("INVALID_ARGUMENT", "必须明确指定要修订的学生")
    student_id = _student(ctx, role, requested)
    if ctx.bound_student_id and ctx.bound_student_id != student_id:
        raise DomainError("CONTEXT_MISMATCH", "写目标必须与会话绑定学生一致")
    base_plan_version = args.get("base_plan_version")
    if base_plan_version is not None and not isinstance(base_plan_version, str):
        raise DomainError("INVALID_ARGUMENT", "base_plan_version 必须是字符串或 null")
    intent = args.get("intent")
    if not isinstance(intent, dict):
        raise DomainError("INVALID_ARGUMENT", "intent 必须是 RevisionIntent 对象")
    actor = str(ctx.extras.get("owner_ref") or "agent")
    with _read_session(ctx) as db:
        payload = c_query.prepare_revision_candidate(
            db,
            dataset_id=dataset_id,
            student_id=student_id,
            actor=actor,
            intent=intent,
            base_plan_version=base_plan_version,
            node_id=args.get("node_id") or ctx.bound_node_id,
        )
    proposal = payload.get("proposal") or {}
    refs = [
        {
            "kind": "recommendation",
            "id": proposal.get("proposal_id") or "",
            "plan_version": proposal.get("base_plan_version"),
            "dataset_id": dataset_id,
        }
    ]
    data = {
        **payload,
        "student_id": student_id,
        "side_effects": "candidate_only",
        "formal_plan_version": None,
    }
    limitations = list(payload.get("limitations") or []) + [
        "本轮不创建正式方案版本、不移动正式指针；正式发布必须由教师确认后走 04 发布路径",
    ]
    data["limitations"] = limitations
    return _ok(data, evidence_refs=refs, limitations=limitations)


# --------------------------------------------------------------------------- #
# submit_plan_revision / get_revision_status
# --------------------------------------------------------------------------- #

STATUS_DRAFT = "draft"
STATUS_PENDING = "pending"
STATUS_CONFIRMED = "confirmed"
STATUS_REJECTED = "rejected"

REVISION_STATUS_VALUES = (STATUS_DRAFT, STATUS_PENDING, STATUS_CONFIRMED, STATUS_REJECTED)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def _is_expired(value: str | None) -> bool:
    parsed = _parse_iso(value)
    return parsed is not None and parsed <= datetime.now(UTC)


def _candidate(db: Session, *, dataset_id: str, proposal_id: str) -> Any:
    """读出候选并做对象级归属校验；不返回越权对象的存在性细节。"""

    from study_companion.db import models

    if not isinstance(proposal_id, str) or not proposal_id.strip():
        raise DomainError("INVALID_ARGUMENT", "proposal_id 不能为空")
    proposal = db.get(models.Proposal, (proposal_id, 1))
    if proposal is None:
        raise DomainError("NOT_FOUND", "候选不存在")
    snapshot = proposal.snapshot_json or {}
    if snapshot.get("dataset_id") != dataset_id:
        raise DomainError("FORBIDDEN", "候选不属于当前数据集")
    if not snapshot.get("student_id"):
        raise DomainError("CONTEXT_MISMATCH", "候选缺少学生绑定")
    return proposal


def _revision_row(db: Session, proposal: Any) -> Any:
    from study_companion.db import models

    rows = db.scalars(
        select(models.PlanRevision)
        .where(models.PlanRevision.proposal_id == proposal.proposal_id)
        .order_by(models.PlanRevision.created_at.desc())
    ).all()
    return next((row for row in rows if row.result_version), rows[0] if rows else None)


def _confirmation_rows(db: Session, proposal: Any) -> list[Any]:
    from study_companion.db import models

    rows = db.scalars(
        select(models.Confirmation)
        .where(
            models.Confirmation.kind == "plan_revision",
            models.Confirmation.scope_key == proposal.scope_key,
        )
        .order_by(models.Confirmation.created_at.desc())
    ).all()
    return [
        row
        for row in rows
        if any(
            (target or {}).get("proposal_id") == proposal.proposal_id
            for target in (row.targets_json or [])
        )
    ]


def resolve_revision_target(
    db: Session,
    *,
    dataset_id: str,
    proposal_id: str | None = None,
    revision_id: str | None = None,
    confirmation_id: str | None = None,
) -> Any:
    """把候选、修订或确认编号解析为同一候选，并做数据集归属校验。"""

    from study_companion.db import models

    selectors = [
        value for value in (proposal_id, revision_id, confirmation_id) if value is not None
    ]
    if len(selectors) != 1:
        raise DomainError(
            "INVALID_ARGUMENT",
            "必须且只能提供一个 proposal_id、revision_id 或 confirmation_id",
        )
    if proposal_id is not None:
        return _candidate(db, dataset_id=dataset_id, proposal_id=proposal_id)
    if revision_id is not None:
        if not isinstance(revision_id, str) or not revision_id.strip():
            raise DomainError("INVALID_ARGUMENT", "revision_id 不能为空")
        row = db.get(models.PlanRevision, revision_id)
        if row is None or not row.proposal_id:
            raise DomainError("NOT_FOUND", "修订记录不存在或不属于 Agent 候选")
        return _candidate(db, dataset_id=dataset_id, proposal_id=str(row.proposal_id))
    if not isinstance(confirmation_id, str) or not confirmation_id.strip():
        raise DomainError("INVALID_ARGUMENT", "confirmation_id 不能为空")
    confirmation = db.get(models.Confirmation, confirmation_id)
    if confirmation is None or confirmation.kind != "plan_revision":
        raise DomainError("NOT_FOUND", "确认记录不存在")
    target = next(
        (
            item
            for item in (confirmation.targets_json or [])
            if (item or {}).get("proposal_id")
        ),
        None,
    )
    if target is None:
        raise DomainError("NOT_FOUND", "确认记录没有对应的候选")
    return _candidate(db, dataset_id=dataset_id, proposal_id=str(target["proposal_id"]))


def revision_status_payload(db: Session, proposal: Any) -> dict[str, Any]:
    """只读投影：候选当前处于 draft/pending/confirmed/rejected 哪一步。

    状态完全由已存的候选、确认和修订记录推导，不写库、不发布。
    """

    confirmations = _confirmation_rows(db, proposal)
    accepted = next((row for row in confirmations if row.status == "accepted"), None)
    rejected = next((row for row in confirmations if row.status == "rejected"), None)
    pending = next((row for row in confirmations if row.status == "pending"), None)
    pending_active = pending if pending is not None and not _is_expired(pending.expires_at) else None
    revision = _revision_row(db, proposal)
    published_revision = revision if revision is not None and revision.result_version else None
    proposal_expired = proposal.state == "expired"

    if proposal.state == "accepted" or accepted is not None or published_revision is not None:
        status = STATUS_CONFIRMED
        chosen = accepted or pending
        plan_version = published_revision.result_version if published_revision else None
    elif proposal.state == "rejected" or rejected is not None:
        status = STATUS_REJECTED
        chosen = rejected
        plan_version = None
    elif pending_active is not None:
        status = STATUS_PENDING
        chosen = pending_active
        plan_version = None
    else:
        # 未提交、确认已过期或候选自行过期，都退回 draft，由教师重新提交或重建。
        status = STATUS_DRAFT
        chosen = pending
        plan_version = None

    body = proposal.body_json or {}
    review_info = body.get("review_info") or {}
    expires_at = chosen.expires_at if chosen is not None else proposal.expires_at
    confirmation_expired = bool(
        chosen is not None and chosen.status == "pending" and _is_expired(chosen.expires_at)
    )
    candidate_expired = _is_expired(proposal.expires_at)
    if status == STATUS_CONFIRMED:
        next_step = "none"
    elif status == STATUS_REJECTED:
        next_step = "regenerate_candidate"
    elif status == STATUS_PENDING:
        next_step = "await_teacher_confirmation"
    elif candidate_expired or proposal_expired:
        next_step = "regenerate_candidate"
    else:
        next_step = "submit_for_confirmation"

    limitations = [
        "状态查询只读：不写候选、不生成方案版本、不移动正式指针",
        "发布只能由教师确认后走 04 agent_revision 路径；本工具不提供发布能力",
    ]
    if status == STATUS_PENDING:
        limitations.append("等待教师确认：模型和学生都不能代替教师确认或发布")
    if confirmation_expired:
        limitations.append("上一次确认已过期，需要重新提交候选")
    if status == STATUS_CONFIRMED:
        limitations.append("已发布状态来自正式修订记录；学生只能读本人已发布历史")

    return {
        "proposal_id": proposal.proposal_id,
        "dataset_id": (proposal.snapshot_json or {}).get("dataset_id", ""),
        "student_id": (proposal.snapshot_json or {}).get("student_id", ""),
        "node_id": (proposal.snapshot_json or {}).get("node_id", ""),
        "status": status,
        "proposal_status": proposal.state,
        "confirmation_id": chosen.confirmation_id if chosen is not None else None,
        "confirmation_status": chosen.status if chosen is not None else None,
        "confirmation_expired": confirmation_expired,
        "base_plan_version": proposal.base_plan_version,
        "plan_version": plan_version,
        "revision_id": published_revision.revision_id if published_revision else None,
        "publication_status": "published" if plan_version else "unpublished",
        "review_status": review_info.get("status") or ("validated" if plan_version else "draft"),
        "expires_at": expires_at,
        "can_submit": status == STATUS_DRAFT and not candidate_expired,
        "can_publish": False,
        "requires_teacher_confirmation": status == STATUS_PENDING,
        "next_step": next_step,
        "limitations": limitations,
    }


def revision_status(
    db: Session,
    *,
    dataset_id: str,
    proposal_id: str | None = None,
    revision_id: str | None = None,
    confirmation_id: str | None = None,
) -> dict[str, Any]:
    proposal = resolve_revision_target(
        db,
        dataset_id=dataset_id,
        proposal_id=proposal_id,
        revision_id=revision_id,
        confirmation_id=confirmation_id,
    )
    return revision_status_payload(db, proposal)


def _submit_payload(
    proposal: Any,
    confirmation: Any,
    *,
    status: str,
    idempotent: bool,
    side_effects: str,
    plan_version: str | None = None,
) -> dict[str, Any]:
    body = proposal.body_json or {}
    limitations = [
        "提交只登记教师确认，不创建正式方案版本、不移动正式指针",
        "正式发布必须由教师确认后走 04 agent_revision 路径；本工具没有发布能力",
    ]
    if status == STATUS_PENDING:
        limitations.append("确认由教师完成，模型和学生都不能代替；重复提交返回同一确认")
    return {
        "proposal_id": proposal.proposal_id,
        "student_id": (proposal.snapshot_json or {}).get("student_id", ""),
        "node_id": (proposal.snapshot_json or {}).get("node_id", ""),
        "base_plan_version": proposal.base_plan_version,
        "status": status,
        "confirmation_id": confirmation.confirmation_id if confirmation is not None else None,
        "confirmation_status": confirmation.status if confirmation is not None else None,
        "expires_at": confirmation.expires_at if confirmation is not None else proposal.expires_at,
        "plan_version": plan_version,
        "publication_status": "published" if plan_version else "unpublished",
        "review_status": (body.get("review_info") or {}).get("status")
        or ("validated" if plan_version else "draft"),
        "requires_teacher_confirmation": status == STATUS_PENDING,
        "idempotent": idempotent,
        "side_effects": side_effects,
        "limitations": limitations,
    }


def submit_revision(
    db: Session,
    *,
    dataset_id: str,
    proposal_id: str,
    actor: str,
    student_id: str | None = None,
    conversation_id: str | None = None,
    message_id: str | None = None,
) -> dict[str, Any]:
    """把已有候选提交给教师确认。

    只创建/复用 ``confirmations`` 里的待确认记录，不发布、不切正式指针。
    审核沿用 04 的完整审核入口 ``review_plan_rules``；内容硬门复用
    ``formal_content_issues``。教师确认后由既有 ``decide_confirmation`` 走
    ``agent_revision`` 发布。
    """

    from study_companion.agent import proposals as proposal_rules
    from study_companion.db import models
    from study_companion.db import version_repository as repo
    from study_companion.recommendation import planner as c_planner
    from study_companion.recommendation.publication import formal_content_issues

    if not isinstance(actor, str) or not actor.strip():
        raise DomainError("CONTEXT_MISMATCH", "缺少受信任的教师会话，不能提交候选确认")

    proposal = _candidate(db, dataset_id=dataset_id, proposal_id=proposal_id)
    snapshot = proposal.snapshot_json or {}
    candidate_student = str(snapshot.get("student_id") or "")
    if student_id is not None and student_id != candidate_student:
        raise DomainError("FORBIDDEN", "候选不属于该学生")
    node_id = str(snapshot.get("node_id") or "")
    if not node_id:
        raise DomainError("CONTEXT_MISMATCH", "候选缺少节点绑定")

    confirmations = _confirmation_rows(db, proposal)
    revision = _revision_row(db, proposal)
    published_revision = revision if revision is not None and revision.result_version else None
    accepted = next((row for row in confirmations if row.status == "accepted"), None)

    if proposal.state == "accepted" or accepted is not None or published_revision is not None:
        return _submit_payload(
            proposal,
            accepted,
            status=STATUS_CONFIRMED,
            idempotent=True,
            side_effects="none",
            plan_version=published_revision.result_version if published_revision else None,
        )

    if proposal.state == "rejected" or any(row.status == "rejected" for row in confirmations):
        raise DomainError("CONFLICT", "候选已被教师拒绝，不能再次提交；请重新生成候选")

    pending = next(
        (
            row
            for row in confirmations
            if row.status == "pending" and not _is_expired(row.expires_at)
        ),
        None,
    )
    if pending is not None:
        if pending.session_ref != actor:
            raise DomainError(
                "CONFLICT", "该候选已有待处理的教师确认，请由发起确认的会话继续处理"
            )
        return _submit_payload(
            proposal,
            pending,
            status=STATUS_PENDING,
            idempotent=True,
            side_effects="none",
        )

    if proposal.state != "ready":
        raise DomainError("CONFLICT", f"候选当前状态为 {proposal.state}，不能提交")
    if _is_expired(proposal.expires_at):
        raise DomainError("REVISION_CONFLICT", "候选已过期，请重新生成候选后再提交")

    dataset = db.get(models.Dataset, dataset_id)
    if dataset is None or not dataset.family_id:
        raise DomainError("NOT_FOUND", "数据集不存在")
    key = repo.FormalKey(
        family_id=dataset.family_id,
        dataset_id=dataset_id,
        kind="plan",
        node_key=node_id,
        target_key=candidate_student,
    )
    head = repo.read_head(db, key)
    if (head.version_id if head else None) != proposal.base_plan_version:
        raise DomainError("DEPENDENCY_STALE", "基础方案已被更新，候选失效，请重新生成")

    body = dict(proposal.body_json or {})
    try:
        draft = RecommendationBundle.model_validate(body.get("draft") or {})
    except Exception as exc:
        raise DomainError("UNPROCESSABLE", "候选正文不可读，不能提交") from exc
    review: ReviewInfo | None = None
    try:
        review = ReviewInfo.model_validate(body.get("review_info") or {})
    except Exception:
        review = None
    if review is None or review.status != "validated":
        review = c_planner.review_plan_rules(draft)
    if review.status != "validated":
        failed = [check.code for check in review.checks if not check.passed]
        raise DomainError(
            "UNPROCESSABLE",
            "候选未通过完整审核，不能提交：" + ("、".join(failed) or "存在未通过项"),
        )
    issues = formal_content_issues(draft)
    if issues:
        raise DomainError(
            "UNPROCESSABLE",
            "候选内容不完整，不能提交：" + "；".join(item.message for item in issues[:3]),
        )

    body["review_info"] = review.model_dump(mode="json")
    body["submitted_at"] = repo.now_iso()
    proposal.body_json = body
    confirmation = proposal_rules.create_confirmation(
        db,
        proposal=proposal,
        session_ref=actor,
        conversation_id=conversation_id,
        message_id=message_id,
        summary=f"确认后会把「{candidate_student}」的新方案发布，原方案保留历史",
    )
    db.flush()
    return _submit_payload(
        proposal,
        confirmation,
        status=STATUS_PENDING,
        idempotent=False,
        side_effects="confirmation_created",
    )


def _submit_revision(ctx: ServerContext, role: str, args: dict[str, Any]) -> dict[str, Any]:
    if role != "teacher":
        raise DomainError("FORBIDDEN", "只有教师可以提交方案修订候选")
    dataset_id = _dataset(ctx)
    proposal_id = args.get("proposal_id")
    if not isinstance(proposal_id, str) or not proposal_id.strip():
        raise DomainError("INVALID_ARGUMENT", "proposal_id 不能为空")
    actor = ctx.extras.get("owner_ref")
    if not isinstance(actor, str) or not actor.strip():
        raise DomainError("CONTEXT_MISMATCH", "缺少受信任的教师会话，不能提交候选确认")
    conversation_id = ctx.extras.get("conversation_id")
    message_id = ctx.extras.get("message_id")
    with _write_session(ctx) as db:
        proposal = _candidate(db, dataset_id=dataset_id, proposal_id=proposal_id)
        student_id = str((proposal.snapshot_json or {}).get("student_id") or "")
        _student(ctx, role, student_id)
        if ctx.bound_student_id and ctx.bound_student_id != student_id:
            raise DomainError("CONTEXT_MISMATCH", "写目标必须与会话绑定学生一致")
        payload = submit_revision(
            db,
            dataset_id=dataset_id,
            proposal_id=proposal_id,
            actor=actor.strip(),
            student_id=student_id,
            conversation_id=conversation_id if isinstance(conversation_id, str) else None,
            message_id=message_id if isinstance(message_id, str) else None,
        )
    refs = [
        {
            "kind": "recommendation",
            "id": payload["proposal_id"],
            "plan_version": payload.get("plan_version") or payload.get("base_plan_version"),
            "dataset_id": dataset_id,
        }
    ]
    limitations = list(payload.get("limitations") or [])
    data = {**payload, "student_id": student_id}
    data["limitations"] = limitations
    return _ok(data, evidence_refs=refs, limitations=limitations)


def _revision_status(ctx: ServerContext, role: str, args: dict[str, Any]) -> dict[str, Any]:
    if role != "teacher":
        raise DomainError("FORBIDDEN", "只有教师可以查询方案修订状态")
    dataset_id = _dataset(ctx)
    selectors = {
        key: args.get(key)
        for key in ("proposal_id", "revision_id", "confirmation_id")
        if args.get(key) is not None
    }
    if len(selectors) != 1:
        raise DomainError(
            "INVALID_ARGUMENT",
            "必须且只能提供一个 proposal_id、revision_id 或 confirmation_id",
        )
    for key, value in selectors.items():
        if not isinstance(value, str) or not value.strip():
            raise DomainError("INVALID_ARGUMENT", f"{key} 不能为空")
    with _read_session(ctx) as db:
        proposal = resolve_revision_target(db, dataset_id=dataset_id, **selectors)
        student_id = str((proposal.snapshot_json or {}).get("student_id") or "")
        _student(ctx, role, student_id)
        payload = revision_status_payload(db, proposal)
    refs = [
        {
            "kind": "recommendation",
            "id": payload["proposal_id"],
            "plan_version": payload.get("plan_version") or payload.get("base_plan_version"),
            "dataset_id": dataset_id,
        }
    ]
    limitations = list(payload.get("limitations") or [])
    data = {**payload, "student_id": student_id}
    data["limitations"] = limitations
    return _ok(data, evidence_refs=refs, limitations=limitations)


# --------------------------------------------------------------------------- #
# 注册
# --------------------------------------------------------------------------- #


def _definitions() -> list[tuple[str, dict[str, Any], Callable[[ServerContext, str, dict[str, Any]], dict[str, Any]]]]:
    return [
        (
            "get_student_progress",
            _schema(
                "get_student_progress",
                "比较该学生两次正式分析结果，返回是否可比与逐指标进展；没有基线时明确不可比",
                {
                    "student_id": {"type": "string", "minLength": 1, "maxLength": 64},
                    "node_id": {"type": "string", "minLength": 1, "maxLength": 64},
                    "baseline_analysis_version": {"type": "string", "minLength": 1, "maxLength": 128},
                    "current_analysis_version": {"type": "string", "minLength": 1, "maxLength": 128},
                    "scope": {"type": "string", "minLength": 1, "maxLength": 64},
                },
                [],
            ),
            _progress,
        ),
        (
            "compare_classes",
            _schema(
                "compare_classes",
                "按 B 的比较规则比较两个已授权班级的同口径指标；仅教师可用",
                {
                    "class_ids": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1, "maxLength": 64},
                        "minItems": 2,
                        "maxItems": 2,
                    },
                    "node_id": {"type": "string", "minLength": 1, "maxLength": 64},
                    "comparison_scope": {"type": "string", "minLength": 1, "maxLength": 64},
                },
                ["class_ids"],
            ),
            _compare_classes,
        ),
        (
            "resolve_evidence",
            _schema(
                "resolve_evidence",
                "按 EvidenceRef 解析正式证据的安全投影（按角色裁剪）",
                {
                    "refs": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "kind": {"type": "string", "enum": [
                                    "record", "metric", "material", "curriculum", "knowledge_point",
                                    "relation", "annotation", "rubric_point", "learning_event",
                                    "attempt", "grade", "finding", "progress", "recommendation",
                                    "source_page", "lesson",
                                ]},
                                "id": {"type": "string", "minLength": 1, "maxLength": 128},
                                "run_id": {"type": "string", "minLength": 1, "maxLength": 128},
                                "catalog_version": {"type": "string", "minLength": 1, "maxLength": 128},
                                "dataset_id": {"type": "string", "minLength": 1, "maxLength": 64},
                                "knowledge_version": {"type": "string", "minLength": 1, "maxLength": 128},
                                "analysis_version": {"type": "string", "minLength": 1, "maxLength": 128},
                                "plan_version": {"type": "string", "minLength": 1, "maxLength": 128},
                                "material_id": {"type": "string", "minLength": 1, "maxLength": 128},
                                "attempt_id": {"type": "string", "minLength": 1, "maxLength": 128},
                                "curriculum_type": {"type": "string", "enum": ["topic", "indicator", "source"]},
                            },
                            "required": ["kind", "id"],
                        },
                        "minItems": 1,
                        "maxItems": 20,
                    }
                },
                ["refs"],
            ),
            _resolve_evidence,
        ),
        (
            "get_quality_report",
            _schema(
                "get_quality_report",
                "读取 A/B/C 模块的正式质量报告（按角色裁剪，仅教师）",
                {
                    "module": {"type": "string", "enum": ["A", "B", "C"]},
                    "node_id": {"type": "string", "minLength": 1, "maxLength": 64},
                    "class_id": {"type": "string", "minLength": 1, "maxLength": 64},
                    "window_days": {"type": "integer", "minimum": 1, "maximum": 365},
                    "page": {"type": "integer", "minimum": 1},
                    "page_size": {"type": "integer", "minimum": 1, "maximum": PAGE_MAX},
                },
                ["module"],
            ),
            _quality_report,
        ),
        (
            "get_plan_history",
            _schema(
                "get_plan_history",
                "读取正式方案的修订历史（学生仅本人已发布历史）",
                {
                    "student_id": {"type": "string", "minLength": 1, "maxLength": 64},
                    "page": {"type": "integer", "minimum": 1},
                    "page_size": {"type": "integer", "minimum": 1, "maximum": PAGE_MAX},
                },
                [],
            ),
            _plan_history,
        ),
        (
            "get_plan_reason",
            _schema(
                "get_plan_reason",
                "读取正式方案里已保存的目标、策略、理由与限制，不重新编造理由",
                {
                    "student_id": {"type": "string", "minLength": 1, "maxLength": 64},
                    "plan_version": {"type": "string", "minLength": 1, "maxLength": 128},
                    "node_id": {"type": "string", "minLength": 1, "maxLength": 64},
                    "group_id": {"type": "string", "minLength": 1, "maxLength": 128},
                    "activity_id": {"type": "string", "minLength": 1, "maxLength": 128},
                },
                [],
            ),
            _plan_reason,
        ),
        (
            "search_learning_materials",
            _schema(
                "search_learning_materials",
                "在正式目录里按知识点/用途/题型/难度筛选材料；学生视图不含答案与解析",
                {
                    "kp_ids": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1, "maxLength": 128},
                        "minItems": 1,
                        "maxItems": 4,
                    },
                    "purpose": {"type": "string", "enum": ["practice", "support"]},
                    "question_type": {"type": "string", "enum": ["choice", "fill_blank", "solution"]},
                    "difficulty": {"type": "string", "enum": ["basic", "intermediate", "advanced"]},
                    "page": {"type": "integer", "minimum": 1},
                    "page_size": {"type": "integer", "minimum": 1, "maximum": PAGE_MAX},
                },
                ["purpose"],
            ),
            _search_materials,
        ),
        (
            "get_lesson_view",
            _schema(
                "get_lesson_view",
                "读取已发布微课堂的安全正文，不重新生成、不计分",
                {
                    "activity_id": {"type": "string", "minLength": 1, "maxLength": 128},
                    "plan_version": {"type": "string", "minLength": 1, "maxLength": 128},
                    "node_id": {"type": "string", "minLength": 1, "maxLength": 64},
                    "student_id": {"type": "string", "minLength": 1, "maxLength": 64},
                },
                ["activity_id"],
            ),
            _lesson_view,
        ),
        (
            "prepare_plan_revision",
            _schema(
                "prepare_plan_revision",
                "按教师修订意图生成方案候选；只生成候选，不产生正式方案版本",
                {
                    "student_id": {"type": "string", "minLength": 1, "maxLength": 64},
                    "base_plan_version": {"type": ["string", "null"], "maxLength": 128},
                    "node_id": {"type": "string", "minLength": 1, "maxLength": 64},
                    "intent": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "goal": {"type": "string", "minLength": 1, "maxLength": 300},
                            "reason": {"type": "string", "minLength": 1, "maxLength": 500},
                            "requested_changes": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "properties": {
                                        "kind": {"type": "string", "enum": [
                                            "reduce_questions",
                                            "remove_pending_activity",
                                            "reorder_pending",
                                            "replace_material",
                                            "adjust_goal",
                                            "update_intro",
                                        ]},
                                        "group_id": {"type": "string", "minLength": 1, "maxLength": 128},
                                        "max_questions": {"type": "integer", "minimum": 1, "maximum": 8},
                                        "activity_id": {"type": "string", "minLength": 1, "maxLength": 128},
                                        "activity_ids": {
                                            "type": "array",
                                            "items": {"type": "string", "minLength": 1, "maxLength": 128},
                                            "maxItems": 8,
                                        },
                                        "material_id": {"type": "string", "minLength": 1, "maxLength": 128},
                                        "goal": {"type": "string", "minLength": 1, "maxLength": 300},
                                        "text": {"type": "string", "minLength": 1, "maxLength": 500},
                                    },
                                    "required": ["kind"],
                                },
                                "minItems": 1,
                                "maxItems": 8,
                            },
                            "teacher_observations": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "properties": {
                                        "text": {"type": "string", "minLength": 1, "maxLength": 300},
                                        "source": {"type": "string", "enum": ["teacher_statement"]},
                                    },
                                    "required": ["text", "source"],
                                },
                                "maxItems": 8,
                            },
                            "constraints": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "max_questions": {"type": "integer", "minimum": 1, "maximum": 8},
                                    "max_minutes": {"type": "integer", "minimum": 1, "maximum": 60},
                                },
                            },
                            "evidence_refs": {
                                "type": "array",
                                "items": {"type": "object"},
                                "maxItems": 20,
                            },
                        },
                        "required": ["goal", "reason", "requested_changes"],
                    },
                },
                ["student_id", "intent"],
            ),
            _prepare_revision,
        ),
        (
            "submit_plan_revision",
            _schema(
                "submit_plan_revision",
                "把已存在的方案候选提交给教师确认；只登记确认、不发布，重复提交返回同一确认",
                {
                    "proposal_id": {"type": "string", "minLength": 1, "maxLength": 64},
                },
                ["proposal_id"],
            ),
            _submit_revision,
        ),
        (
            "get_revision_status",
            _schema(
                "get_revision_status",
                "按候选/修订/确认查询方案修订状态（draft/pending/confirmed/rejected），只读",
                {
                    "proposal_id": {"type": "string", "minLength": 1, "maxLength": 64},
                    "revision_id": {"type": "string", "minLength": 1, "maxLength": 64},
                    "confirmation_id": {"type": "string", "minLength": 1, "maxLength": 64},
                },
                [],
            ),
            _revision_status,
        ),
    ]


def build_expanded_tools(ctx: ServerContext, role: str, task: TaskSpec | None) -> list[RegisteredTool]:
    """按任务与角色筛选扩展工具；未列出的任务不会得到这些工具。"""

    if task is None:
        return []
    allowed = set(TASK_TOOLS.get(task.task_type, ()))
    if role not in task.roles:
        return []
    tools: list[RegisteredTool] = []
    for name, schema, handler in _definitions():
        if name not in allowed:
            continue
        if name in TEACHER_ONLY and role != "teacher":
            continue
        tools.append(RegisteredTool(schema, _bind(handler, ctx, role)))
    return tools


def _bind(
    handler: Callable[[ServerContext, str, dict[str, Any]], dict[str, Any]],
    ctx: ServerContext,
    role: str,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def call(args: dict[str, Any]) -> dict[str, Any]:
        arguments = {key: value for key, value in args.items() if key != "_call_id"}
        return handler(ctx, role, arguments)

    return call


def extend_registry(
    registry: ToolRegistry, ctx: ServerContext, role: str, task: TaskSpec | None
) -> ToolRegistry:
    """把扩展工具并入已有注册表（按 name 去重，扩展工具优先保留原注册）。"""

    existing = {tool["function"]["name"] for tool in registry.schemas()}
    tools = [
        tool
        for tool in build_expanded_tools(ctx, role, task)
        if tool.schema["function"]["name"] not in existing
    ]
    if not tools:
        return registry
    return ToolRegistry(list(_existing_tools(registry)) + tools)


def _existing_tools(registry: ToolRegistry) -> list[RegisteredTool]:
    """ToolRegistry 未暴露内部条目，这里按 schema+invoke 再取一次。"""

    return list(getattr(registry, "_tools", {}).values())


__all__ = [
    "DB_SESSION_KEY",
    "REVISION_STATUS_VALUES",
    "TASK_TOOLS",
    "TEACHER_ONLY",
    "build_expanded_tools",
    "extend_registry",
    "resolve_revision_target",
    "revision_status",
    "revision_status_payload",
    "submit_revision",
]
