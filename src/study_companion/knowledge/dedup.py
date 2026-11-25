"""Conservative deterministic equivalence detection with explicit review proposals."""

from __future__ import annotations

from hashlib import sha1
import re
import unicodedata

from study_companion.contracts import DomainError
from study_companion.knowledge.models import KnowledgeBase, KnowledgePoint, MergeCandidate


def normalize_name(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).casefold()


def normalize_definition(value: str) -> str:
    # Preserve mathematical punctuation/operators rather than conflating definitions.
    return normalize_name(value).strip("。.;；").removeprefix("再次")


def _basis(left: KnowledgePoint, right: KnowledgePoint) -> list[str]:
    names_left = {normalize_name(x) for x in [left.name, *left.aliases]} - {""}
    names_right = {normalize_name(x) for x in [right.name, *right.aliases]} - {""}
    basis = []
    if normalize_name(left.name) and normalize_name(left.name) == normalize_name(right.name):
        basis.append("name")
    elif names_left & names_right:
        basis.append("alias")
    definition = normalize_definition(left.snippet)
    if definition and definition == normalize_definition(right.snippet):
        basis.append("definition")
    return basis


def are_exact_duplicates(left: KnowledgePoint, right: KnowledgePoint) -> bool:
    basis = _basis(left, right)
    return ("definition" in basis and bool({"name", "alias"} & set(basis))
            and len(left.snippet) < 80 and len(right.snippet) < 80
            and left.kind == right.kind
            and left.verification_status == right.verification_status
            and left.verification_status != "rejected")


def propose_merge_candidates(kb: KnowledgeBase) -> list[MergeCandidate]:
    """Return stable proposals, retaining human decisions on repeated scans."""
    existing = {frozenset((c.source_id, c.target_id)): c for c in kb.merge_candidates}
    points = sorted((p for p in kb.points if p.verification_status != "rejected"), key=lambda p: p.kp_id)
    candidates = []
    for index, left in enumerate(points):
        for right in points[index + 1:]:
            basis = _basis(left, right)
            if not basis:
                continue
            key = frozenset((left.kp_id, right.kp_id))
            if key in existing:
                candidates.append(existing[key].model_copy(deep=True))
                continue
            digest = sha1(f"{left.kp_id}|{right.kp_id}".encode()).hexdigest()[:16]
            reason = ("名称或别名一致，但定义不同或缺失，可能是同名异义，请对照两处原文。"
                      if "definition" not in basis else
                      "定义短摘一致，请核对名称、别名、知识类型和两处来源，确认是否为同一知识点。")
            candidates.append(MergeCandidate(candidate_id=f"MERGE-{digest}", source_id=right.kp_id,
                                             target_id=left.kp_id, reason=reason, match_basis=basis,
                                             source_name=right.name, target_name=left.name,
                                             source_snippet=right.snippet, target_snippet=left.snippet,
                                             source_locator=right.definition_locator.model_copy(deep=True),
                                             target_locator=left.definition_locator.model_copy(deep=True)))
    # Historical accepted/rejected proposals stay auditable after points are merged.
    generated = {c.candidate_id for c in candidates}
    candidates.extend(c.model_copy(deep=True) for c in kb.merge_candidates if c.candidate_id not in generated)
    return candidates


def _merge_preserves_relations(kb: KnowledgeBase, source_id: str, target_id: str) -> bool:
    for relation in kb.relations:
        sources = {target_id if x == source_id else x for x in relation.source_ids}
        target = target_id if relation.target_id == source_id else relation.target_id
        if target in sources or (relation.type == "composed_of" and len(sources) < 2):
            return False
    return True


def merge_points(kb: KnowledgeBase, source_id: str, target_id: str) -> None:
    """Rewrite references in a private draft, preserving source provenance."""
    if not _merge_preserves_relations(kb, source_id, target_id):
        raise DomainError("MERGE_RELATION_CONFLICT", "Merge would collapse a relation; review the relation first")
    source = next(p for p in kb.points if p.kp_id == source_id)
    target = next(p for p in kb.points if p.kp_id == target_id)
    target.aliases = sorted(set([*target.aliases, *source.aliases, source.name]) - {target.name})
    for field in ("topic_ids", "indicator_ids"):
        setattr(target, field, sorted(set(getattr(target, field)) | set(getattr(source, field))))
    for loc in [target.definition_locator, *target.source_locators, source.definition_locator, *source.source_locators]:
        if loc not in target.source_locators:
            target.source_locators.append(loc.model_copy(deep=True))
    for relation in kb.relations:
        relation.source_ids = list(dict.fromkeys(target_id if x == source_id else x for x in relation.source_ids))
        if relation.target_id == source_id:
            relation.target_id = target_id
    for annotation in kb.annotations:
        if annotation.source_type == "knowledge_point" and annotation.source_id == source_id:
            annotation.source_id = target_id
        for field in ("kp_ids", "target_ids"):
            if field == "kp_ids" or annotation.target_type == "knowledge_point":
                setattr(annotation, field, sorted({target_id if x == source_id else x for x in getattr(annotation, field)}))
    kb.points = [p for p in kb.points if p.kp_id != source_id]


def deduplicate_knowledge(kb: KnowledgeBase) -> KnowledgeBase:
    """Copy a snapshot, fold strict duplicates, queue ambiguous pairs for review."""
    result = kb.model_copy(deep=True)
    protected = {frozenset((c.source_id, c.target_id)) for c in kb.merge_candidates}
    canonical: list[KnowledgePoint] = []
    # Preserve earlier snapshot points as canonical targets; incoming points follow them.
    for point in list(result.points):
        match = next((p for p in canonical if frozenset((p.kp_id, point.kp_id)) not in protected
                      and are_exact_duplicates(p, point)
                      and _merge_preserves_relations(result, point.kp_id, p.kp_id)), None)
        if match is None:
            canonical.append(point)
        else:
            merge_points(result, point.kp_id, match.kp_id)
    result.merge_candidates = propose_merge_candidates(result)
    return KnowledgeBase.model_validate(result.model_dump())
