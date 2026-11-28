"""C 的整份学习方案独立模型复核（04 §5.2 review、§8 完整审核）。

规则审核（``planner.review_plan_rules``）覆盖确定性可证的部分；本模块把整份
``RecommendationBundle`` 交给独立模型，按证据、目标、顺序、负荷、答案保护、
材料历史六类再复核一次。模型只产出结构化问题，不改方案、不改规则审核结论；
不可用、超时或输出不合法时返回空结果并记录限制，规则审核照常生效。

提示词文件为 ``prompts/c/review_plan.v1.md``，任务键取 ``review_plan.v1``，
命名空间为 ``c``，这样 ``model_service.load_prompt`` 才能按目录 + 文件名取到。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field

from study_companion.contracts import (
    Activity,
    EvidenceRef,
    Recommendation,
    RecommendationBundle,
    ReviewCheck,
    ValidationIssue,
)
from study_companion.model_service import ModelService, ModelTaskRequest

TASK_KEY = "review_plan.v1"
PROMPT_VERSION = "1.0"
PROMPT_NAMESPACE = "c"

#: 04 §5.2 六类方案检查；程序下发后模型必须一一覆盖。
PLAN_CHECK_IDS: tuple[str, ...] = (
    "evidence",
    "target",
    "sequence",
    "load",
    "answer_guard",
    "material_history",
)

PlanCheckId = Literal["evidence", "target", "sequence", "load", "answer_guard", "material_history"]

#: 方案级复核可用的 ReviewIssue code 子集（04 实施附录）。
PlanIssueCode = Literal[
    "EVIDENCE_MISMATCH",
    "TARGET_MISMATCH",
    "SEQUENCE_INVALID",
    "LOAD_WARNING",
    "ANSWER_PROTECTION",
    "MATERIAL_INVALID",
    "REPEAT_INVALID",
    "UNCERTAIN",
]

_SEVERITY_RANK = {"info": 0, "warning": 1, "error": 2}

#: 程序重新裁定严重性的上限；模型只能给出更轻的级别，不能把安全问题降成 info。
_SEVERITY_CAP = {
    "ANSWER_PROTECTION": "error",
    "MATERIAL_INVALID": "error",
    "EVIDENCE_MISMATCH": "warning",
    "TARGET_MISMATCH": "warning",
    "SEQUENCE_INVALID": "warning",
    "REPEAT_INVALID": "warning",
    "LOAD_WARNING": "info",
    "UNCERTAIN": "info",
}


class PlanCheckOut(BaseModel):
    """模型返回的单条检查；``evidence_keys`` 只能引用输入里下发的 E 编号。"""

    model_config = ConfigDict(extra="forbid")

    check_id: PlanCheckId
    result: Literal["pass", "fail", "uncertain"]
    reason: str = Field(min_length=1, max_length=300)
    evidence_keys: list[str] = Field(default_factory=list, max_length=8)


class PlanIssueOut(BaseModel):
    """模型返回的单条问题；对象只能引用输入里下发的 G/A 编号。"""

    model_config = ConfigDict(extra="forbid")

    code: PlanIssueCode
    severity: Literal["info", "warning", "error"] = "warning"
    message: str = Field(min_length=1, max_length=300)
    group_key: str | None = Field(default=None, max_length=40)
    activity_key: str | None = Field(default=None, max_length=40)
    evidence_keys: list[str] = Field(default_factory=list, max_length=8)


class PlanReviewOut(BaseModel):
    """整份复核输出；检查项覆盖情况由程序核对，不靠模型自觉。"""

    model_config = ConfigDict(extra="forbid")

    checks: list[PlanCheckOut] = Field(default_factory=list, max_length=6)
    issues: list[PlanIssueOut] = Field(default_factory=list, max_length=20)


@dataclass
class PlanReviewOutcome:
    """模型复核结果：空列表表示没有采用模型结论。"""

    checks: list[ReviewCheck] = field(default_factory=list)
    issues: list[ValidationIssue] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    model_used: bool = False

    def blocking_issues(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "error"]


@dataclass
class _PlanIndex:
    groups: dict[str, Recommendation] = field(default_factory=dict)
    activities: dict[str, Activity] = field(default_factory=dict)
    activity_group: dict[str, str] = field(default_factory=dict)
    group_activities: dict[str, list[str]] = field(default_factory=dict)
    evidence_refs: dict[str, EvidenceRef] = field(default_factory=dict)
    group_payload: list[dict[str, Any]] = field(default_factory=list)
    activity_payload: list[dict[str, Any]] = field(default_factory=list)
    evidence_payload: list[dict[str, Any]] = field(default_factory=list)


def _index_plan(bundle: RecommendationBundle) -> _PlanIndex:
    """把方案换成模型可引用的别名（G/A/E），同时保留程序侧回查表。"""

    index = _PlanIndex()
    evidence_key_of: dict[str, str] = {}

    def add_evidence(refs: list[EvidenceRef]) -> list[str]:
        keys: list[str] = []
        for ref in refs:
            ref_key = ref.key()
            key = evidence_key_of.get(ref_key)
            if key is None:
                key = f"E{len(index.evidence_refs) + 1}"
                evidence_key_of[ref_key] = key
                index.evidence_refs[key] = ref
            if key not in keys:
                keys.append(key)
        return keys

    activity_counter = 0
    for group_number, rec in enumerate(bundle.recommendations, start=1):
        group_key = f"G{group_number}"
        index.groups[group_key] = rec
        index.group_activities[group_key] = []
        activity_keys: list[str] = []
        for activity in rec.activities:
            activity_counter += 1
            activity_key = f"A{activity_counter}"
            index.activities[activity_key] = activity
            index.activity_group[activity_key] = group_key
            index.group_activities[group_key].append(activity_key)
            activity_keys.append(activity_key)
            index.activity_payload.append(
                {
                    "activity_key": activity_key,
                    "group_key": group_key,
                    "order": activity.order,
                    "activity_kind": activity.activity_kind,
                    "material_id": activity.material_id,
                    "title": activity.title[:80],
                    "purpose": activity.purpose[:200],
                    "expected_outcome": activity.expected_outcome[:200],
                    "estimate_minutes": activity.estimate_minutes,
                    "knowledge_ids": activity.knowledge_ids[:4],
                    "state": activity.state,
                    "exposure_mode": activity.exposure_mode,
                    "repeat_reason": activity.repeat_reason,
                    "hint_level_count": len(activity.hint_levels),
                    "hint_levels": [hint[:300] for hint in activity.hint_levels][:3],
                    "dependencies": activity.dependencies[:4],
                    "evidence_keys": add_evidence(activity.evidence_refs),
                }
            )
        index.group_payload.append(
            {
                "group_key": group_key,
                "goal": rec.goal[:200],
                "reason": rec.reason[:300],
                "primary_strategy": rec.strategy_types[0] if rec.strategy_types else None,
                "kp_ids": rec.kp_ids[:4],
                "finding_ids": rec.finding_ids[:8],
                "activity_keys": activity_keys,
                "evidence_keys": add_evidence(rec.evidence_refs),
                "expected_minutes": sum(item.estimate_minutes for item in rec.activities),
                "adjustment_conditions": [
                    item.observable_condition[:120] for item in rec.adjustment_conditions
                ][:4],
            }
        )

    index.evidence_payload = [
        {"key": key, **ref.model_dump(mode="json", exclude_none=True)}
        for key, ref in index.evidence_refs.items()
    ]
    return index


def _build_payload(
    bundle: RecommendationBundle,
    index: _PlanIndex,
    protected_texts: Sequence[str] | None,
    history_material_ids: Sequence[str] | None,
    rule_checks: Sequence[ReviewCheck] | None,
    round_minutes: int,
    max_activities: int,
) -> dict[str, Any]:
    return {
        "plan": {
            "plan_version": bundle.plan_version,
            "catalog_version": bundle.catalog_version,
            "analysis_version": bundle.analysis_version,
            "knowledge_version": bundle.knowledge_version,
            "limitations": bundle.limitations[:8],
            "groups": index.group_payload,
            "activities": index.activity_payload,
            "evidence": index.evidence_payload,
        },
        "limits": {
            "round_minutes": round_minutes,
            "max_activities": max_activities,
        },
        "protected_material_contents": [
            text[:300] for text in (protected_texts or []) if text
        ][:12],
        "history": {
            "recent_material_ids": [str(item)[:40] for item in (history_material_ids or [])][:20]
        },
        "rule_checks": [
            {"code": check.code, "passed": check.passed, "reason": check.reason[:200]}
            for check in (rule_checks or [])
        ][:8],
        "required_checks": [
            {"check_id": check_id, "object_key": "plan"} for check_id in PLAN_CHECK_IDS
        ],
    }


def _resolve_evidence(
    keys: Sequence[str], catalog: dict[str, EvidenceRef]
) -> tuple[list[EvidenceRef], int]:
    refs: list[EvidenceRef] = []
    missing = 0
    for key in keys:
        ref = catalog.get(key)
        if ref is None:
            missing += 1
            continue
        if ref.key() not in {item.key() for item in refs}:
            refs.append(ref)
    return refs, missing


def _capped_severity(code: str, proposed: str) -> Literal["info", "warning", "error"]:
    cap = _SEVERITY_CAP.get(code, "warning")
    if _SEVERITY_RANK[proposed] <= _SEVERITY_RANK[cap]:
        return proposed  # type: ignore[return-value]
    return cap  # type: ignore[return-value]


def _convert(
    value: PlanReviewOut,
    index: _PlanIndex,
    limitations: list[str],
) -> tuple[list[ReviewCheck], list[ValidationIssue]]:
    checks: list[ReviewCheck] = []
    issues: list[ValidationIssue] = []

    returned = [item.check_id for item in value.checks]
    if sorted(returned) != sorted(PLAN_CHECK_IDS):
        limitations.append(
            "整份方案独立模型复核未覆盖全部六类检查（缺失或重复），本轮不采用模型审核"
        )
        return checks, issues

    unknown_evidence = 0
    for item in value.checks:
        refs, missing = _resolve_evidence(item.evidence_keys, index.evidence_refs)
        unknown_evidence += missing
        reason = item.reason[:300]
        if missing:
            reason = ("复核依据不在输入证据集合内，结论不可采信：" + reason)[:300]
        checks.append(
            ReviewCheck(
                code=item.check_id,
                passed=item.result == "pass" and not missing,
                reason=reason,
                evidence_refs=refs,
            )
        )

    dropped = 0
    for item in value.issues:
        if item.evidence_keys:
            refs, missing = _resolve_evidence(item.evidence_keys, index.evidence_refs)
            if missing:
                dropped += 1
                continue
        else:
            refs = []
        recommendation = index.groups.get(item.group_key) if item.group_key else None
        activity = index.activities.get(item.activity_key) if item.activity_key else None
        if item.group_key and recommendation is None:
            dropped += 1
            continue
        if item.activity_key and activity is None:
            dropped += 1
            continue
        if activity is not None:
            owner_group = index.activity_group[item.activity_key or ""]
            if item.group_key and owner_group != item.group_key:
                dropped += 1
                continue
            if recommendation is None:
                recommendation = index.groups[owner_group]
        issues.append(
            ValidationIssue(
                code=item.code,
                message=item.message[:300],
                severity=_capped_severity(item.code, item.severity),
                recommendation_id=(
                    recommendation.recommendation_id if recommendation is not None else None
                ),
                activity_id=(activity.activity_id or None) if activity is not None else None,
                evidence_refs=refs,
            )
        )

    if dropped:
        limitations.append(
            f"模型有 {dropped} 条问题引用了当前方案之外的对象或不存在的依据，已丢弃"
        )
    if unknown_evidence:
        limitations.append(
            f"模型引用了 {unknown_evidence} 个不在输入证据集合内的依据，相关结论不采信"
        )
    return checks, issues


def review_plan_with_model(
    *,
    service: ModelService,
    bundle: RecommendationBundle,
    protected_texts: Sequence[str] | None = None,
    history_material_ids: Sequence[str] | None = None,
    rule_checks: Sequence[ReviewCheck] | None = None,
    round_minutes: int = 20,
    max_activities: int = 8,
    prompt_version: str = PROMPT_VERSION,
) -> PlanReviewOutcome:
    """对整份方案做独立模型复核，返回结构化问题与限制说明。

    该函数不修改 ``bundle``、不改变规则审核状态；调用方自行决定是否采纳
    ``outcome.issues`` 或把 ``outcome.checks`` 合并进审核记录。
    """

    outcome = PlanReviewOutcome()
    if not bundle.recommendations:
        outcome.limitations.append("方案没有可复核的任务组，跳过整份方案模型复核")
        return outcome

    index = _index_plan(bundle)
    request = ModelTaskRequest(
        task_key=TASK_KEY,
        prompt_version=prompt_version,
        namespace=PROMPT_NAMESPACE,
        input_payload=_build_payload(
            bundle,
            index,
            protected_texts,
            history_material_ids,
            rule_checks,
            round_minutes,
            max_activities,
        ),
        allow_external=True,
    )
    try:
        result = service.generate_structured(request, PlanReviewOut)
    except Exception:  # 模型故障不得阻断规则审核
        outcome.limitations.append("整份方案独立模型复核调用异常，沿用规则审核结果")
        return outcome
    if not result.succeeded or result.value is None:
        code = (result.error or {}).get("code", "MODEL_BAD_RESPONSE")
        outcome.limitations.append(
            f"整份方案独立模型复核未完成（{code}），沿用规则审核结果"
        )
        return outcome

    outcome.model_used = True
    outcome.checks, outcome.issues = _convert(result.value, index, outcome.limitations)
    return outcome
