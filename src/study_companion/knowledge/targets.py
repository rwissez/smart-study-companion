"""Conservative, explainable target candidates for the knowledge graph."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from hashlib import sha1
from typing import Literal

from study_companion.contracts import Catalog, DomainError


def material_source_id(catalog_version: str, material_id: str) -> str:
    if not catalog_version.strip() or not material_id.strip():
        raise DomainError("SOURCE_ID_MISSING", "Stable material identity required")
    return f"{catalog_version}/{material_id}"


def rubric_source_id(catalog_version: str, material_id: str, point_id: str) -> str:
    if not point_id.strip():
        raise DomainError("SOURCE_ID_MISSING", "Stable rubric identity required")
    return f"{material_source_id(catalog_version, material_id)}/{point_id}"
from study_companion.knowledge.models import Annotation, KnowledgeBase

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[一-鿿]")
SourceType = Literal["knowledge_point", "material", "rubric_point", "textbook_fragment"]
TargetType = Literal["topic", "indicator", "knowledge_point"]


def _norm(value: str) -> str:
    return unicodedata.normalize("NFKC", value).lower()


def _terms(value: str) -> set[str]:
    normalized = _norm(value)
    compact = re.sub(r"\s+", "", normalized)
    words = set(_TOKEN_RE.findall(normalized))
    if len(compact) >= 2:
        words.add(compact)
    return {word for word in words if len(word) > 1 or word.isascii()}


def _candidate_id(source_type: str, source_id: str, target_type: str, target_id: str) -> str:
    raw = f"{source_type}:{source_id}:{target_type}:{target_id}".encode()
    return f"CAND-{sha1(raw).hexdigest()[:16]}"


def textbook_fragment_id(source_id: str, file_page: int, evidence: str) -> str:
    """Return a stable identity for a bounded excerpt of a manifest source."""
    normalized = re.sub(r"\s+", " ", _norm(evidence)).strip()
    if not source_id.strip() or file_page < 1 or not normalized:
        raise DomainError("SOURCE_ID_MISSING", "Textbook fragment requires source, page, and evidence")
    raw = f"textbook_fragment:{source_id.strip()}:{file_page}:{normalized}".encode()
    return f"FRAG-{sha1(raw).hexdigest()[:16]}"


def register_textbook_fragment(
    source_id: str,
    file_page: int,
    evidence: str,
    target_id: str,
    *,
    target_type: TargetType = "knowledge_point",
    reason: str = "规则：教材片段来自 manifest 绑定页",
) -> Annotation:
    """Register one deterministic, still-unverified textbook fragment candidate."""
    fragment_id = textbook_fragment_id(source_id, file_page, evidence)
    return Annotation(
        candidate_id=_candidate_id("textbook_fragment", fragment_id, target_type, target_id),
        source_type="textbook_fragment",
        source_id=fragment_id,
        target_type=target_type,
        target_ids=[target_id],
        evidence=evidence,
        source_file=source_id.strip(),
        file_page=file_page,
        reason=reason,
        proposer="rule",
        verification_status="unverified",
    )


@dataclass(frozen=True)
class TargetRejection:
    candidate_id: str
    code: str
    message: str


def _annotation(
    source_type: SourceType,
    source_id: str,
    target_type: TargetType,
    target_id: str,
    keywords: set[str],
    reason: str,
    evidence: str = "",
) -> Annotation:
    return Annotation(
        candidate_id=_candidate_id(source_type, source_id, target_type, target_id),
        source_type=source_type,
        source_id=source_id,
        target_type=target_type,
        target_ids=[target_id],
        keywords=sorted(keywords),
        evidence=evidence[:160],
        reason=reason[:240],
        proposer="rule",
        verification_status="unverified",
    )


def _kp_text(kp) -> str:
    loc = kp.definition_locator
    return " ".join([kp.name, *kp.aliases, kp.snippet, loc.chapter, loc.section])


def generate_target_candidates(kb: KnowledgeBase, catalog: Catalog) -> list[Annotation]:
    """Generate literal-overlap candidates without making an automatic decision."""
    candidates: list[Annotation] = []
    topics = {topic.topic_id: topic for topic in catalog.topics}
    for kp in kb.points:
        kp_terms = _terms(_kp_text(kp))
        for topic in catalog.topics:
            overlap = kp_terms & _terms(topic.title)
            if overlap:
                candidates.append(_annotation(
                    "knowledge_point", kp.kp_id, "topic", topic.topic_id, overlap,
                    "规则：知识点文本与主题标题有字面关键词重合", kp.snippet,
                ))
        for indicator in catalog.indicators:
            overlap = kp_terms & _terms(indicator.description)
            if indicator.topic_id in topics:
                overlap |= kp_terms & _terms(topics[indicator.topic_id].title)
            if overlap:
                candidates.append(_annotation(
                    "knowledge_point", kp.kp_id, "indicator", indicator.indicator_id, overlap,
                    "规则：知识点文本与指标描述或所属主题有字面关键词重合", kp.snippet,
                ))

    for material in catalog.materials:
        text_terms = _terms(" ".join([
            material.content, material.reference_answer or "", material.reference_explanation or "",
        ]))
        for kp in kb.points:
            overlap = text_terms & _terms(_kp_text(kp))
            if set(kp.topic_ids) & set(material.topic_ids) or set(kp.indicator_ids) & set(material.indicator_ids):
                overlap |= {"catalog-context"}
            if overlap:
                candidates.append(_annotation(
                    "material", material_source_id(catalog.version, material.material_id), "knowledge_point", kp.kp_id, overlap,
                    "规则：材料文本或其 Catalog 主题/指标上下文与知识点匹配", material.content,
                ))
        for rubric in material.rubric_points:
            rubric_terms = _terms(rubric.description)
            for kp in kb.points:
                overlap = rubric_terms & _terms(_kp_text(kp))
                if overlap:
                    candidates.append(_annotation(
                        "rubric_point", rubric_source_id(catalog.version, material.material_id, rubric.point_id), "knowledge_point", kp.kp_id, overlap,
                        "规则：评分点文本与知识点有字面关键词重合", rubric.description,
                    ))
    return candidates


def validate_target_candidates(
    kb: KnowledgeBase,
    catalog: Catalog,
    candidates: list[Annotation],
    page_text: dict[object, str] | None = None,
) -> tuple[list[Annotation], list[TargetRejection]]:
    """Validate Catalog-bound IDs, duplicates, status, and optional source evidence."""
    kp_ids = {kp.kp_id for kp in kb.points if kp.verification_status != "rejected"}
    topic_ids = {topic.topic_id for topic in catalog.topics}
    indicator_ids = {indicator.indicator_id for indicator in catalog.indicators}
    materials = {material_source_id(catalog.version, material.material_id): material for material in catalog.materials}
    rubrics = {rubric_source_id(catalog.version, material.material_id, rubric.point_id): rubric
               for material in catalog.materials for rubric in material.rubric_points if rubric.point_id.strip()}
    accepted: list[Annotation] = []
    rejected: list[TargetRejection] = []
    seen: set[tuple[str, str, str]] = set()
    for candidate in candidates:
        target_id = candidate.target_ids[0] if candidate.target_ids else ""
        key = (candidate.source_id or "", candidate.target_type or "", target_id)
        code = message = ""
        target_ids = candidate.target_ids
        if candidate.source_id is None or candidate.target_type is None or candidate.source_type is None:
            code, message = "MALFORMED", "候选缺少来源或目标类型/ID"
        elif candidate.source_type == "knowledge_point" and candidate.source_id not in kp_ids:
            code, message = "KP_NOT_FOUND", "来源知识点不存在"
        elif candidate.source_type == "material" and candidate.source_id not in materials:
            code, message = "MATERIAL_NOT_FOUND", "来源材料不存在"
        elif candidate.source_type == "rubric_point" and candidate.source_id not in rubrics:
            code, message = "RUBRIC_POINT_NOT_FOUND", "来源评分点不存在"
        elif candidate.target_type == "knowledge_point" and any(target not in kp_ids for target in target_ids):
            code, message = "KP_NOT_FOUND", "目标知识点不存在"
        elif candidate.target_type == "topic" and any(target not in topic_ids for target in target_ids):
            code, message = "TOPIC_NOT_FOUND", "目标主题不存在"
        elif candidate.target_type == "indicator" and any(target not in indicator_ids for target in target_ids):
            code, message = "INDICATOR_NOT_FOUND", "目标指标不存在"
        elif candidate.verification_status == "verified" and candidate.proposer != "human" and not (candidate.reviewed_by and candidate.reviewed_at):
            code, message = "AUTO_VERIFIED", "自动生成的候选必须有人工审核记录才能 verified"
        elif candidate.source_file and candidate.file_page is None:
            code, message = "SOURCE_PAGE_REQUIRED", "指定来源文件时必须指定 file page"
        if not code and candidate.source_type == "textbook_fragment":
            if not candidate.source_file or candidate.file_page is None:
                code, message = "SOURCE_PAGE_REQUIRED", "Textbook fragment must bind a manifest source and page"
            elif candidate.source_id != textbook_fragment_id(candidate.source_file, candidate.file_page, candidate.evidence):
                code, message = "FRAGMENT_ID_MISMATCH", "Textbook fragment ID does not match its source, page, and evidence"
            else:
                manifest_key = (candidate.source_file, candidate.file_page)
                if manifest_key not in (page_text or {}):
                    code, message = "SOURCE_NOT_FOUND", "Textbook fragment source/page is not present in the manifest"
        if not code and page_text is not None:
            if candidate.source_type == "material":
                text = materials[candidate.source_id].content
            elif candidate.source_type == "rubric_point":
                text = rubrics[candidate.source_id].description
            elif candidate.source_type == "textbook_fragment":
                text = page_text.get((candidate.source_file, candidate.file_page))
            else:
                point = next((p for p in kb.points if p.kp_id == candidate.source_id), None)
                loc = point.definition_locator if point is not None else None
                text = page_text.get((loc.source_file, loc.file_page)) if loc else None
            if not candidate.evidence.strip() or text is None or re.sub(r"\s+", "", _norm(candidate.evidence)) not in re.sub(r"\s+", "", _norm(text)):
                code, message = "EVIDENCE_NOT_IN_TEXT", "Evidence must match its bound source"
        if not code and key in seen:
            code, message = "DUPLICATE", "同一来源到目标的候选重复"
        if code:
            rejected.append(TargetRejection(candidate.candidate_id or "", code, message))
        else:
            seen.add(key)
            accepted.append(candidate.model_copy(update={"verification_status": "unverified"}))
    return accepted, rejected


# Descriptive aliases for callers that prefer domain wording.
generate_knowledge_target_candidates = generate_target_candidates
validate_candidates = validate_target_candidates
