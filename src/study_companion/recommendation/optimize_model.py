"""C 的教师意图优化：在合法候选内提出调整建议（04 §5.2 optimize）。

教师意图由程序装配成规范 JSON，模型只能在当前方案与允许的候选活动之间
增删、替换、调序；程序校验活动 id、来源、进行中/已完成保护和负荷上限，
任何一条不合法就整份拒绝，保留当前方案。模型不可用时返回不可用状态，
不硬改、不伪造结论。

提示词文件为 ``prompts/c/optimize_plan.v1.md``，任务键取 ``optimize_plan.v1``，
命名空间为 ``c``，这样 ``model_service.load_prompt`` 才能按目录 + 文件名取到。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from study_companion.contracts import (
    Activity,
    Recommendation,
    RecommendationBundle,
    RevisionIntent,
    ValidationIssue,
)
from study_companion.model_service import ModelService, ModelTaskRequest

TASK_KEY = "optimize_plan.v1"
PROMPT_VERSION = "1.0"
PROMPT_NAMESPACE = "c"

#: 可用活动池的合法来源；其他来源即使出现在池里也不可被选中。
ALLOWED_SOURCES = frozenset({"catalog", "verified_material", "approved_template"})

#: 允许的调整动作。
ALLOWED_OPS: tuple[str, ...] = ("add", "remove", "replace", "reorder")

#: 程序校验失败的稳定问题码。
ISSUE_UNKNOWN_ACTIVITY = "UNAUTHORIZED_ACTIVITY_ID"
ISSUE_ILLEGAL_SOURCE = "UNAUTHORIZED_ACTIVITY_SOURCE"
ISSUE_UNKNOWN_GROUP = "UNKNOWN_GROUP_KEY"
ISSUE_CONSTRAINT = "CONSTRAINT_VIOLATION"
ISSUE_PROTECTED_STATE = "COMPLETED_ACTIVITY_PROTECTED"


class OptimizeChangeOut(BaseModel):
    """模型返回的单条调整；``activity_key`` 只能来自候选池或当前方案的 A 编号。"""

    model_config = ConfigDict(extra="forbid")

    op: Literal["add", "remove", "replace", "reorder"]
    group_key: str = Field(min_length=1, max_length=40)
    activity_key: str | None = Field(default=None, max_length=40)
    from_activity_key: str | None = Field(default=None, max_length=40)
    position: int | None = Field(default=None, ge=1, le=8)
    reason: str = Field(min_length=1, max_length=300)


class OptimizeOut(BaseModel):
    """优化任务输出：要么给出调整，要么明确说明无法满足。"""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["proposal", "unsatisfiable"]
    reason: str = Field(min_length=1, max_length=300)
    changes: list[OptimizeChangeOut] = Field(default_factory=list, max_length=8)
    limitations: list[str] = Field(default_factory=list, max_length=6)

    @model_validator(mode="after")
    def kind_consistency(self) -> "OptimizeOut":
        if self.kind == "proposal" and not self.changes:
            raise ValueError("proposal requires at least one change")
        if self.kind == "unsatisfiable" and self.changes:
            raise ValueError("unsatisfiable must not carry changes")
        return self


@dataclass(frozen=True)
class OptimizeCandidate:
    """可用活动池中的一项；来源不合法的一项不会被下发给模型。"""

    activity_key: str
    purpose: str = ""
    activity_kind: str = "question"
    estimate_minutes: int = 4
    source: str = "catalog"
    knowledge_ids: tuple[str, ...] = ()
    material_id: str | None = None


@dataclass(frozen=True)
class OptimizeConstraints:
    """程序侧硬约束；模型只能在这个范围内调整。"""

    max_activities: int = 8
    max_minutes: int = 20
    locked_activity_keys: tuple[str, ...] = ()
    forbidden_activity_keys: tuple[str, ...] = ()
    allowed_ops: tuple[str, ...] = ALLOWED_OPS


@dataclass(frozen=True)
class OptimizeChange:
    """通过校验的调整建议（仍是建议，不直接修改方案）。"""

    op: str
    group_key: str
    activity_key: str | None
    from_activity_key: str | None
    position: int | None
    reason: str


@dataclass
class OptimizeOutcome:
    """优化结果；只有 ``kind == "proposal"`` 时 ``changes`` 才可用。"""

    kind: Literal["proposal", "unsatisfiable", "rejected", "unavailable"]
    changes: list[OptimizeChange] = field(default_factory=list)
    issues: list[ValidationIssue] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    reason: str = ""
    model_used: bool = False


@dataclass
class _PlanIndex:
    groups: dict[str, Recommendation] = field(default_factory=dict)
    activities: dict[str, Activity] = field(default_factory=dict)
    activity_group: dict[str, str] = field(default_factory=dict)
    group_activities: dict[str, list[str]] = field(default_factory=dict)
    group_payload: list[dict[str, Any]] = field(default_factory=list)
    activity_payload: list[dict[str, Any]] = field(default_factory=list)


def _index_plan(bundle: RecommendationBundle) -> _PlanIndex:
    index = _PlanIndex()
    activity_counter = 0
    for group_number, rec in enumerate(bundle.recommendations, start=1):
        group_key = f"G{group_number}"
        index.groups[group_key] = rec
        keys: list[str] = []
        for activity in rec.activities:
            activity_counter += 1
            activity_key = f"A{activity_counter}"
            index.activities[activity_key] = activity
            index.activity_group[activity_key] = group_key
            keys.append(activity_key)
            index.activity_payload.append(
                {
                    "activity_key": activity_key,
                    "group_key": group_key,
                    "order": activity.order,
                    "purpose": activity.purpose[:160],
                    "activity_kind": activity.activity_kind,
                    "estimate_minutes": activity.estimate_minutes,
                    "state": activity.state,
                    "material_id": activity.material_id,
                    "knowledge_ids": activity.knowledge_ids[:4],
                }
            )
        index.group_activities[group_key] = keys
        index.group_payload.append(
            {
                "group_key": group_key,
                "goal": rec.goal[:200],
                "primary_strategy": rec.strategy_types[0] if rec.strategy_types else None,
                "activity_keys": keys,
                "estimate_minutes": sum(item.estimate_minutes for item in rec.activities),
            }
        )
    return index


def _candidate_payload(
    candidates: Sequence[OptimizeCandidate], forbidden: set[str]
) -> list[dict[str, Any]]:
    """只下发可用候选：来源不合法或被排除的活动不出现在模型视野里。"""

    return [
        {
            "activity_key": item.activity_key,
            "purpose": item.purpose[:160],
            "activity_kind": item.activity_kind,
            "estimate_minutes": item.estimate_minutes,
            "source": item.source,
            "knowledge_ids": list(item.knowledge_ids)[:4],
            "material_id": item.material_id,
        }
        for item in candidates
        if item.source in ALLOWED_SOURCES and item.activity_key not in forbidden
    ][:20]


def _locked_aliases(index: _PlanIndex, constraints: OptimizeConstraints) -> set[str]:
    locked = set(constraints.locked_activity_keys)
    for activity_key, activity in index.activities.items():
        if activity.activity_id and activity.activity_id in locked:
            locked.add(activity_key)
    return locked


def _build_payload(
    intent: RevisionIntent,
    index: _PlanIndex,
    candidates: Sequence[OptimizeCandidate],
    constraints: OptimizeConstraints,
    locked: set[str],
) -> dict[str, Any]:
    total_minutes = sum(item.estimate_minutes for item in index.activities.values())
    return {
        "intent": {
            "goal": intent.goal[:200],
            "reason": intent.reason[:300],
            "requested_changes": intent.requested_changes[:8],
            "teacher_observations": intent.teacher_observations[:6],
            "constraints": intent.constraints,
        },
        "groups": [
            {**group, "locked_activity_keys": sorted(locked)} for group in index.group_payload
        ],
        "activities": index.activity_payload,
        "candidates": _candidate_payload(candidates, set(constraints.forbidden_activity_keys)),
        "baseline": [
            {"group_key": group_key, "activity_keys": keys}
            for group_key, keys in index.group_activities.items()
        ],
        "frozen_activity_keys": sorted(locked),
        "limits": {
            "max_activities": constraints.max_activities,
            "max_minutes": constraints.max_minutes,
            "remaining_minutes": max(0, constraints.max_minutes - total_minutes),
            "allowed_ops": list(constraints.allowed_ops),
        },
    }


def _issue(
    code: str,
    message: str,
    *,
    recommendation: Recommendation | None = None,
    activity: Activity | None = None,
) -> ValidationIssue:
    return ValidationIssue(
        code=code,
        message=message[:300],
        severity="error",
        recommendation_id=(
            recommendation.recommendation_id if recommendation is not None else None
        ),
        activity_id=(activity.activity_id or None) if activity is not None else None,
    )


def _check_new_activity(
    activity_key: str | None,
    *,
    label: str,
    state: dict[str, list[str]],
    index: _PlanIndex,
    candidates_by_key: dict[str, OptimizeCandidate],
    forbidden: set[str],
    recommendation: Recommendation,
) -> tuple[OptimizeCandidate | None, ValidationIssue | None]:
    if not activity_key:
        return None, _issue(
            ISSUE_CONSTRAINT, f"{label}缺少要增加的活动 id", recommendation=recommendation
        )
    candidate = candidates_by_key.get(activity_key)
    if candidate is None:
        return None, _issue(
            ISSUE_UNKNOWN_ACTIVITY,
            f"{label}使用的活动 {activity_key} 不在可用活动池内",
            recommendation=recommendation,
        )
    if candidate.source not in ALLOWED_SOURCES:
        return None, _issue(
            ISSUE_ILLEGAL_SOURCE,
            f"{label}使用的活动 {activity_key} 来源不合法（{candidate.source}）",
            recommendation=recommendation,
        )
    if activity_key in forbidden:
        return None, _issue(
            ISSUE_CONSTRAINT,
            f"{label}使用的活动 {activity_key} 已被教师或规则排除",
            recommendation=recommendation,
        )
    in_plan = any(activity_key in keys for keys in state.values())
    in_plan = in_plan or any(
        activity.activity_id == activity_key for activity in index.activities.values()
    )
    if in_plan:
        return None, _issue(
            ISSUE_CONSTRAINT,
            f"{label}要增加的活动 {activity_key} 已在当前方案中",
            recommendation=recommendation,
        )
    return candidate, None


def _check_existing_activity(
    activity_key: str | None,
    *,
    label: str,
    group_key: str,
    state: dict[str, list[str]],
    index: _PlanIndex,
    locked: set[str],
    recommendation: Recommendation,
) -> tuple[Activity | None, ValidationIssue | None]:
    if not activity_key:
        return None, _issue(
            ISSUE_CONSTRAINT, f"{label}缺少要调整的活动 id", recommendation=recommendation
        )
    activity = index.activities.get(activity_key)
    if activity is None:
        return None, _issue(
            ISSUE_UNKNOWN_ACTIVITY,
            f"{label}引用的活动 {activity_key} 不在当前方案内",
            recommendation=recommendation,
        )
    if activity_key not in state.get(group_key, []):
        return None, _issue(
            ISSUE_CONSTRAINT,
            f"{label}引用的活动 {activity_key} 不在任务组 {group_key} 内",
            recommendation=recommendation,
        )
    if activity.state in {"completed", "in_progress"}:
        return None, _issue(
            ISSUE_PROTECTED_STATE,
            f"{label}触碰了{_state_label(activity.state)}的活动 {activity_key}，不允许改动",
            recommendation=recommendation,
            activity=activity,
        )
    if activity_key in locked:
        return None, _issue(
            ISSUE_CONSTRAINT,
            f"{label}触碰了教师锁定的活动 {activity_key}",
            recommendation=recommendation,
            activity=activity,
        )
    return activity, None


def _state_label(state: str) -> str:
    return {"completed": "已完成", "in_progress": "进行中", "cancelled": "已取消"}.get(
        state, "已有状态"
    )


def _validate_changes(
    changes: Sequence[OptimizeChangeOut],
    *,
    index: _PlanIndex,
    candidates: Sequence[OptimizeCandidate],
    constraints: OptimizeConstraints,
) -> tuple[list[OptimizeChange], list[ValidationIssue]]:
    candidates_by_key = {item.activity_key: item for item in candidates}
    forbidden = set(constraints.forbidden_activity_keys)
    locked = _locked_aliases(index, constraints)
    state: dict[str, list[str]] = {
        group_key: list(keys) for group_key, keys in index.group_activities.items()
    }
    validated: list[OptimizeChange] = []
    issues: list[ValidationIssue] = []

    for position, change in enumerate(changes, start=1):
        label = f"第 {position} 条调整（{change.op}）"
        if change.op not in constraints.allowed_ops:
            issues.append(
                _issue(ISSUE_CONSTRAINT, f"{label}不在允许的调整类型内")
            )
            continue
        group_key = change.group_key
        recommendation = index.groups.get(group_key)
        if recommendation is None:
            issues.append(
                _issue(ISSUE_UNKNOWN_GROUP, f"{label}指向不存在的任务组 {group_key}")
            )
            continue
        current = state[group_key]

        if change.op == "add":
            _candidate, problem = _check_new_activity(
                change.activity_key,
                label=label,
                state=state,
                index=index,
                candidates_by_key=candidates_by_key,
                forbidden=forbidden,
                recommendation=recommendation,
            )
            if problem is not None:
                issues.append(problem)
                continue
            current.append(change.activity_key or "")
        elif change.op == "remove":
            _activity, problem = _check_existing_activity(
                change.activity_key,
                label=label,
                group_key=group_key,
                state=state,
                index=index,
                locked=locked,
                recommendation=recommendation,
            )
            if problem is not None:
                issues.append(problem)
                continue
            current.remove(change.activity_key or "")
        elif change.op == "replace":
            _activity, problem = _check_existing_activity(
                change.from_activity_key,
                label=label,
                group_key=group_key,
                state=state,
                index=index,
                locked=locked,
                recommendation=recommendation,
            )
            if problem is not None:
                issues.append(problem)
                continue
            _candidate, problem = _check_new_activity(
                change.activity_key,
                label=label,
                state=state,
                index=index,
                candidates_by_key=candidates_by_key,
                forbidden=forbidden,
                recommendation=recommendation,
            )
            if problem is not None:
                issues.append(problem)
                continue
            current[current.index(change.from_activity_key or "")] = change.activity_key or ""
        else:  # reorder
            _activity, problem = _check_existing_activity(
                change.activity_key,
                label=label,
                group_key=group_key,
                state=state,
                index=index,
                locked=locked,
                recommendation=recommendation,
            )
            if problem is not None:
                issues.append(problem)
                continue
            if change.position is None or change.position < 1 or change.position > len(current):
                issues.append(
                    _issue(
                        ISSUE_CONSTRAINT,
                        f"{label}的目标位置 {change.position} 超出该组活动范围",
                        recommendation=recommendation,
                    )
                )
                continue
            old_index = current.index(change.activity_key or "")
            if old_index == change.position - 1:
                issues.append(
                    _issue(
                        ISSUE_CONSTRAINT,
                        f"{label}调序后顺序没有变化",
                        recommendation=recommendation,
                    )
                )
                continue
            current.pop(old_index)
            current.insert(change.position - 1, change.activity_key or "")

        validated.append(
            OptimizeChange(
                op=change.op,
                group_key=group_key,
                activity_key=change.activity_key,
                from_activity_key=change.from_activity_key,
                position=change.position,
                reason=change.reason[:300],
            )
        )

    if issues:
        return [], issues

    total_activities = sum(len(keys) for keys in state.values())
    if total_activities > constraints.max_activities:
        issues.append(
            _issue(
                ISSUE_CONSTRAINT,
                f"调整后活动数为 {total_activities}，超过上限 {constraints.max_activities}",
            )
        )
    empty_groups = [group_key for group_key, keys in state.items() if not keys]
    if empty_groups:
        issues.append(
            _issue(
                ISSUE_CONSTRAINT,
                f"调整后任务组 {'、'.join(empty_groups)} 没有任何活动",
            )
        )
    total_minutes = 0
    for keys in state.values():
        for activity_key in keys:
            activity = index.activities.get(activity_key)
            if activity is not None:
                total_minutes += activity.estimate_minutes
            else:
                total_minutes += candidates_by_key[activity_key].estimate_minutes
    if total_minutes > constraints.max_minutes:
        issues.append(
            _issue(
                ISSUE_CONSTRAINT,
                f"调整后预计 {total_minutes} 分钟，超过本轮上限 {constraints.max_minutes} 分钟",
            )
        )
    if issues:
        return [], issues
    return validated, []


def optimize_plan_with_model(
    *,
    service: ModelService,
    intent: RevisionIntent,
    bundle: RecommendationBundle,
    candidates: Sequence[OptimizeCandidate],
    constraints: OptimizeConstraints | None = None,
    prompt_version: str = PROMPT_VERSION,
) -> OptimizeOutcome:
    """按教师意图提出调整建议；非法提案整份拒绝，模型不可用时明确降级。

    返回的 ``changes`` 只是建议，调用方仍需走预览/确认或完整审核后再改动方案。
    """

    constraints = constraints or OptimizeConstraints()
    if not bundle.recommendations:
        return OptimizeOutcome(
            kind="unsatisfiable",
            reason="当前方案没有可调整的任务组",
            limitations=["方案为空，无法按教师意图调整"],
        )

    index = _index_plan(bundle)
    locked = _locked_aliases(index, constraints)
    request = ModelTaskRequest(
        task_key=TASK_KEY,
        prompt_version=prompt_version,
        namespace=PROMPT_NAMESPACE,
        input_payload=_build_payload(intent, index, candidates, constraints, locked),
        allow_external=True,
    )
    try:
        result = service.generate_structured(request, OptimizeOut)
    except Exception:  # 模型故障不得让调用方拿到半个提案
        return OptimizeOutcome(
            kind="unavailable",
            limitations=["教师意图优化调用异常，保留当前方案"],
        )
    if not result.succeeded or result.value is None:
        code = (result.error or {}).get("code", "MODEL_BAD_RESPONSE")
        return OptimizeOutcome(
            kind="unavailable",
            reason=code,
            limitations=[f"教师意图优化未完成（{code}），保留当前方案"],
        )

    value = result.value
    if value.kind == "unsatisfiable":
        return OptimizeOutcome(
            kind="unsatisfiable",
            reason=value.reason,
            limitations=[item[:200] for item in value.limitations][:6],
            model_used=True,
        )

    changes, issues = _validate_changes(
        value.changes, index=index, candidates=candidates, constraints=constraints
    )
    if issues:
        return OptimizeOutcome(
            kind="rejected",
            issues=issues,
            reason=value.reason,
            limitations=["模型调整建议未通过程序校验，已整份拒绝并保留当前方案"],
            model_used=True,
        )
    return OptimizeOutcome(
        kind="proposal",
        changes=changes,
        reason=value.reason,
        model_used=True,
    )
