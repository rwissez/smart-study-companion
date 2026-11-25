"""Pure human review decisions; persistence and authorization belong to callers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from study_companion.contracts import Catalog, DomainError
from study_companion.knowledge.dedup import merge_points
from study_companion.knowledge.models import Annotation, KnowledgeBase, KnowledgePoint, MergeCandidate, Relation
from study_companion.knowledge.relations import _flat, validate_candidates
from study_companion.knowledge.targets import validate_target_candidates


@dataclass(frozen=True)
class ReviewDecision:
    candidate_id: str
    action: Literal["accept", "reject", "edit-target"]
    target_ids: tuple[str, ...] | None = None
    evidence: str | None = None


def review_candidates(
    base_version: str, decisions: list[ReviewDecision], reviewer: str, kb: KnowledgeBase,
    *, new_version: str, catalog: Catalog | None = None,
    page_text: dict[object, str] | None = None, now: datetime | None = None,
) -> KnowledgeBase:
    """Build a new draft atomically; edited candidates require a later acceptance."""
    if kb.kb_version != base_version:
        raise DomainError("KB_CONTEXT_MISMATCH", "Review base version mismatch")
    if catalog is None:
        raise DomainError("CATALOG_REQUIRED", "Review requires a bound Catalog")
    if not reviewer.strip():
        raise DomainError("REVIEWER_REQUIRED", "Reviewer is required")
    if not new_version or new_version == base_version:
        raise DomainError("INVALID_KB_VERSION", "Review requires a new version")
    result = kb.model_copy(deep=True)
    by_id = {}
    for item in [*result.points, *result.annotations, *result.relations, *result.merge_candidates]:
        key = item.kp_id if isinstance(item, KnowledgePoint) else item.relation_id if isinstance(item, Relation) else item.candidate_id
        if key:
            if key in by_id:
                raise DomainError("CANDIDATE_ID_DUPLICATE", "Candidate IDs must be unique")
            by_id[key] = item
    requested = set()
    timestamp = (now or datetime.now(UTC)).isoformat()
    accepted_relations = []
    for decision in decisions:
        if decision.action not in {"accept", "reject", "edit-target"}:
            raise DomainError("INVALID_DECISION", "Unknown review action")
        if decision.candidate_id in requested:
            raise DomainError("DECISION_DUPLICATE", "Duplicate decision")
        requested.add(decision.candidate_id)
        item = by_id.get(decision.candidate_id)
        if item is None:
            raise DomainError("CANDIDATE_NOT_FOUND", "Review candidate not found")
        if decision.action != "edit-target" and (decision.target_ids is not None or decision.evidence is not None):
            raise DomainError("INVALID_DECISION", "Only edit-target can change target or evidence")
        if isinstance(item, MergeCandidate):
            if decision.action == "edit-target":
                raise DomainError("INVALID_DECISION", "Merge proposals can only be accepted or rejected")
            if decision.action == "accept":
                points = {p.kp_id: p for p in result.points}
                for point_id in (item.source_id, item.target_id):
                    point = points.get(point_id)
                    if point is None or point.verification_status == "rejected":
                        raise DomainError("MERGE_REVIEW_INVALID", "Merge point is missing or rejected")
                    loc = point.definition_locator
                    text = (page_text or {}).get((loc.source_file, loc.file_page))
                    if not point.snippet or text is None or _flat(point.snippet) not in _flat(text):
                        raise DomainError("EVIDENCE_NOT_IN_TEXT", "Merge requires evidence from both bound sources")
                merge_points(result, item.source_id, item.target_id)
            item.reviewed_by = reviewer.strip()
            item.reviewed_at = timestamp
            item.verification_status = "verified" if decision.action == "accept" else "rejected"
            type(item).model_validate(item.model_dump())
            continue
        if decision.action == "edit-target":
            if isinstance(item, KnowledgePoint) or not decision.target_ids or any(not x.strip() for x in decision.target_ids):
                raise DomainError("TARGET_REQUIRED", "A target candidate requires nonempty IDs")
            if isinstance(item, Relation):
                if len(decision.target_ids) != 1:
                    raise DomainError("TARGET_REQUIRED", "Relation requires one target")
                item.target_id = decision.target_ids[0]
            else:
                item.target_ids = sorted(set(decision.target_ids))
            if decision.evidence is not None:
                item.evidence = decision.evidence
        item.reviewed_by = reviewer.strip()
        item.reviewed_at = timestamp
        item.verification_status = ("rejected" if decision.action == "reject" else
                                    "verified" if decision.action == "accept" else
                                    "candidate" if isinstance(item, Relation) else "unverified")
        # Reconstruct after edits because model_copy and field assignment bypass validators.
        type(item).model_validate(item.model_dump())
        if decision.action == "reject":
            continue
        if isinstance(item, KnowledgePoint):
            loc = item.definition_locator
            text = (page_text or {}).get((loc.source_file, loc.file_page))
            if text is None or (item.snippet and _flat(item.snippet) not in _flat(text)):
                raise DomainError("EVIDENCE_NOT_IN_TEXT", "Knowledge point evidence does not match bound source")
            loc.verification_status = "verified"
        elif isinstance(item, Annotation):
            _, errors = validate_target_candidates(result, catalog, [item], page_text or {})
            if errors:
                raise DomainError("TARGET_VALIDATION_FAILED", "; ".join(e.code for e in errors))
            if decision.action == "accept" and item.source_type == "knowledge_point":
                point = next((p for p in result.points if p.kp_id == item.source_id), None)
                if point is not None:
                    field = "topic_ids" if item.target_type == "topic" else "indicator_ids"
                    setattr(point, field, sorted(set(getattr(point, field)) | set(item.target_ids)))
        else:
            accepted_relations.append(item)
    if accepted_relations:
        ids = {r.relation_id for r in accepted_relations}
        validation_kb = result.model_copy(update={"relations": [r for r in result.relations if r.relation_id not in ids and r.verification_status != "rejected"]})
        _, errors = validate_candidates(validation_kb, accepted_relations, page_text or {})
        if errors:
            raise DomainError("RELATION_REVIEW_INVALID", "; ".join(e.code for e in errors))
    result.kb_version = new_version
    result.parent_version = base_version
    result.status = "draft"
    result.created_at = timestamp
    result.change_summary.append(f"Human review by {reviewer.strip()}: {len(decisions)} decisions")
    return KnowledgeBase.model_validate(result.model_dump())
