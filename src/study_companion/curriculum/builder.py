"""课程目录与基础题库构建（A 的输入层，不是正式知识体系）。

只做确定性映射与结构校验：课程要求 → 主题/指标，题目与材料 → Material（含选项、
题型、评分点）。知识体系（知识点/关系/关联）由 A 的构建服务产出，二者版本分开。
"""

from __future__ import annotations

import re

from study_companion.contracts import (
    Catalog,
    DifficultyAssessment,
    DomainError,
    Indicator,
    Material,
    RawTables,
    RubricPoint,
    SourceLocator,
    Topic,
)
from study_companion.curriculum.course_sources import CourseReview

QUESTION_TYPES = {
    "选择题": "choice",
    "填空题": "fill_blank",
    "解答题": "solution",
}

MATERIAL_TYPES = {
    "题目": "question",
    "练习": "question",
    "学习卡": "card",
    "教材": "textbook",
}


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
    maximums = {point.point_id: point.max_score for point in points}
    scores: dict[str, int] = {}
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


def _options(value: str | None) -> list[str]:
    if not value:
        return []
    return [line.strip() for line in value.splitlines() if line.strip()]


def build_base_catalog(
    tables: RawTables, version: str, review: CourseReview | None = None
) -> Catalog:
    review = review or CourseReview()
    topics: dict[str, Topic] = {}
    indicators: dict[str, Indicator] = {}
    for row in tables.requirements:
        topic_id = _required(row, "主题编号", "课程要求")
        indicator_id = _required(row, "指标编号", "课程要求")
        topic = Topic(
            topic_id=topic_id,
            title=_required(row, "主题", "课程要求"),
            goal=row.get("主题目标") or "",
        )
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

    materials: list[Material] = []
    seen: set[str] = set()
    for index, row in enumerate(tables.materials, start=1):
        material_id = _required(row, "材料编号", "题目与材料")
        if material_id in seen:
            raise DomainError("UNPROCESSABLE", f"题目与材料中材料编号重复：{material_id}")
        seen.add(material_id)
        source_type = _required(row, "材料类型", "题目与材料")
        material_type = MATERIAL_TYPES.get(source_type)
        if material_type is None:
            raise DomainError("UNPROCESSABLE", f"不支持的材料类型：{source_type}")
        topic_ids = _ids(row.get("主题编号") or "")
        indicator_ids = _ids(row.get("指标编号") or "")
        if not set(topic_ids).issubset(topics) or not set(indicator_ids).issubset(indicators):
            raise DomainError(
                "UNPROCESSABLE", f"材料 {material_id} 引用了不存在的主题或指标"
            )
        raw_score = (row.get("满分") or "").strip()
        try:
            max_score = int(raw_score) if raw_score else None
        except ValueError as exc:
            raise DomainError("UNPROCESSABLE", f"材料 {material_id} 的满分格式无效") from exc

        source_file = (row.get("来源或资料文件") or "").strip()
        locator = SourceLocator(
            source_file=source_file or "题目与材料",
            verification_status="unverified",
            source_kind="competition_material" if source_file else None,
        )
        question_type = None
        if material_type == "question":
            raw_type = (row.get("题型") or "").strip()
            question_type = QUESTION_TYPES.get(raw_type)
            if question_type is None:
                raise DomainError(
                    "UNPROCESSABLE", f"题目 {material_id} 的题型无法识别：{raw_type or '空'}"
                )
        materials.append(
            Material(
                material_id=material_id,
                material_type=material_type,
                topic_ids=topic_ids,
                indicator_ids=indicator_ids,
                content=_required(row, "题干或内容", "题目与材料"),
                options=_options(row.get("选项")),
                source_locator=locator,
                reference_answer=row.get("参考答案") or None,
                reference_explanation=row.get("参考解析") or None,
                max_score=max_score,
                rubric_points=parse_rubric(row.get("评分点") or ""),
                question_type=question_type,
                source_label=row.get("来源或资料文件") or None,
                display_order=index,
            )
        )
    return Catalog(
        version=version,
        topics=list(topics.values()),
        indicators=list(indicators.values()),
        materials=materials,
        links=[
            link
            for link in review.links
            if link.source_id in indicators and link.target_id in indicators
        ],
    )


def difficulty_of(material: Material) -> DifficultyAssessment | None:
    """目录构建阶段不生成难度：没有可靠评估就必须是 null（01 §4）。"""

    return material.difficulty
