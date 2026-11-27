"""班级共性与重点关注学生（03 §12）。

从成员学情结果聚合，不重算统计、不跨节点推断。三条规则都可解释：

1. 同一指标上"需加强"占已观测成员的比例达到阈值 → 共性问题；
2. 表现分布按 (指标, 表现, 证据状态) 计数，分母是全班名册；
3. 重点关注学生：有薄弱项、证据冲突、或完全没有充分证据。
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from study_companion.contracts import (
    AttentionStudent,
    DistributionBucket,
    EvidenceRef,
    Finding,
    StudentResult,
)

COMMON_SHARE = 0.5
MIN_OBSERVED_FOR_COMMON = 2
ATTENTION_LIMIT = 10


def _member_findings(members: list[StudentResult]) -> list[tuple[str, Finding]]:
    rows: list[tuple[str, Finding]] = []
    for member in members:
        for finding in member.findings:
            rows.append((member.student_id, finding))
    return rows


def build_common_findings(
    *, class_id: str, node_id: str, members: list[StudentResult], threshold: float = COMMON_SHARE
) -> tuple[list[Finding], list[str]]:
    findings, limitations = [], []
    by_indicator: dict[str, list[tuple[str, Finding]]] = defaultdict(list)
    for student_id, finding in _member_findings(members):
        by_indicator[finding.indicator_id].append((student_id, finding))

    for indicator_id, rows in sorted(by_indicator.items()):
        observed = [finding for _, finding in rows if finding.evidence_status != "insufficient"]
        if len(observed) < MIN_OBSERVED_FOR_COMMON:
            limitations.append(f"指标{indicator_id}已观测成员不足{MIN_OBSERVED_FOR_COMMON}人，不判定班级共性")
            continue
        weak = [finding for finding in observed if finding.performance == "needs_practice"]
        share = len(weak) / len(observed)
        if share < threshold:
            continue
        refs: list[EvidenceRef] = []
        for finding in weak:
            refs.extend(finding.evidence_refs)
        findings.append(
            Finding(
                finding_id=f"cf-{class_id}-{node_id}-{indicator_id}",
                indicator_id=indicator_id,
                description=f"班级共性问题：{len(weak)}/{len(observed)}名已观测学生在该指标上需要加强",
                evidence_status="sufficient",
                performance="needs_practice",
                evidence_refs=refs[:20],
                limitations=[
                    f"共性问题判定阈值{threshold}，分母为已观测成员{len(observed)}人，不含无有效记录学生"
                ],
                generation_method="rule",
            )
        )
    return findings, limitations


def build_distribution(
    *, members: list[StudentResult], roster_count: int
) -> list[DistributionBucket]:
    buckets: Counter[tuple[str, str, str]] = Counter()
    for _, finding in _member_findings(members):
        buckets[(finding.indicator_id, finding.performance, finding.evidence_status)] += 1
    return [
        DistributionBucket(
            indicator_id=indicator_id,
            performance=performance,  # type: ignore[arg-type]
            evidence_status=evidence,  # type: ignore[arg-type]
            count=count,
            roster_count=roster_count,
        )
        for (indicator_id, performance, evidence), count in sorted(buckets.items())
    ]


def build_attention_students(
    *, members: list[StudentResult], limit: int = ATTENTION_LIMIT
) -> list[AttentionStudent]:
    attention: list[AttentionStudent] = []
    for member in members:
        if not member.findings:
            continue
        weak = [item for item in member.findings if item.performance == "needs_practice"]
        conflicting = [item for item in member.findings if item.evidence_status == "conflicting"]
        sufficient = [item for item in member.findings if item.evidence_status == "sufficient"]
        reasons: list[str] = []
        refs: list[EvidenceRef] = []
        ids: list[str] = []
        if weak:
            reasons.append(f"{len(weak)}项指标需要加强：" + "、".join(item.indicator_id for item in weak[:3]))
            refs.extend(ref for item in weak for ref in item.evidence_refs)
            ids.extend(item.finding_id for item in weak)
        if conflicting:
            reasons.append(f"{len(conflicting)}项指标记录冲突，需要核对")
            refs.extend(ref for item in conflicting for ref in item.evidence_refs)
            ids.extend(item.finding_id for item in conflicting)
        if not sufficient and not reasons:
            reasons.append("没有充分证据判定表现，需要补充观察")
            ids.extend(item.finding_id for item in member.findings)
        if not reasons:
            continue
        attention.append(
            AttentionStudent(
                student_id=member.student_id,
                finding_ids=ids[:10],
                evidence_refs=refs[:20],
                reason="；".join(reasons),
                limitations=["按规则排序，教师可结合课堂观察复核"],
            )
        )
    attention.sort(key=lambda item: (len(item.finding_ids), item.student_id), reverse=True)
    return attention[:limit]


def enrich_class(
    *,
    class_id: str,
    node_id: str,
    roster_count: int,
    members: list[StudentResult],
    threshold: float = COMMON_SHARE,
) -> dict[str, Any]:
    common, limitations = build_common_findings(
        class_id=class_id, node_id=node_id, members=members, threshold=threshold
    )
    return {
        "common_findings": common,
        "performance_distribution": build_distribution(members=members, roster_count=roster_count),
        "attention_students": build_attention_students(members=members),
        "limitations": limitations,
    }
