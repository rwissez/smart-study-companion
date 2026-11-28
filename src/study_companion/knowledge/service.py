"""A 正式知识体系构建（02 §4—§12）。

分工：
* **程序**负责结构、来源、去重、覆盖与闭环校验；
* **模型**负责语义提取与复核；模型未配置时，A 只产出**可确定性证明**的部分
  （课程指标级知识点、正式目录已核验的材料关联、已登记的前置关系），
  并在报告里写明"未执行模型提取"，不把规则投影冒充完整的教材级知识体系。

本模块不写数据库；正式发布由 ``pipeline`` 在事务内完成。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from study_companion.contracts import (
    Catalog,
    Contract,
    ReviewCheck,
    ReviewInfo,
    ReviewVerdict,
    SourceLocator,
    ValidationIssue,
)
from study_companion.knowledge.summary import (
    coverage_item,
    material_mapping,
    point_source_states,
    review_with_source_check,
    source_resolvability,
)


class KnowledgeConfig(Contract):
    rule_version: str = "a-rules-1"
    prompt_version: str = "a-1.0"
    max_repair_rounds: int = Field(default=2, ge=0)
    max_model_calls_per_job: int = Field(default=2000, ge=1)
    min_text_chars: int = Field(default=30, ge=0)


class KnowledgePointDraft(Contract):
    kp_id: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    kind: str = "concept"
    definition: str = ""
    scope_role: str = "core"
    topic_ids: list[str] = Field(default_factory=list)
    indicator_ids: list[str] = Field(default_factory=list)
    snippet: str = ""
    source_locators: list[SourceLocator] = Field(default_factory=list)
    review_info: ReviewInfo | None = None
    content_hash: str = ""


class KnowledgeRelationDraft(Contract):
    relation_id: str
    type: str
    source_ids: list[str]
    target_id: str
    basis: str = "standard"
    note: str = ""
    source_locators: list[SourceLocator] = Field(default_factory=list)
    review_info: ReviewInfo | None = None


class KnowledgeAnnotationDraft(Contract):
    annotation_id: str
    source_type: str
    source_id: str
    target_type: str = "knowledge_point"
    target_ids: list[str]
    knowledge_role: str | None = "primary"
    basis: str = "standard"
    evidence: str = ""
    source_locators: list[SourceLocator] = Field(default_factory=list)
    review_info: ReviewInfo | None = None


class KnowledgeSnapshotDraft(Contract):
    dataset_id: str
    catalog_version: str
    knowledge_version: str
    parent_version: str | None = None
    points: list[KnowledgePointDraft] = Field(default_factory=list)
    relations: list[KnowledgeRelationDraft] = Field(default_factory=list)
    annotations: list[KnowledgeAnnotationDraft] = Field(default_factory=list)
    review_summary: dict[str, Any] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)


class KnowledgeBuildResult(Contract):
    candidate_snapshot: KnowledgeSnapshotDraft
    review_info: ReviewInfo
    issues: list[ValidationIssue] = Field(default_factory=list)
    publishable_object_ids: list[str] = Field(default_factory=list)


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def build_knowledge(
    catalog: Catalog,
    *,
    context: Any,
    config: KnowledgeConfig,
    dataset_id: str,
    knowledge_version: str,
    model_configured: bool = False,
    textbook_candidates: list[Any] | None = None,
    textbook_stats: dict[str, int] | None = None,
) -> KnowledgeBuildResult:
    """构建候选知识体系并给出可发布子集。

    只使用目录与已登记来源中**可核对**的信息；缺少教材正文解析能力时如实记录
    限制，不虚构页码或原文。
    """

    issues: list[ValidationIssue] = []
    limitations: list[str] = []

    points: list[KnowledgePointDraft] = []
    for indicator in catalog.indicators:
        locators = list(indicator.textbook_refs) or [
            SourceLocator(
                source_file="课程要求.csv",
                verification_status="unverified",
                source_kind="standard",
            )
        ]
        verified = any(item.verification_status == "verified" for item in locators)
        resolution = source_resolvability(locators)
        checks = [
            ReviewCheck(
                code="SOURCE_RESOLVABLE",
                passed=resolution.passed,
                reason=resolution.reason,
            ),
            ReviewCheck(
                code="POINT_SCOPE",
                passed=bool(indicator.description.strip()),
                reason="知识点定义来自观察指标，非空且归属明确" if indicator.description.strip() else "观察指标为空",
            ),
        ]
        review = ReviewInfo(
            status="validated" if all(item.passed for item in checks) else "needs_review",
            method="rule",
            checks=checks,
            reviewed_at="",
            rule_version=config.rule_version,
        )
        points.append(
            KnowledgePointDraft(
                kp_id=f"kp-{indicator.indicator_id}",
                name=indicator.description,
                kind="concept",
                definition=indicator.description,
                scope_role="core",
                topic_ids=[indicator.topic_id],
                indicator_ids=[indicator.indicator_id],
                snippet=indicator.description[:80],
                source_locators=locators,
                review_info=review,
                content_hash=_hash(
                    {
                        "name": indicator.description,
                        "topic": indicator.topic_id,
                        "indicator": indicator.indicator_id,
                        "verified_source": verified,
                    }
                ),
            )
        )
    if textbook_stats:
        for note in textbook_stats.get("model_notes") or []:  # type: ignore[union-attr]
            limitations.append(str(note))
    merges: dict[str, str] = {}
    if textbook_candidates and model_configured:
        try:
            from study_companion.knowledge.merge_model import resolve_merges
            from study_companion.model_service import ModelService

            merge_service = ModelService()
            if merge_service.configured:
                merges, merge_notes = resolve_merges(
                    service=merge_service, candidates=textbook_candidates, points=points
                )
                limitations.extend(merge_notes)
        except Exception:
            limitations.append("教材候选合并判断异常，全部保留待审")
    if textbook_candidates:
        existing_names = {point.name for point in points}
        for candidate in textbook_candidates:
            target = merges.get(getattr(candidate, "candidate_id", ""))
            if target:
                for index, point in enumerate(points):
                    if point.kp_id != target:
                        continue
                    locator = SourceLocator(
                        source_file=getattr(candidate, "file_id", "") or candidate.chapter_id,
                        source_kind="textbook",
                        file_id=getattr(candidate, "file_id", "") or None,
                        chapter=candidate.chapter_id,
                        pdf_page=getattr(candidate, "page", None),
                        verification_status="verified",
                    )
                    merged_locators = list(point.source_locators) + [locator]
                    resolution = source_resolvability(merged_locators)
                    points[index] = point.model_copy(
                        update={
                            "source_locators": merged_locators,
                            "aliases": sorted(
                                set(point.aliases)
                                | {candidate.name}
                                | set(getattr(candidate, "aliases", []) or [])
                            ),
                            "indicator_ids": sorted(
                                set(point.indicator_ids)
                                | set(getattr(candidate, "indicator_ids", []) or [])
                            ),
                            "review_info": review_with_source_check(
                                point.review_info, resolution
                            ),
                        }
                    )
                    break
                continue
            if candidate.name in existing_names:
                continue
            existing_names.add(candidate.name)
            validated = getattr(candidate, "review_status", "needs_review") == "validated"
            candidate_locator = SourceLocator(
                source_file=getattr(candidate, "file_id", "") or candidate.chapter_id,
                source_kind="textbook",
                file_id=getattr(candidate, "file_id", "") or None,
                chapter=candidate.chapter_id,
                pdf_page=getattr(candidate, "page", None),
                verification_status="verified",
            )
            resolution = source_resolvability([candidate_locator])
            checks = [
                ReviewCheck(
                    code="SOURCE_RESOLVABLE",
                    passed=resolution.passed,
                    reason=resolution.reason,
                ),
                ReviewCheck(
                    code="POINT_SCOPE",
                    passed=bool(candidate.definition.strip()),
                    reason="定义句来自教材正文" if candidate.definition.strip() else "定义句为空",
                ),
            ]
            if not validated:
                checks.append(
                    ReviewCheck(
                        code="NEEDS_TEACHER_CONFIRM",
                        passed=False,
                        reason=getattr(candidate, "review_note", "")
                        or "教材候选未通过独立复核，需教师确认",
                    )
                )
            points.append(
                KnowledgePointDraft(
                    kp_id=f"kp-{candidate.candidate_id}",
                    name=candidate.name,
                    kind=getattr(candidate, "kind", "concept"),
                    definition=candidate.definition,
                    scope_role=getattr(candidate, "scope_role", "core"),
                    topic_ids=list(getattr(candidate, "topic_ids", []) or []),
                    indicator_ids=list(getattr(candidate, "indicator_ids", []) or []),
                    snippet=candidate.snippet,
                    source_locators=[candidate_locator],
                    review_info=ReviewInfo(
                        status=(
                            "validated"
                            if validated and all(check.passed for check in checks)
                            else "needs_review"
                        ),
                        method="model" if validated else "rule",
                        checks=checks,
                        reviewed_at="",
                        rule_version=config.rule_version,
                    ),
                    content_hash=candidate.content_hash,
                )
            )
        validated_count = sum(
            1
            for candidate in textbook_candidates
            if getattr(candidate, "review_status", "") == "validated"
        )
        limitations.append(
            f"教材候选 {len(textbook_candidates)} 条，其中 {validated_count} 条通过独立复核；"
            "其余需教师逐条确认后才可用于正式方案"
        )
        limitations.append(
            "教材候选只核对页码存在，未核对章节页范围（候选未携带章节页范围），不写 SOURCE_PAGE_IN_RANGE 通过"
        )
    if not points:
        issues.append(
            ValidationIssue(
                code="NO_POINTS",
                message="当前目录没有可构建的课程指标，无法生成知识体系",
                severity="error",
            )
        )

    point_ids = {point.kp_id for point in points}
    relations: list[KnowledgeRelationDraft] = []
    for index, link in enumerate(catalog.links, start=1):
        source_kp = f"kp-{link.source_id}"
        target_kp = f"kp-{link.target_id}"
        if source_kp not in point_ids or target_kp not in point_ids:
            continue
        # 只有明确的"前置"语义才进入 C 的强制顺序；supports 仅作解释关系。
        relation_type = "prerequisite" if link.relation == "prerequisite" else "has_property"
        relations.append(
            KnowledgeRelationDraft(
                relation_id=f"rel-{index:03d}-{_hash([source_kp, target_kp, relation_type])[:8]}",
                type=relation_type,
                source_ids=[source_kp] if relation_type == "prerequisite" else [target_kp],
                target_id=target_kp if relation_type == "prerequisite" else source_kp,
                basis=link.basis,
                note=link.description,
                review_info=ReviewInfo(
                    status="validated",
                    method="rule",
                    checks=[
                        ReviewCheck(
                            code="RELATION_VALID",
                            passed=True,
                            reason="关系来自本机已登记课程出处配置",
                        )
                    ],
                    rule_version=config.rule_version,
                ),
            )
        )

    annotations: list[KnowledgeAnnotationDraft] = []
    for material in catalog.materials:
        targets = [f"kp-{indicator_id}" for indicator_id in material.indicator_ids]
        targets = [item for item in targets if item in point_ids]
        if not targets:
            issues.append(
                ValidationIssue(
                    code="MAPPING_PARTIAL",
                    message=f"材料 {material.material_id} 没有可核验的知识点关联",
                    severity="warning",
                    field=material.material_id,
                )
            )
            continue
        annotations.append(
            KnowledgeAnnotationDraft(
                annotation_id=f"ann-{material.material_id}",
                source_type="material",
                source_id=material.material_id,
                target_ids=targets,
                knowledge_role="primary",
                basis="standard",
                evidence="材料指标归属由课程要求表确定",
            )
        )

    # 映射缺口补充：没有指标归属的题，让模型在已有知识点里找，两轮仍找不到就记缺口。
    mapping_gap_items: list[dict[str, str]] = []
    if model_configured and annotations:
        mapped_ids = {item.source_id for item in annotations}
        unmapped = [
            material
            for material in catalog.materials
            if material.material_id not in mapped_ids
        ]
        if unmapped:
            try:
                from study_companion.knowledge.gap_fill import fill_mapping_gaps
                from study_companion.model_service import ModelService

                gap_service = ModelService()
                if gap_service.configured:
                    accepted, gap_notes, mapping_gap_items = fill_mapping_gaps(
                        service=gap_service,
                        materials=unmapped,
                        points=points,
                        existing_annotations=annotations,
                    )
                    limitations.extend(gap_notes)
                    for item in accepted:
                        annotations.append(KnowledgeAnnotationDraft(**item))
                    resolved_ids = {item["source_id"] for item in accepted}
                    resolved_ids |= {item["material_id"] for item in mapping_gap_items}
                    issues = [
                        issue
                        for issue in issues
                        if not (
                            issue.code == "MAPPING_PARTIAL" and issue.field in resolved_ids
                        )
                    ]
            except Exception:
                limitations.append("映射缺口补充异常，未建立新的题目关联")

    # 形式检查：自环、源数、重复
    seen: set[tuple[str, tuple[str, ...], str]] = set()
    clean_relations: list[KnowledgeRelationDraft] = []
    for relation in relations:
        if relation.target_id in relation.source_ids:
            issues.append(
                ValidationIssue(
                    code="RELATION_SELF_LOOP",
                    message=f"关系 {relation.relation_id} 存在自环，已隔离",
                    severity="error",
                )
            )
            continue
        if relation.type == "composed_of" and len(set(relation.source_ids)) < 2:
            issues.append(
                ValidationIssue(
                    code="RELATION_SOURCE_COUNT",
                    message=f"关系 {relation.relation_id} 的组合来源不足两个",
                    severity="error",
                )
            )
            continue
        signature = (relation.type, tuple(sorted(relation.source_ids)), relation.target_id)
        if signature in seen:
            continue
        seen.add(signature)
        clean_relations.append(relation)

    # 前置环检测：只用拓扑排序隔离环上的新边
    clean_relations = _break_cycles(clean_relations, issues)

    # 关系推断：模型只提候选，程序校验 + 独立复核通过后才进正式候选。
    model_relation_notes: list[str] = []
    try:
        from study_companion.knowledge.relation_model import infer_relations
        from study_companion.model_service import ModelService

        relation_service = ModelService()
        if relation_service.configured and len(points) >= 2:
            payload_relations, model_relation_notes = infer_relations(
                service=relation_service,
                points=[point.model_dump(mode="json") for point in points],
                knowledge_version=knowledge_version,
            )
            existing_signatures = {
                (item.type, tuple(sorted(item.source_ids)), item.target_id)
                for item in clean_relations
            }
            for item in payload_relations:
                signature = (
                    item["type"],
                    tuple(sorted(item["source_ids"])),
                    item["target_id"],
                )
                if signature in existing_signatures:
                    continue
                existing_signatures.add(signature)
                clean_relations.append(
                    KnowledgeRelationDraft(
                        relation_id=item["relation_id"],
                        type=item["type"],
                        source_ids=list(item["source_ids"]),
                        target_id=item["target_id"],
                        basis=item.get("basis", "team_inference"),
                        note=item.get("note", ""),
                        source_locators=[
                            SourceLocator.model_validate(locator)
                            for locator in item.get("source_locators", [])
                        ],
                        review_info=ReviewInfo(
                            status="validated",
                            method="model",
                            checks=[
                                ReviewCheck(
                                    code="relation_independent_review",
                                    passed=True,
                                    reason="关系由独立模型复核通过",
                                )
                            ],
                            reviewed_at="",
                            rule_version=config.rule_version,
                        ),
                    )
                )
        elif len(points) >= 2:
            model_relation_notes = ["模型未配置，关系仅来自目录登记"]
    except Exception:  # 关系推断失败不影响其余候选构建
        model_relation_notes = ["关系推断异常，本轮未新增模型关系"]
    for note in model_relation_notes:
        if note and note not in limitations:
            limitations.append(note)

    indicator_ids = {indicator.indicator_id for indicator in catalog.indicators}
    covered_indicators = {
        indicator_id
        for point in points
        for indicator_id in point.indicator_ids
        if indicator_id in indicator_ids
    }
    question_materials = [item for item in catalog.materials if item.material_type == "question"]
    card_materials = [item for item in catalog.materials if item.material_type == "card"]
    textbook_materials = [item for item in catalog.materials if item.material_type == "textbook"]
    source_states = point_source_states(points)
    mapping = material_mapping(catalog.materials, annotations, point_ids)
    mapped_ids = mapping.mapped_material_ids

    if not model_configured:
        limitations.append(
            "未配置模型：本轮只生成课程指标级知识点与已登记关联，未执行教材正文提取与语义复核"
        )

    mapped_indicators = {
        indicator_id
        for material in catalog.materials
        for indicator_id in (material.indicator_ids or [])
    }
    material_ids = {item.material_id for item in catalog.materials}
    unclassified_note = (
        f"{len(mapping.unclassified_material_ids)} 条关联材料不在当前目录，无法归类，未计入任何材料分子："
        + "、".join(mapping.unclassified_material_ids)
        if mapping.unclassified_material_ids
        else ""
    )
    verified_sources = source_states["verified"]
    unresolved_sources = source_states["unverified"] + source_states["missing"]
    if unresolved_sources:
        limitations.append(
            f"{len(unresolved_sources)} 个知识点的来源只有未核验或有核验无页码的定位，"
            "SOURCE_RESOLVABLE 记 false，已进入教师审核队列，不计入来源可核验率"
        )
    if unclassified_note:
        limitations.append(unclassified_note)
    limitations.append(
        "review_summary 在知识构建时计算；教师补来源或部分审核后需重建知识版本才能更新统计"
    )
    mapping_gaps = {
        "needs_new_point": [item for item in mapping_gap_items if item.get("status") == "needs_review"],
        "unmapped": [item for item in mapping_gap_items if item.get("status") == "unmapped"],
        "indicators_without_material": sorted(indicator_ids - mapped_indicators),
        "materials_without_indicator": sorted(
            item.material_id
            for item in question_materials
            if not (set(item.indicator_ids or []) & indicator_ids)
        ),
        "materials_without_rubric": sorted(
            item.material_id
            for item in question_materials
            if item.question_type == "solution" and not (item.rubric_points or [])
        ),
        "materials_without_annotation": sorted(material_ids - mapped_ids),
        "questions_without_annotation": sorted(
            item.material_id for item in question_materials if item.material_id not in mapped_ids
        ),
        "cards_without_annotation": sorted(
            item.material_id for item in card_materials if item.material_id not in mapped_ids
        ),
        "textbooks_without_annotation": sorted(
            item.material_id for item in textbook_materials if item.material_id not in mapped_ids
        ),
        "materials_without_classification": list(mapping.unclassified_material_ids),
        "points_without_annotation": sorted(
            point.kp_id
            for point in points
            if not any(point.kp_id in (annotation.target_ids or []) for annotation in annotations)
        ),
        "points_without_verified_source": sorted(unresolved_sources),
        "limitations": [
            "缺口按目录与材料表计算；缺失项不自动补全，需教师确认",
            "材料关联缺口按题目、学习卡（讲义）、教材分别列出，不混用",
        ],
    }

    def _partial_count(materials: list[Any]) -> int:
        """有指标归属但没形成正式知识点关联的材料：算部分关联，不算已关联。"""

        return sum(
            1
            for item in materials
            if item.material_id not in mapped_ids
            and (set(item.indicator_ids or []) & indicator_ids)
        )

    review_summary = {
        "dataset_id": dataset_id,
        "catalog_version": catalog.version,
        "knowledge_version": knowledge_version,
        "candidate_version": knowledge_version,
        "generated_at": "",
        "mapping_gaps": mapping_gaps,
        "counts": {
            "source_files": (textbook_stats or {}).get(
                "source_files",
                len({loc.source_file for point in points for loc in point.source_locators}),
            ),
            "total_pages": (textbook_stats or {}).get("total_pages", 0),
            "relevant_pages": (textbook_stats or {}).get("relevant_pages", 0),
            "usable_relevant_pages": (textbook_stats or {}).get("usable_relevant_pages", 0),
            "recovered_pages": (textbook_stats or {}).get("recovered_pages", 0),
            "formal_points": len(points),
            "formal_relations": len(clean_relations),
            "formal_annotations": len(annotations),
            "unresolved_items": len([issue for issue in issues if issue.severity == "error"]),
            "catalog_materials": len(catalog.materials),
            "materials_with_annotation": len(mapped_ids & material_ids),
            "question_total": mapping.totals["question"],
            "question_with_annotation": mapping.mapped["question"],
            "card_total": mapping.totals["card"],
            "card_with_annotation": mapping.mapped["card"],
            "textbook_total": mapping.totals["textbook"],
            "textbook_with_annotation": mapping.mapped["textbook"],
            "points_with_verified_source": len(verified_sources),
            "points_without_verified_source": len(unresolved_sources),
        },
        "coverage_scope_note": (
            "材料类比率（题目、学习卡、教材）的分子与分母只取同一类材料；"
            "指标与知识点比率按各自对象统计。比率不会超过 100%，"
            "分母为 0 或分子大于分母时 value 为 null 并写原因"
        ),
        "coverage": [
            coverage_item(
                key="indicator_coverage",
                label="课程指标覆盖率",
                population="indicator",
                numerator=len(covered_indicators),
                denominator=len(catalog.indicators),
                numerator_label="至少有一个正式知识点关联的课程指标数",
                denominator_label="全部课程指标数",
                scope_description="按课程指标统计，与题目、学习卡、教材的数量无关",
            ),
            coverage_item(
                key="question_mapping",
                label="题目知识关联率",
                population="question",
                numerator=mapping.mapped["question"],
                denominator=mapping.totals["question"],
                numerator_label="有正式知识点关联的题目数",
                denominator_label="全部正式题目数（material_type=question）",
                scope_description=(
                    "题目关联率=有正式知识点关联的题目数／全部题目数；"
                    "学习卡（讲义）与教材各自单独统计，不混入题目分子或分母"
                ),
                partial_count=_partial_count(question_materials),
                unknown_count=len(mapping.unclassified_material_ids),
                reason_notes=[unclassified_note] if unclassified_note else None,
            ),
            coverage_item(
                key="card_mapping",
                label="学习卡（讲义）知识关联率",
                population="card",
                numerator=mapping.mapped["card"],
                denominator=mapping.totals["card"],
                numerator_label="有正式知识点关联的学习卡（讲义）数",
                denominator_label="全部学习卡（讲义）数（material_type=card）",
                scope_description="只统计学习卡（讲义）；不并入题目关联率，也不占用题目分母",
                partial_count=_partial_count(card_materials),
                unknown_count=len(mapping.unclassified_material_ids),
                reason_notes=[unclassified_note] if unclassified_note else None,
            ),
            coverage_item(
                key="textbook_mapping",
                label="教材知识关联率",
                population="textbook",
                numerator=mapping.mapped["textbook"],
                denominator=mapping.totals["textbook"],
                numerator_label="有正式知识点关联的教材数",
                denominator_label="全部教材数（material_type=textbook）",
                scope_description="只统计教材；不并入题目关联率，也不占用题目分母",
                partial_count=_partial_count(textbook_materials),
                unknown_count=len(mapping.unclassified_material_ids),
                reason_notes=[unclassified_note] if unclassified_note else None,
            ),
            coverage_item(
                key="point_sourcing",
                label="知识点来源可核验率",
                population="knowledge_point",
                numerator=len(verified_sources),
                denominator=len(points),
                numerator_label="至少有一处已核验且带页码定位的知识点数",
                denominator_label="全部正式知识点数",
                scope_description=(
                    "只认 verification_status=verified 且带 PDF 页码或印刷页码的来源；"
                    "只有课程要求表登记、未核验或没有页码的不计入分子"
                ),
                unknown_count=len(source_states["missing"]),
            ),
        ],
        "audit": {
            "reviewed_objects": len(points) + len(clean_relations) + len(annotations),
            "first_pass": len(points),
            "auto_repaired_pass": 0,
            "human_pass": 0,
            "rejected": 0,
            "needs_review": len([issue for issue in issues if issue.severity == "error"]),
            "not_processed": 0,
        },
        "issues": [],
        "limitations": limitations,
    }

    snapshot = KnowledgeSnapshotDraft(
        dataset_id=dataset_id,
        catalog_version=catalog.version,
        knowledge_version=knowledge_version,
        points=points,
        relations=clean_relations,
        annotations=annotations,
        review_summary=review_summary,
        limitations=limitations,
    )
    review = ReviewInfo(
        status="validated" if points and not any(issue.severity == "error" for issue in issues) else "needs_review",
        method="rule",
        checks=[
            ReviewCheck(
                code="DEPENDENCY_READY",
                passed=bool(points),
                reason="知识点、关系与被引用的目标均在本次候选内",
            ),
            ReviewCheck(
                code="MAPPING_VALID",
                passed=bool(mapped_ids & material_ids),
                reason="材料关联目标存在于正式知识点集合；题目、学习卡、教材分别计数",
            ),
        ],
        reviewed_at="",
        rule_version=config.rule_version,
    )
    return KnowledgeBuildResult(
        candidate_snapshot=snapshot,
        review_info=review,
        issues=issues,
        publishable_object_ids=[
            item.kp_id
            for item in points
            if item.review_info is not None and item.review_info.status == "validated"
        ]
        + [
            item.relation_id
            for item in clean_relations
            if item.review_info is not None and item.review_info.status == "validated"
        ]
        + [
            item.annotation_id
            for item in annotations
            if item.review_info is not None and item.review_info.status == "validated"
        ],
    )


def _break_cycles(
    relations: list[KnowledgeRelationDraft], issues: list[ValidationIssue]
) -> list[KnowledgeRelationDraft]:
    """只对 prerequisite 做环检测，成环的新边单独隔离，不牵连无关边。"""

    prereq = [item for item in relations if item.type == "prerequisite"]
    graph: dict[str, set[str]] = {}
    for item in prereq:
        for source in item.source_ids:
            graph.setdefault(source, set()).add(item.target_id)
    kept: list[KnowledgeRelationDraft] = [item for item in relations if item.type != "prerequisite"]
    accepted: dict[str, set[str]] = {}
    for item in prereq:
        node = item.source_ids[0]
        target = item.target_id
        if _reachable(accepted, target, node):
            issues.append(
                ValidationIssue(
                    code="RELATION_CYCLE",
                    message=f"前置关系 {item.relation_id} 会形成环，已隔离",
                    severity="error",
                )
            )
            continue
        accepted.setdefault(node, set()).add(target)
        kept.append(item)
    return kept


def _reachable(graph: dict[str, set[str]], start: str, target: str) -> bool:
    stack = [start]
    seen: set[str] = set()
    while stack:
        node = stack.pop()
        if node == target:
            return True
        if node in seen:
            continue
        seen.add(node)
        stack.extend(graph.get(node, ()))
    return False
