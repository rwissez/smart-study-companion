"""教师意图优化的可调用入口（04 §5.2 optimize）。

``optimize_model.py`` 只管"规范输入 → 模型 → 程序校验"；本模块把它接到正式方案上：

* 候选活动池取当前正式目录里的题目，可选按节点可用题目再筛；
* 已完成、进行中的活动按库里的真实学习状态冻结，模型不能碰；
* 结果转成教师能逐条核对的只读建议；只有"移除/调序第一个任务组"这类
  能由现有活动直接表达的调整，才额外给出一份可带入草稿的 ``RevisionChanges``。

这里不写库、不发布。建议只是候选，教师确认后仍走
``preview_teacher_revision`` → ``publish_teacher_revision``。新增或替换活动需要
材料和三级提示，模型只给活动编号，因此这类建议保持只读，不伪造可发布改动。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from study_companion.contracts import (
    Activity,
    Catalog,
    Recommendation,
    RecommendationBundle,
    RevisionChanges,
    RevisionIntent,
)
from study_companion.model_service import ModelService
from study_companion.recommendation.optimize_model import (
    ALLOWED_OPS,
    OptimizeCandidate,
    OptimizeChange,
    OptimizeConstraints,
    OptimizeOutcome,
    optimize_plan_with_model,
)

#: 与 04 §8 的硬门一致：一次方案最多 8 项活动、60 分钟。
MAX_ACTIVITIES_PER_PLAN = 8
MAX_MINUTES_PER_PLAN = 60
#: 候选池上限；候选题的默认预计时长（正式难度评估不在本入口重算）。
MAX_CANDIDATES = 20
CANDIDATE_ESTIMATE_MINUTES = 4

PREVIEW_NOTE = (
    "这些只是只读建议，不会改动正式方案；确认后仍要先检查草稿（preview），"
    "通过后再确认发布。"
)
BLOCKER_NEW_ACTIVITY = (
    "新增或替换活动需要材料和三级提示，优化只给活动编号；"
    "请在活动列表里手动添加并填写三级提示，再检查草稿。"
)
BLOCKER_GROUP = "现有预览只重建第一个任务组的活动列表；其他任务组的调整需要手动编辑。"
BLOCKER_POSITION = "调序建议缺少可用的目标位置，无法自动套用。"

POOL_NOTE_UNSCOPED = "该节点没有登记可用题目清单，候选池按当前目录版本的全部正式题目取。"
POOL_NOTE_SCOPED = "候选池只取本节点登记的可用题目。"


def candidate_pool_note(*, available_ids: Sequence[str] | None) -> str:
    """说明候选池的来源范围，避免把目录里的题当成节点必做题。"""

    return POOL_NOTE_SCOPED if available_ids else POOL_NOTE_UNSCOPED


def build_candidates(
    catalog: Catalog,
    bundle: RecommendationBundle,
    *,
    available_ids: Sequence[str] | None = None,
    limit: int = MAX_CANDIDATES,
) -> list[OptimizeCandidate]:
    """从正式目录取候选活动；来源固定为 ``catalog``，已在方案里的题不再下发。"""

    in_plan = {
        activity.material_id
        for recommendation in bundle.recommendations
        for activity in recommendation.activities
        if activity.material_id
    }
    allowed = set(available_ids) if available_ids else None
    candidates: list[OptimizeCandidate] = []
    for material in catalog.materials:
        if len(candidates) >= limit:
            break
        if material.material_type != "question" or material.question_type is None:
            continue
        if material.material_id in in_plan:
            continue
        if allowed is not None and material.material_id not in allowed:
            continue
        candidates.append(
            OptimizeCandidate(
                activity_key=material.material_id,
                purpose=material.content[:160],
                activity_kind="question",
                estimate_minutes=CANDIDATE_ESTIMATE_MINUTES,
                source="catalog",
                knowledge_ids=tuple(material.indicator_ids[:4]),
                material_id=material.material_id,
            )
        )
    return candidates


def apply_known_states(
    bundle: RecommendationBundle, states: dict[str, str]
) -> RecommendationBundle:
    """把库里权威的活动状态覆盖到方案上，模型才能看到真实的已完成/进行中。"""

    if not states:
        return bundle
    changed = False
    recommendations: list[Recommendation] = []
    for recommendation in bundle.recommendations:
        activities: list[Activity] = []
        recommendation_changed = False
        for activity in recommendation.activities:
            known = states.get(activity.activity_id or "")
            if known and known != activity.state:
                activities.append(activity.model_copy(update={"state": known}))
                recommendation_changed = True
            else:
                activities.append(activity)
        recommendations.append(
            recommendation.model_copy(update={"activities": activities})
            if recommendation_changed
            else recommendation
        )
        changed = changed or recommendation_changed
    if not changed:
        return bundle
    return bundle.model_copy(update={"recommendations": recommendations})


def build_constraints(protected_activity_ids: Sequence[str] = ()) -> OptimizeConstraints:
    """已完成、进行中的活动一律冻结；上限对齐 04 §8 的硬门。"""

    return OptimizeConstraints(
        max_activities=MAX_ACTIVITIES_PER_PLAN,
        max_minutes=MAX_MINUTES_PER_PLAN,
        locked_activity_keys=tuple(sorted(set(protected_activity_ids))),
        allowed_ops=ALLOWED_OPS,
    )


def optimize_teacher_plan(
    *,
    service: ModelService,
    bundle: RecommendationBundle,
    intent: RevisionIntent,
    candidates: Sequence[OptimizeCandidate],
    known_states: dict[str, str] | None = None,
) -> OptimizeOutcome:
    """按教师意图生成建议；模型不可用时返回 unavailable，不改动方案。"""

    states = dict(known_states or {})
    protected = [
        activity_id
        for activity_id, state in states.items()
        if state in {"completed", "in_progress"}
    ]
    return optimize_plan_with_model(
        service=service,
        intent=intent,
        bundle=apply_known_states(bundle, states),
        candidates=candidates,
        constraints=build_constraints(protected),
    )


# --------------------------------------------------------------------------- #
# 建议视图：把 A/G 编号翻回教师能核对的对象
# --------------------------------------------------------------------------- #


def _index_plan(
    bundle: RecommendationBundle,
) -> tuple[dict[str, Activity], dict[str, str], list[str]]:
    """与 ``optimize_model._index_plan`` 同一编号规则：G1..Gn、A1..Am。"""

    activities: dict[str, Activity] = {}
    group_goal: dict[str, str] = {}
    first_group_keys: list[str] = []
    counter = 0
    for group_number, recommendation in enumerate(bundle.recommendations, start=1):
        group_key = f"G{group_number}"
        group_goal[group_key] = recommendation.goal
        keys: list[str] = []
        for activity in recommendation.activities:
            counter += 1
            activity_key = f"A{counter}"
            activities[activity_key] = activity
            keys.append(activity_key)
        if group_number == 1:
            first_group_keys = keys
    return activities, group_goal, first_group_keys


def _activity_label(activity: Activity) -> str:
    return (
        activity.title
        or activity.purpose
        or activity.material_id
        or activity.activity_id
        or ""
    ).strip()[:120]


def _apply_blocker(
    change: OptimizeChange,
    *,
    first_group_keys: list[str],
) -> str | None:
    if change.op in {"add", "replace"}:
        return BLOCKER_NEW_ACTIVITY
    if change.group_key != "G1" or change.activity_key not in first_group_keys:
        return BLOCKER_GROUP
    if change.op == "reorder" and change.position is None:
        return BLOCKER_POSITION
    if change.op not in {"remove", "reorder"}:
        return BLOCKER_GROUP
    return None


def _change_view(
    change: OptimizeChange,
    *,
    activities: dict[str, Activity],
    group_goal: dict[str, str],
    first_group_keys: list[str],
    candidates_by_key: dict[str, OptimizeCandidate],
) -> dict[str, Any]:
    view: dict[str, Any] = {
        "op": change.op,
        "group_key": change.group_key,
        "group_goal": group_goal.get(change.group_key, ""),
        "activity_key": change.activity_key,
        "activity_title": None,
        "activity_source": None,
        "from_activity_key": change.from_activity_key,
        "from_activity_title": None,
        "position": change.position,
        "reason": change.reason,
        "applicable": False,
        "apply_blocker": None,
    }
    if change.op in {"add", "replace"}:
        candidate = candidates_by_key.get(change.activity_key or "")
        if candidate is not None:
            view["activity_title"] = candidate.purpose[:120]
            view["activity_source"] = candidate.source
    else:
        activity = activities.get(change.activity_key or "")
        if activity is not None:
            view["activity_title"] = _activity_label(activity)
            view["activity_source"] = "current_plan"
    if change.from_activity_key:
        from_activity = activities.get(change.from_activity_key)
        if from_activity is not None:
            view["from_activity_title"] = _activity_label(from_activity)
    blocker = _apply_blocker(change, first_group_keys=first_group_keys)
    view["apply_blocker"] = blocker
    view["applicable"] = blocker is None
    return view


def _draft_activities(
    bundle: RecommendationBundle, changes: Sequence[OptimizeChange]
) -> list[Activity] | None:
    """只应用移除/调序；调序位置按 optimize_model 的同一顺序逐条计算。"""

    if not bundle.recommendations:
        return None
    first = bundle.recommendations[0]
    keys = [f"A{number}" for number in range(1, len(first.activities) + 1)]
    if not keys:
        return None
    by_key = dict(zip(keys, first.activities))
    order = list(keys)
    for change in changes:
        activity_key = change.activity_key or ""
        if activity_key not in order:
            return None
        if change.op == "remove":
            order.remove(activity_key)
        elif change.op == "reorder":
            if change.position is None or not 1 <= change.position <= len(order):
                return None
            order.remove(activity_key)
            order.insert(change.position - 1, activity_key)
        else:  # add / replace 无法由现有活动表达
            return None
    if not order:
        return None
    return [
        by_key[activity_key].model_copy(update={"order": position})
        for position, activity_key in enumerate(order, start=1)
    ]


def build_optimize_view(
    outcome: OptimizeOutcome,
    bundle: RecommendationBundle,
    candidates: Sequence[OptimizeCandidate],
    *,
    base_plan_version: str,
    pool_note: str = "",
) -> dict[str, Any]:
    """把优化结果转成只读建议；只有全部可套用时才附带 RevisionChanges。"""

    activities, group_goal, first_group_keys = _index_plan(bundle)
    candidates_by_key = {item.activity_key: item for item in candidates}
    views = [
        _change_view(
            change,
            activities=activities,
            group_goal=group_goal,
            first_group_keys=first_group_keys,
            candidates_by_key=candidates_by_key,
        )
        for change in outcome.changes
    ]
    blocked_reasons: list[str] = []
    for view in views:
        blocker = view["apply_blocker"]
        if blocker and blocker not in blocked_reasons:
            blocked_reasons.append(blocker)

    revision_changes: dict[str, Any] | None = None
    if outcome.kind == "proposal" and views and all(view["applicable"] for view in views):
        drafted = _draft_activities(bundle, outcome.changes)
        if drafted:
            revision_changes = RevisionChanges(activities=drafted).model_dump(mode="json")

    return {
        "status": outcome.kind,
        "read_only": True,
        "model_used": outcome.model_used,
        "reason": outcome.reason,
        "limitations": [item[:200] for item in outcome.limitations][:8],
        "issues": [issue.model_dump(mode="json") for issue in outcome.issues],
        "base_plan_version": base_plan_version,
        "candidate_count": len(candidates),
        "pool_note": pool_note,
        "changes": views,
        "blocked_reasons": blocked_reasons,
        "revision_changes": revision_changes,
        "preview_note": PREVIEW_NOTE,
    }
