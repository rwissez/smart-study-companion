"""Read-only knowledge queries bound to a catalog and role."""

from __future__ import annotations

from typing import Any

from study_companion.contracts import Catalog, DomainError
from study_companion.knowledge.index import locate
from study_companion.knowledge.models import KnowledgeBase, KnowledgePoint


def _check(kb: KnowledgeBase, catalog: Catalog, kb_version: str | None, catalog_version: str, role: str) -> None:
    if role not in {"teacher", "student"}:
        raise DomainError("FORBIDDEN", "Unsupported knowledge query role")
    if catalog.version != catalog_version or (kb_version is not None and kb.kb_version != kb_version):
        raise DomainError("KB_CONTEXT_MISMATCH", "目录版本或知识库版本与会话绑定版本不一致")


def _visible(kb: KnowledgeBase, role: str) -> dict[str, KnowledgePoint]:
    return {p.kp_id: p for p in kb.points if p.verification_status != "rejected" and (
        role == "teacher" or (p.verification_status == "verified" and p.definition_locator.verification_status == "verified"))}


def locate_knowledge(kb: KnowledgeBase, catalog: Catalog, keyword: str, *, role: str, kb_version: str | None, catalog_version: str) -> list[dict[str, Any]]:
    _check(kb, catalog, kb_version, catalog_version, role)
    points = _visible(kb, role)
    filtered = kb.model_copy(update={"points": list(points.values())})
    topics = {t.topic_id for t in catalog.topics}
    indicators = {i.indicator_id for i in catalog.indicators}
    return [{**hit.__dict__, "kb_version": kb.kb_version,
             "verification_status": points[hit.kp_id].verification_status,
             "locator_verification_status": points[hit.kp_id].definition_locator.verification_status,
             "topic_ids": [x for x in points[hit.kp_id].topic_ids if x in topics],
             "indicator_ids": [x for x in points[hit.kp_id].indicator_ids if x in indicators]}
            for hit in locate(filtered, keyword)]


def get_knowledge_detail(kb: KnowledgeBase, catalog: Catalog, kp_id: str, *, role: str, kb_version: str | None, catalog_version: str) -> dict[str, Any]:
    _check(kb, catalog, kb_version, catalog_version, role)
    point = _visible(kb, role).get(kp_id)
    if point is None:
        raise DomainError("NOT_FOUND", "Knowledge point not found or not visible")
    topics = {t.topic_id for t in catalog.topics}
    indicators = {i.indicator_id for i in catalog.indicators}
    return {"kb_version": kb.kb_version, "kp_id": point.kp_id, "name": point.name,
            "aliases": point.aliases, "kind": point.kind, "snippet": point.snippet,
            "verification_status": point.verification_status,
            "locator": point.definition_locator.model_dump(mode="json"),
            "source_locators": [loc.model_dump(mode="json") for loc in point.source_locators
                                if loc.verification_status != "rejected" and (role == "teacher" or loc.verification_status == "verified")],
            "topic_ids": [x for x in point.topic_ids if x in topics],
            "indicator_ids": [x for x in point.indicator_ids if x in indicators],
            "annotations": [a.model_dump(mode="json") for a in kb.annotations
                            if kp_id in a.target_ids and a.verification_status != "rejected"
                            and (role == "teacher" or a.verification_status == "verified")]}


def get_knowledge_relations(kb: KnowledgeBase, catalog: Catalog, kp_id: str, *, role: str, kb_version: str | None, catalog_version: str, types: set[str] | None = None, direction: str = "neighbors", depth: int = 1) -> list[dict[str, Any]]:
    _check(kb, catalog, kb_version, catalog_version, role)
    points = _visible(kb, role)
    if kp_id not in points:
        raise DomainError("NOT_FOUND", "Knowledge point not found or not visible")
    if isinstance(depth, bool) or not isinstance(depth, int) or not 1 <= depth <= 5 or direction not in {"neighbors", "upstream", "downstream"}:
        raise DomainError("INVALID_ARGUMENT", "Invalid traversal direction or depth")
    if types is not None and not set(types) <= {"has_property", "derives_from", "composed_of", "special_case"}:
        raise DomainError("INVALID_ARGUMENT", "Unknown relation type")
    allowed = [r for r in kb.relations if r.verification_status != "rejected"
               and (role == "teacher" or r.verification_status == "verified")
               and all(x in points for x in [*r.source_ids, r.target_id])
               and (types is None or r.type in types)]
    frontier, visited, selected = {kp_id}, {kp_id}, []
    for _ in range(depth):
        edges = [r for r in allowed if (
            r.target_id in frontier if direction == "upstream" else
            any(s in frontier for s in r.source_ids) if direction == "downstream" else
            r.target_id in frontier or any(s in frontier for s in r.source_ids))]
        selected.extend(r for r in edges if r not in selected)
        following = ({s for r in edges for s in r.source_ids} if direction == "upstream" else
                     {r.target_id for r in edges} if direction == "downstream" else
                     {x for r in edges for x in [*r.source_ids, r.target_id]})
        frontier = following - visited
        visited |= frontier
        if not frontier:
            break
    return [{**r.model_dump(mode="json"), "kb_version": kb.kb_version} for r in selected]


def list_knowledge_candidates(kb: KnowledgeBase, catalog: Catalog, *, role: str, kb_version: str | None, catalog_version: str, status: str | None = None, page: int = 1, page_size: int = 20, category: str | None = None) -> dict[str, Any]:
    _check(kb, catalog, kb_version, catalog_version, role)
    if role != "teacher":
        raise DomainError("FORBIDDEN", "Candidate list is teacher-only")
    if status not in {None, "candidate", "unverified", "verified", "rejected"} or category not in {None, "annotation", "relation", "knowledge_point", "merge"}:
        raise DomainError("INVALID_ARGUMENT", "Invalid candidate filter")
    if isinstance(page, bool) or isinstance(page_size, bool) or not isinstance(page, int) or not isinstance(page_size, int) or page < 1 or not 1 <= page_size <= 100:
        raise DomainError("INVALID_ARGUMENT", "Invalid pagination")
    pending = status in {None, "candidate", "unverified"}
    items = []
    if category in {None, "annotation"}:
        items += [{**a.model_dump(mode="json"), "category": "annotation"} for a in kb.annotations
                  if a.candidate_id and a.verification_status == ("unverified" if pending else status)]
    if category in {None, "relation"}:
        items += [{**r.model_dump(mode="json"), "category": "relation"} for r in kb.relations
                  if r.verification_status == ("candidate" if pending else status)]
    if category in {None, "merge"}:
        items += [{**c.model_dump(mode="json"), "category": "merge"} for c in kb.merge_candidates
                  if c.verification_status == ("unverified" if pending else status)]
    if category == "knowledge_point":
        items += [{**p.model_dump(mode="json"), "category": "knowledge_point"} for p in kb.points
                  if p.verification_status == ("unverified" if pending else status)]
    start = (page - 1) * page_size
    return {"kb_version": kb.kb_version, "items": items[start:start + page_size], "total": len(items), "page": page, "page_size": page_size}
