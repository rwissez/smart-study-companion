"""S4 关系候选的程序核对（人和智能体提交的关系都要过这一关）。

检查项：知识点存在、无重复、无环（has_property/derives_from）、
evidence 子串确实出现在引用页的文字中。不通过的候选直接丢弃并给出原因。
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

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


PageText = Mapping[Any, str]


def _page_text_for(relation: Relation, page_text: PageText) -> str | None:
    """Resolve evidence using a source-qualified map, while keeping old maps valid."""
    page = relation.evidence_file_page
    if page is None:
        return None
    if relation.source_file is not None:
        source_page = page_text.get((relation.source_file, page))
        if source_page is not None:
            return source_page
        source_pages = page_text.get(relation.source_file)
        if isinstance(source_pages, Mapping):
            return source_pages.get(page)
        return None
    return page_text.get(page)


def validate_candidates(
    kb: KnowledgeBase,
    candidates: list[Relation],
    page_text: PageText,
) -> tuple[list[Relation], list[Rejection]]:
    """Return accepted and rejected relations.

    ``page_text`` accepts the legacy ``{file_page: text}`` shape. New callers may
    use ``{(source_file, file_page): text}`` or ``{source_file: {file_page: text}}``
    so evidence cannot accidentally be checked against another source file.
    """
    ids = {p.kp_id for p in kb.points}
    accepted: list[Relation] = []
    rejected: list[Rejection] = []
    existing = list(kb.relations)
    existing_ids = {r.relation_id for r in existing}
    seen_ids: set[str] = set()
    keys = {(r.type, tuple(sorted(r.source_ids)), r.target_id) for r in existing}
    edges: dict[str, set[str]] = {}
    for r in existing:
        if r.type in ACYCLIC:
            for s in r.source_ids:
                edges.setdefault(s, set()).add(r.target_id)

    for r in candidates:
        if r.relation_id in existing_ids or r.relation_id in seen_ids:
            rejected.append(Rejection(r.relation_id, "RELATION_ID_DUPLICATE", "关系 ID 必须唯一"))
            continue
        seen_ids.add(r.relation_id)
        missing = [x for x in [*r.source_ids, r.target_id] if x not in ids]
        if missing:
            rejected.append(Rejection(r.relation_id, "KP_NOT_FOUND", f"知识点不存在：{missing}"))
            continue
        key = (r.type, tuple(sorted(r.source_ids)), r.target_id)
        if key in keys:
            rejected.append(Rejection(r.relation_id, "DUPLICATE", "同类型同端点的关系已存在"))
            continue
        if r.source_file is not None and r.evidence_file_page is None:
            rejected.append(Rejection(r.relation_id, "SOURCE_PAGE_REQUIRED", "指定来源文件时必须指定 file page"))
            continue
        if r.evidence:
            text = _page_text_for(r, page_text)
            if text is None:
                rejected.append(Rejection(r.relation_id, "EVIDENCE_PAGE_MISSING", "evidence 未指定有效来源页"))
                continue
            if _flat(r.evidence) not in _flat(text):
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
