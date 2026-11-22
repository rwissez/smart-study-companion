"""S6：把细粒度知识图谱投影为现有 Catalog。

不修改 Catalog 契约。映射规则由用户确认：
- derives_from（衍生） -> prerequisite
- composed_of（合并创造） -> prerequisite
- special_case（特例） -> supports
- has_property（从属）不映射为学习顺序

只有 verified 关系进入 Catalog.links；候选与被跳过项留在 ProjectionReport。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product

from study_companion.contracts import Catalog, CatalogLink, Material, SourceLocator
from study_companion.knowledge.models import KnowledgeBase, Relation

RELATION_MAP = {
    "derives_from": "prerequisite",
    "composed_of": "prerequisite",
    "special_case": "supports",
}


@dataclass
class ProjectionReport:
    candidate_relation_ids: list[str] = field(default_factory=list)
    skipped_relation_ids: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class ProjectionResult:
    catalog: Catalog
    report: ProjectionReport


def _topic_ids(indicator_ids: list[str], catalog: Catalog) -> list[str]:
    by_id = {item.indicator_id: item.topic_id for item in catalog.indicators}
    return sorted({by_id[item] for item in indicator_ids if item in by_id})


def _knowledge_material(point, catalog: Catalog) -> Material:
    loc = point.definition_locator
    summary = point.name if not point.snippet else f"{point.name}：{point.snippet}"
    return Material(
        material_id=f"kp:{point.kp_id}",
        material_type="textbook",
        topic_ids=_topic_ids(point.indicator_ids, catalog),
        indicator_ids=list(point.indicator_ids),
        content=summary,
        source_locator=SourceLocator(
            source_file=loc.source_file,
            pdf_page=loc.file_page,
            printed_page=str(loc.printed_page) if loc.printed_page is not None else None,
            verification_status=(
                "verified" if point.verification_status == "verified" else "unverified"
            ),
        ),
    )


def _relation_links(
    relation: Relation,
    kb: KnowledgeBase,
    report: ProjectionReport,
) -> list[CatalogLink]:
    relation_name = RELATION_MAP.get(relation.type)
    if relation_name is None:
        report.skipped_relation_ids.append(relation.relation_id)
        return []

    points = {item.kp_id: item for item in kb.points}
    target = points[relation.target_id]
    sources = [points[item] for item in relation.source_ids]
    if not target.indicator_ids or any(not source.indicator_ids for source in sources):
        report.skipped_relation_ids.append(relation.relation_id)
        report.warnings.append(
            f"{relation.relation_id} 缺少知识点到指标的标注，不能投影"
        )
        return []

    links: list[CatalogLink] = []
    for source in sources:
        for source_indicator, target_indicator in product(
            source.indicator_ids, target.indicator_ids
        ):
            if source_indicator == target_indicator:
                continue
            links.append(
                CatalogLink(
                    source_id=source_indicator,
                    target_id=target_indicator,
                    relation=relation_name,
                    basis=relation.basis,
                    description=(
                        f"知识图谱投影：{source.name} -> {target.name}；"
                        f"关系={relation.type}；依据={relation.evidence or relation.note}"
                    ),
                )
            )
    if not links:
        report.skipped_relation_ids.append(relation.relation_id)
        report.warnings.append(
            f"{relation.relation_id} 只连接同一指标，不生成 CatalogLink"
        )
    return links


def project_to_catalog(kb: KnowledgeBase, base: Catalog, version: str) -> ProjectionResult:
    """返回一份新 Catalog，不修改 base。

    知识点被追加为 textbook Material。只有 verified 且能映射到不同指标的
    关系被翻译为 CatalogLink。候选、缺标注和同指标关系均不会进入正式 links。
    """
    report = ProjectionReport()
    known_indicators = {item.indicator_id for item in base.indicators}
    materials = list(base.materials)
    material_ids = {item.material_id for item in materials}

    for point in kb.points:
        unknown = sorted(set(point.indicator_ids) - known_indicators)
        if unknown:
            report.warnings.append(
                f"{point.kp_id} 引用了不存在的指标：{unknown}"
            )
            continue
        material = _knowledge_material(point, base)
        if material.material_id in material_ids:
            report.warnings.append(f"跳过重复材料：{material.material_id}")
            continue
        materials.append(material)
        material_ids.add(material.material_id)

    links = list(base.links)
    link_keys = {
        (item.source_id, item.target_id, item.relation, item.basis) for item in links
    }
    for relation in kb.relations:
        if relation.verification_status == "candidate":
            report.candidate_relation_ids.append(relation.relation_id)
            continue
        if relation.verification_status == "rejected":
            report.skipped_relation_ids.append(relation.relation_id)
            continue
        for link in _relation_links(relation, kb, report):
            key = (link.source_id, link.target_id, link.relation, link.basis)
            if key not in link_keys:
                links.append(link)
                link_keys.add(key)

    catalog = Catalog(
        version=version,
        topics=list(base.topics),
        indicators=list(base.indicators),
        materials=materials,
        links=links,
    )
    return ProjectionResult(catalog=catalog, report=report)
