"""S3 关键词查询：关键词 -> 知识点 -> 单元(章) -> 小节 -> 页码。"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from study_companion.knowledge.models import KnowledgeBase, KnowledgePoint, Relation


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s)).lower()


@dataclass(frozen=True)
class Hit:
    kp_id: str
    name: str
    match: str  # exact / alias / substring
    unit: str
    section: str
    source_file: str
    file_page: int
    printed_page: int | None
    verification_status: str


def _hit(kp: KnowledgePoint, match: str) -> Hit:
    loc = kp.definition_locator
    return Hit(kp.kp_id, kp.name, match, loc.chapter, loc.section, loc.source_file,
               loc.file_page, loc.printed_page, loc.verification_status)


def locate(kb: KnowledgeBase, keyword: str) -> list[Hit]:
    """精确名 -> 别名 -> 子串；多个命中全部返回，不猜。"""
    q = _norm(keyword)
    if not q:
        return []
    exact = [p for p in kb.points if _norm(p.name) == q]
    if exact:
        return [_hit(p, "exact") for p in exact]
    alias = [p for p in kb.points if any(_norm(a) == q for a in p.aliases)]
    if alias:
        return [_hit(p, "alias") for p in alias]
    sub = [p for p in kb.points if q in _norm(p.name) or any(q in _norm(a) for a in p.aliases)]
    return [_hit(p, "substring") for p in sub]


def neighbors(kb: KnowledgeBase, kp_id: str, types: set[str] | None = None) -> list[Relation]:
    return [
        r for r in kb.relations
        if (kp_id in r.source_ids or r.target_id == kp_id)
        and (types is None or r.type in types)
        and r.verification_status != "rejected"
    ]


def upstream_closure(kb: KnowledgeBase, kp_id: str, only_verified: bool = False) -> list[str]:
    """沿"来源"方向追溯全部上游知识点（用于补救建议）。"""
    seen: list[str] = []
    stack = [kp_id]
    while stack:
        cur = stack.pop()
        for r in kb.relations:
            if r.target_id != cur or r.verification_status == "rejected":
                continue
            if only_verified and r.verification_status != "verified":
                continue
            for s in r.source_ids:
                if s not in seen and s != kp_id:
                    seen.append(s)
                    stack.append(s)
    return seen
