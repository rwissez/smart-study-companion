"""S4 关系候选的程序核对（人和智能体提交的关系都要过这一关）。

检查项：知识点存在、无重复、无环（has_property/derives_from）、
evidence 子串确实出现在引用页的文字中。不通过的候选直接丢弃并给出原因。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from study_companion.knowledge.models import KnowledgeBase, Relation

ACYCLIC = {"has_property", "derives_from"}


def _flat(s: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s))


@dataclass
class Rejection:
    relation_id: str
    code: str
    message: str


def _has_path(edges: dict[str, set[str]], start: str, goal: str) -> bool:
    stack, seen = [start], set()
    while stack:
        cur = stack.pop()
        if cur == goal:
            return True
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(edges.get(cur, ()))
    return False


def validate_candidates(
    kb: KnowledgeBase,
    candidates: list[Relation],
    page_text: dict[int, str],
) -> tuple[list[Relation], list[Rejection]]:
    """返回 (通过, 拒绝)。page_text: 文件页 -> 该页文字。"""
    ids = {p.kp_id for p in kb.points}
    accepted: list[Relation] = []
    rejected: list[Rejection] = []
    existing = list(kb.relations)
    keys = {(r.type, tuple(sorted(r.source_ids)), r.target_id) for r in existing}
    edges: dict[str, set[str]] = {}
    for r in existing:
        if r.type in ACYCLIC:
            for s in r.source_ids:
                edges.setdefault(s, set()).add(r.target_id)

    for r in candidates:
        missing = [x for x in [*r.source_ids, r.target_id] if x not in ids]
        if missing:
            rejected.append(Rejection(r.relation_id, "KP_NOT_FOUND", f"知识点不存在：{missing}"))
            continue
        key = (r.type, tuple(sorted(r.source_ids)), r.target_id)
        if key in keys:
            rejected.append(Rejection(r.relation_id, "DUPLICATE", "同类型同端点的关系已存在"))
            continue
        if r.evidence:
            if r.evidence_file_page is None or r.evidence_file_page not in page_text:
                rejected.append(Rejection(r.relation_id, "EVIDENCE_PAGE_MISSING", "evidence 未指定有效页"))
                continue
            if _flat(r.evidence) not in _flat(page_text[r.evidence_file_page]):
                rejected.append(Rejection(r.relation_id, "EVIDENCE_NOT_IN_TEXT", "evidence 不是该页原文的子串"))
                continue
        elif r.proposer != "human":
            rejected.append(Rejection(r.relation_id, "EVIDENCE_REQUIRED", "非人工提议的关系必须带原文 evidence"))
            continue
        if r.type in ACYCLIC and any(_has_path(edges, r.target_id, s) for s in r.source_ids):
            rejected.append(Rejection(r.relation_id, "CYCLE", "该关系会使图出现环"))
            continue
        accepted.append(r)
        keys.add(key)
        if r.type in ACYCLIC:
            for s in r.source_ids:
                edges.setdefault(s, set()).add(r.target_id)
    return accepted, rejected
