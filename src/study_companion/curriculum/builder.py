"""Create an unverified catalog from the uploaded course and material tables."""

import re

from study_companion.contracts import (
    Catalog,
    DomainError,
    Indicator,
    Material,
    RawTables,
    RubricPoint,
    SourceLocator,
    Topic,
)
from study_companion.curriculum.course_sources import CourseReview


def _required(row: dict, field: str, table: str) -> str:
    value = (row.get(field) or "").strip()
    if not value:
        raise DomainError("UNPROCESSABLE", f"{table}缺少{field}")
    return value


def _ids(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"[、,，;；\s]+", value) if part.strip()]


def parse_rubric(value: str) -> list[RubricPoint]:
    points = []
    for part in re.split(r"[；;\n]+", value):
        match = re.fullmatch(r"\s*(\S+)\s+(.+?)\s+(\d+)\s*分\s*", part)
        if match:
            key, description, maximum = match.groups()
            points.append(RubricPoint(point_id=key, description=description, max_score=int(maximum)))
    return points


def parse_rubric_scores(value: str, points: list[RubricPoint]) -> dict[str, int]:
    maximums = {p.point_id: p.max_score for p in points}
    scores = {}
    for part in re.split(r"[；;\n]+", value):
        match = re.fullmatch(r"\s*([^\s=＝]+)\s*[=＝]\s*(\d+)\s*", part)
        if not match:
            return {}
        key, raw_score = match.groups()
        score = int(raw_score)
        if key in scores or key not in maximums or score > maximums[key]:
            return {}
        scores[key] = score
    return scores


def build_base_catalog(tables: RawTables, version: str, review: CourseReview | None = None) -> Catalog:
    review = review or CourseReview()
    topics: dict[str, Topic] = {}
    indicators: dict[str, Indicator] = {}
    for row in tables.requirements:
        topic_id = _required(row, "主题编号", "课程要求")
        indicator_id = _required(row, "指标编号", "课程要求")
        topic = Topic(topic_id=topic_id, title=_required(row, "主题", "课程要求"), goal=row.get("主题目标") or "")
        indicator = Indicator(
            indicator_id=indicator_id,
            topic_id=topic_id,
            description=_required(row, "观察指标", "课程要求"),
            curriculum_ref=row.get("课标整理编号") or "",
            curriculum_page=row.get("课标文件页／正文页") or "",
            textbook_pages=row.get("教材正文页") or "",
        )
        indicator.textbook_refs = review.textbook_refs.get(indicator_id, [])
        if topic_id in topics and topics[topic_id] != topic:
            raise DomainError("UNPROCESSABLE", "课程要求中同一主题编号对应不同名称")
        if indicator_id in indicators and indicators[indicator_id] != indicator:
            raise DomainError("UNPROCESSABLE", "课程要求中同一指标编号对应不同内容")
        topics[topic_id] = topic
        indicators[indicator_id] = indicator

    types = {
        "题目": "question",
        "练习": "question",
        "学习卡": "card",
        "教材": "textbook",
    }
    materials = []
    seen: set[str] = set()
    for row in tables.materials:
        material_id = _required(row, "材料编号", "题目与材料")
        if material_id in seen:
            raise DomainError("UNPROCESSABLE", "题目与材料中存在重复材料编号")
        seen.add(material_id)
        source_type = _required(row, "材料类型", "题目与材料")
        material_type = types.get(source_type)
        if material_type is None:
            raise DomainError("UNPROCESSABLE", f"不支持的材料类型：{source_type}")
        topic_ids = _ids(row.get("主题编号") or "")
        indicator_ids = _ids(row.get("指标编号") or "")
        if not set(topic_ids).issubset(topics) or not set(indicator_ids).issubset(
            indicators
        ):
            raise DomainError(
                "UNPROCESSABLE", f"材料 {material_id} 引用了不存在的主题或指标"
            )
        raw_score = (row.get("满分") or "").strip()
        try:
            max_score = int(raw_score) if raw_score else None
        except ValueError as exc:
            raise DomainError(
                "UNPROCESSABLE", f"材料 {material_id} 的满分格式无效"
            ) from exc
        materials.append(
            Material(
                material_id=material_id,
                material_type=material_type,
                topic_ids=topic_ids,
                indicator_ids=indicator_ids,
                content=_required(row, "题干或内容", "题目与材料"),
                options=[option.strip() for option in (row.get("选项") or "").splitlines() if option.strip()],
                source_locator=SourceLocator(
                    source_file=(row.get("来源或资料文件") or "题目与材料.csv"),
                    verification_status="unverified",
                ),
                reference_answer=row.get("参考答案") or None,
                reference_explanation=row.get("参考解析") or None,
                max_score=max_score,
                rubric_points=parse_rubric(row.get("评分点") or ""),
            )
        )
    return Catalog(
        version=version,
        topics=list(topics.values()),
        indicators=list(indicators.values()),
        materials=materials,
        links=[link for link in review.links if link.source_id in indicators and link.target_id in indicators],
    )
