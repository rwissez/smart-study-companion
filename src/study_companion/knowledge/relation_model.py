"""A 的知识关系模型协作（02 §7）。

程序负责候选、分批、结构校验与环隔离；模型只提出关系，独立复核只判断是否成立。
没有模型或复核不通过时，不生成任何新关系，绝不猜测。

大批量知识点（教材点会到几十个）不能一次塞给模型：候选按点度上限和总数上限
截断，再按固定上限分批提案与分批复核；每批只带本批用到的知识点和候选点对。
这样单次提示体积可控，单批超时也不会拖垮全部关系。
"""

from __future__ import annotations

import itertools
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

from study_companion.model_service import ModelService, ModelTaskRequest

RELATION_TYPES = {"prerequisite", "has_property", "derives_from", "composed_of", "special_case"}

#: 每个知识点最多进入多少个候选点对。
MAX_CANDIDATES_PER_POINT = 8
#: 每个知识点至少先保留多少个自己的高分候选，避免弱主题被强主题挤掉。
SEED_CANDIDATES_PER_POINT = 3
#: 一轮推断最多保留多少个候选点对（34 个点时约 72 对，而不是 190 多对）。
MAX_CANDIDATES_TOTAL = 72
#: 单批提案的候选点对上限；配合每批知识点上限压缩提示体积。
MAX_CANDIDATES_PER_BATCH = 12
#: 单批知识点上限，避免一个候选把大量无关定义带进提示。
MAX_POINTS_PER_BATCH = 18
#: 单批复核的关系条数。
MAX_REVIEWS_PER_BATCH = 12
#: 最多处理多少批；与候选总数上限配合，防止批次无限增长。
MAX_BATCHES = 6
#: 连续多少批模型调用失败后提前停止，保留已经拿到的结果。
MAX_CONSECUTIVE_MODEL_FAILURES = 2
#: 关系推断的总时间预算（秒）。提案和复核共用，超时只丢弃未完成部分。
RELATION_BUDGET_SECONDS = 180.0


class RelationProposal(BaseModel):
    type: str = Field(
        description=(
            "关系类型，只能是 prerequisite、has_property、derives_from、"
            "composed_of、special_case"
        )
    )
    source_ids: list[str] = Field(
        min_length=1,
        max_length=4,
        description=(
            "来源知识点编号。除 composed_of 恰好 1 个外，其他类型只能有 1 个；"
            "多写来源会被程序丢弃"
        ),
    )
    target_id: str = Field(description="目标知识点编号，必须出现在输入 points 中")
    basis: Literal["standard", "team_inference"] = "team_inference"
    reason: str = Field(default="", description="推理依据，简洁说明为什么成立")
    prerequisite_context: str | None = Field(
        default=None, description="仅 prerequisite 使用：说明先学前置的适用场景"
    )


class RelationProposals(BaseModel):
    relations: list[RelationProposal] = Field(default_factory=list, max_length=24)


class RelationDecision(BaseModel):
    index: int = Field(ge=0)
    decision: Literal["pass", "reject"]
    reason: str = ""


class RelationReview(BaseModel):
    decisions: list[RelationDecision] = Field(default_factory=list, max_length=24)


@dataclass
class RelationCandidate:
    left: str
    right: str
    shared_indicators: list[str] = field(default_factory=list)
    name_overlap: float = 0.0
    same_topic: bool = False


@dataclass
class CandidateBatch:
    candidates: list[RelationCandidate]
    point_ids: set[str]
    pairs: set[tuple[str, str]]


def _tokens(text: str) -> set[str]:
    cleaned = re.sub(r"[\s，。；：、（）()【】\[\]]+", "", text or "")
    return {cleaned[i : i + 2] for i in range(max(0, len(cleaned) - 1))}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _pair_key(left: str, right: str) -> tuple[str, str]:
    return (left, right) if left <= right else (right, left)


def collect_candidates(points: list[dict[str, Any]]) -> list[RelationCandidate]:
    """按共享指标、主题与名称字面重合排序，并限制每点与总数。

    每个知识点最多进入 ``MAX_CANDIDATES_PER_POINT`` 个候选点对，总数最多
    ``MAX_CANDIDATES_TOTAL`` 对。度数上限是"任一端点已满就不再收"，不是
    "两端都满才停"——后者会让热点知识点把候选撑到 O(n²)。

    选择顺序是"每个点先保住自己的高分候选，再按全局分数补满"：教材点集中在
    某一主题时，主题外的少量课程指标点不会因为分数低而全部拿不到候选。
    """

    by_id = {point["kp_id"]: point for point in points if point.get("kp_id")}
    scored: list[tuple[float, str, str, list[str], float, bool]] = []
    for left_id, right_id in itertools.combinations(by_id, 2):
        left, right = by_id[left_id], by_id[right_id]
        shared = sorted(set(left.get("indicator_ids") or []) & set(right.get("indicator_ids") or []))
        same_topic = bool(set(left.get("topic_ids") or []) & set(right.get("topic_ids") or []))
        overlap = _jaccard(_tokens(left.get("name", "")), _tokens(right.get("name", "")))
        score = len(shared) * 2 + (1 if same_topic else 0) + overlap
        if score <= 0:
            continue
        scored.append((score, left_id, right_id, shared, overlap, same_topic))
    scored.sort(key=lambda item: (-item[0], item[1], item[2]))

    by_point: dict[str, list[tuple[float, str, str, list[str], float, bool]]] = {}
    for item in scored:
        by_point.setdefault(item[1], []).append(item)
        by_point.setdefault(item[2], []).append(item)

    ordered: list[tuple[float, str, str, list[str], float, bool]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for rank in range(SEED_CANDIDATES_PER_POINT):
        for point_id in sorted(by_point):
            items = by_point[point_id]
            if rank >= len(items):
                continue
            item = items[rank]
            pair = _pair_key(item[1], item[2])
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            ordered.append(item)
    for item in scored:
        pair = _pair_key(item[1], item[2])
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        ordered.append(item)

    degree: dict[str, int] = {}
    candidates: list[RelationCandidate] = []
    for score, left_id, right_id, shared, overlap, same_topic in ordered:
        if len(candidates) >= MAX_CANDIDATES_TOTAL:
            break
        if degree.get(left_id, 0) >= MAX_CANDIDATES_PER_POINT:
            continue
        if degree.get(right_id, 0) >= MAX_CANDIDATES_PER_POINT:
            continue
        degree[left_id] = degree.get(left_id, 0) + 1
        degree[right_id] = degree.get(right_id, 0) + 1
        candidates.append(
            RelationCandidate(
                left=left_id,
                right=right_id,
                shared_indicators=shared,
                name_overlap=round(overlap, 4),
                same_topic=same_topic,
            )
        )
    return candidates


def _batches(candidates: list[RelationCandidate]) -> list[CandidateBatch]:
    """按候选顺序切成体积有上限的批次；候选顺序已保证每个点先有覆盖。"""

    batches: list[CandidateBatch] = []
    current: list[RelationCandidate] = []
    point_ids: set[str] = set()
    pairs: set[tuple[str, str]] = set()
    for candidate in candidates:
        candidate_points = {candidate.left, candidate.right}
        if current and (
            len(current) >= MAX_CANDIDATES_PER_BATCH
            or len(point_ids | candidate_points) > MAX_POINTS_PER_BATCH
        ):
            batches.append(CandidateBatch(current, point_ids, pairs))
            current, point_ids, pairs = [], set(), set()
        current.append(candidate)
        point_ids |= candidate_points
        pairs.add(_pair_key(candidate.left, candidate.right))
    if current:
        batches.append(CandidateBatch(current, point_ids, pairs))
    return batches[:MAX_BATCHES]


def _validate(
    proposals: list[RelationProposal],
    point_ids: set[str],
    allowed_pairs: set[tuple[str, str]] | None = None,
) -> tuple[list[RelationProposal], list[str]]:
    accepted: list[RelationProposal] = []
    issues: list[str] = []
    signatures: set[tuple[str, tuple[str, ...], str]] = set()
    for proposal in proposals:
        sources = sorted({item for item in proposal.source_ids if item})
        if proposal.type not in RELATION_TYPES:
            issues.append(f"未知关系类型 {proposal.type}，已丢弃")
            continue
        if proposal.target_id not in point_ids or any(item not in point_ids for item in sources):
            issues.append("关系端点不在本批候选知识点中，已丢弃")
            continue
        if proposal.target_id in sources:
            issues.append("关系出现自环，已丢弃")
            continue
        if proposal.type == "composed_of":
            if len(sources) < 2:
                issues.append("composed_of 需要至少两个来源，已丢弃")
                continue
        else:
            if len(sources) != 1:
                issues.append(f"{proposal.type} 只能有一个来源，已丢弃")
                continue
            if allowed_pairs is not None and _pair_key(sources[0], proposal.target_id) not in allowed_pairs:
                issues.append("关系来自候选点对之外，已丢弃")
                continue
        if proposal.basis == "team_inference" and not proposal.reason.strip():
            issues.append("推断关系缺少理由，已丢弃")
            continue
        signature = (proposal.type, tuple(sources), proposal.target_id)
        if signature in signatures:
            continue
        signatures.add(signature)
        accepted.append(proposal.model_copy(update={"source_ids": sources}))
    return accepted, issues


def _dedupe(proposals: list[RelationProposal]) -> list[RelationProposal]:
    seen: set[tuple[str, tuple[str, ...], str]] = set()
    kept: list[RelationProposal] = []
    for proposal in proposals:
        signature = (proposal.type, tuple(proposal.source_ids), proposal.target_id)
        if signature in seen:
            continue
        seen.add(signature)
        kept.append(proposal)
    return kept


def _drop_prerequisite_cycles(proposals: list[RelationProposal]) -> tuple[list[RelationProposal], list[str]]:
    graph: dict[str, set[str]] = {}
    kept: list[RelationProposal] = []
    issues: list[str] = []
    for proposal in proposals:
        if proposal.type != "prerequisite":
            kept.append(proposal)
            continue
        source = proposal.source_ids[0]
        target = proposal.target_id

        def reachable(start: str, goal: str) -> bool:
            stack = [start]
            seen: set[str] = set()
            while stack:
                node = stack.pop()
                if node == goal:
                    return True
                if node in seen:
                    continue
                seen.add(node)
                stack.extend(graph.get(node, ()))
            return False

        if reachable(target, source):
            issues.append(f"前置关系 {source}→{target} 会形成环，已隔离")
            continue
        graph.setdefault(source, set()).add(target)
        kept.append(proposal)
    return kept, issues


def _model_point(point: dict[str, Any], *, definition_chars: int = 120) -> dict[str, Any]:
    return {
        "kp_id": point["kp_id"],
        "name": (point.get("name") or "")[:40],
        "definition": (point.get("definition") or "")[:definition_chars],
        "topic_ids": point.get("topic_ids") or [],
        "indicator_ids": point.get("indicator_ids") or [],
    }


def _proposal_payload(
    knowledge_version: str,
    index: int,
    total: int,
    batch: CandidateBatch,
    by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    return {
        "knowledge_version": knowledge_version,
        "batch": {"index": index + 1, "total": total, "candidate_count": len(batch.candidates)},
        "points": [_model_point(by_id[point_id]) for point_id in sorted(batch.point_ids) if point_id in by_id],
        "candidates": [
            {
                "left": item.left,
                "right": item.right,
                "shared_indicators": item.shared_indicators,
                "same_topic": item.same_topic,
                "name_overlap": item.name_overlap,
            }
            for item in batch.candidates
        ],
    }


def _review_payload(
    proposals: list[RelationProposal], by_id: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    point_ids = sorted({item for proposal in proposals for item in [*proposal.source_ids, proposal.target_id]})
    return {
        "points": [
            _model_point(by_id[point_id], definition_chars=120)
            for point_id in point_ids
            if point_id in by_id
        ],
        "relations": [
            {
                "type": proposal.type,
                "source_ids": proposal.source_ids,
                "target_id": proposal.target_id,
                "basis": proposal.basis,
                "reason": (proposal.reason or "")[:120],
                "prerequisite_context": (proposal.prerequisite_context or "")[:120],
            }
            for proposal in proposals
        ],
    }


def _limit_notes(issues: list[str], limit: int = 8) -> list[str]:
    """把重复的限制合并成可读的短列表，避免 8 条相同提示原样堆在报告里。"""

    if not issues:
        return []
    counts = Counter(issues)
    ordered = sorted(dict.fromkeys(issues), key=lambda message: (-counts[message], issues.index(message)))
    notes = [
        f"{message}（{counts[message]} 条）" if counts[message] > 1 else message
        for message in ordered[:limit]
    ]
    if len(ordered) > limit:
        notes.append(f"另有 {len(ordered) - limit} 类关系推断限制未展开")
    return notes


def _collect_proposals(
    service: ModelService,
    batches: list[CandidateBatch],
    by_id: dict[str, dict[str, Any]],
    knowledge_version: str,
    deadline: float,
) -> tuple[list[RelationProposal], int, list[str], int, int, list[str]]:
    """逐批提案；单批失败不丢弃已成功批次，连续失败才提前停止。"""

    accepted: list[RelationProposal] = []
    raw_count = 0
    completed = 0
    issues: list[str] = []
    failure_codes: list[str] = []
    consecutive = 0
    for index, batch in enumerate(batches):
        if time.monotonic() >= deadline:
            issues.append(f"关系推断达到 {RELATION_BUDGET_SECONDS:.0f} 秒时间预算，剩余 {len(batches) - index} 批未处理")
            break
        request = ModelTaskRequest(
            task_key="a.relations.v1",
            prompt_version="1.0",
            namespace="a",
            input_payload=_proposal_payload(knowledge_version, index, len(batches), batch, by_id),
            allow_external=True,
        )
        result = service.generate_structured(request, RelationProposals)
        if not result.succeeded or result.value is None:
            code = (result.error or {}).get("code", "MODEL_BAD_RESPONSE")
            failure_codes.append(str(code))
            consecutive += 1
            if consecutive >= MAX_CONSECUTIVE_MODEL_FAILURES:
                issues.append(f"连续 {consecutive} 批模型调用失败（{code}），提前停止本轮关系推断")
                break
            continue
        consecutive = 0
        completed += 1
        raw_count += len(result.value.relations)
        batch_accepted, batch_issues = _validate(result.value.relations, batch.point_ids, batch.pairs)
        accepted.extend(batch_accepted)
        issues.extend(batch_issues)
    if failure_codes:
        timeout_count = sum(1 for code in failure_codes if code == "MODEL_TIMEOUT")
        other_count = len(failure_codes) - timeout_count
        if timeout_count:
            issues.append(f"{timeout_count} 批关系推断超时（MODEL_TIMEOUT）")
        if other_count:
            codes = "、".join(sorted({code for code in failure_codes if code != "MODEL_TIMEOUT"}))
            issues.append(f"{other_count} 批关系推断失败（{codes}）")
    return accepted, raw_count, issues, completed, len(batches), failure_codes


def _review(
    service: ModelService,
    proposals: list[RelationProposal],
    by_id: dict[str, dict[str, Any]],
    deadline: float,
) -> tuple[list[RelationProposal], int, int, list[str]]:
    """分批独立复核；只有明确 pass 的关系保留，缺失决定按未通过处理。"""

    kept: list[RelationProposal] = []
    rejected = 0
    failed = 0
    issues: list[str] = []
    failure_codes: list[str] = []
    total = len(proposals)
    for start in range(0, total, MAX_REVIEWS_PER_BATCH):
        chunk = proposals[start : start + MAX_REVIEWS_PER_BATCH]
        if time.monotonic() >= deadline:
            issues.append(f"独立复核达到时间预算，剩余 {total - start} 条关系未复核，已丢弃")
            rejected += total - start
            break
        request = ModelTaskRequest(
            task_key="a.review_relation.v1",
            prompt_version="1.0",
            namespace="a",
            input_payload=_review_payload(chunk, by_id),
            allow_external=True,
        )
        result = service.generate_structured(request, RelationReview)
        if not result.succeeded or result.value is None:
            code = (result.error or {}).get("code", "MODEL_BAD_RESPONSE")
            failed += len(chunk)
            failure_codes.append(str(code))
            continue
        decisions = {
            item.index: item
            for item in result.value.decisions
            if 0 <= item.index < len(chunk)
        }
        for local_index, proposal in enumerate(chunk):
            decision = decisions.get(local_index)
            if decision is not None and decision.decision == "pass":
                kept.append(proposal)
            else:
                rejected += 1
    if failure_codes:
        codes = "、".join(sorted(set(failure_codes)))
        issues.append(f"{failed} 条关系因独立复核未完成（{codes}）被丢弃")
    return kept, rejected, failed, issues


def infer_relations(
    *,
    service: ModelService,
    points: list[dict[str, Any]],
    knowledge_version: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    """返回可发布的 relation payload 片段与限制说明；模型不可用返回空。"""

    if not service.configured or len(points) < 2:
        return [], ["模型未配置或知识点不足，未做关系推断"]
    by_id = {point["kp_id"]: point for point in points if point.get("kp_id")}
    if len(by_id) < 2:
        return [], ["有效知识点不足两个，未做关系推断"]

    candidates = collect_candidates(points)
    if not candidates:
        return [], [
            "没有可推断关系的候选点对：知识点之间既不共享课程指标或主题，名称也没有重合；"
            "本轮不新增关系，未猜测"
        ]
    batches = _batches(candidates)
    if not batches:
        return [], ["关系候选未能分批，未做关系推断"]

    started = time.monotonic()
    proposal_deadline = started + RELATION_BUDGET_SECONDS * 0.55
    review_deadline = started + RELATION_BUDGET_SECONDS
    accepted, raw_count, issues, completed, total_batches, _failure_codes = _collect_proposals(
        service, batches, by_id, knowledge_version, proposal_deadline
    )
    if not accepted:
        notes = _limit_notes(issues)
        if raw_count == 0:
            if completed == 0:
                notes.append("关系推断没有完成任何一批，本轮不新增关系")
            else:
                notes.append(f"模型看完 {completed} 批候选（{len(candidates)} 个点对）后没有提出关系，本轮不新增关系")
        else:
            notes.append(f"模型提出 {raw_count} 条关系，全部未通过结构校验，本轮不新增关系")
        return [], notes

    accepted = _dedupe(accepted)
    accepted, cycle_issues = _drop_prerequisite_cycles(accepted)
    issues.extend(cycle_issues)
    if not accepted:
        notes = _limit_notes(issues)
        notes.append("关系提案在环检测后为空，本轮不新增关系")
        return [], notes

    kept, rejected, review_failed, review_issues = _review(service, accepted, by_id, review_deadline)
    issues.extend(review_issues)
    if not kept:
        notes = _limit_notes(issues)
        notes.append(f"{len(accepted)} 条关系提案未通过独立复核，本轮不新增关系")
        return [], notes

    payload: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for index, proposal in enumerate(kept):
        source = by_id[proposal.source_ids[0]]
        target = by_id[proposal.target_id]
        relation_id = f"rel-{proposal.type}-{'-'.join(proposal.source_ids)}-{proposal.target_id}"[:120]
        if relation_id in used_ids:
            relation_id = f"{relation_id[:112]}-{index:03d}"
        used_ids.add(relation_id)
        payload.append(
            {
                "relation_id": relation_id,
                "type": proposal.type,
                "source_ids": proposal.source_ids,
                "target_id": proposal.target_id,
                "basis": proposal.basis,
                "note": (proposal.reason or "")[:300],
                "source_locators": list(source.get("source_locators") or [])[:2]
                + list(target.get("source_locators") or [])[:2],
                "review_status": "validated",
            }
        )

    if completed < total_batches or review_failed or rejected:
        issues.append(
            f"关系推断部分完成：{completed}/{total_batches} 批提案完成，"
            f"独立复核保留 {len(kept)} 条、拒绝 {rejected} 条、未完成 {review_failed} 条"
        )
    return payload, _limit_notes(issues)
