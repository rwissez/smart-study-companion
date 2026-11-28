"""课程、题库、评分点与数学表达契约（01 §4、02 §3）。"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from study_companion.contracts.base import Contract, DifficultyLevel
from study_companion.contracts.common import MathText, SourceLocator


class Topic(Contract):
    topic_id: str
    title: str
    #: 主题目标，旧数据未提供时为空文本（01 §4）。
    goal: str = ""


class Indicator(Contract):
    indicator_id: str
    topic_id: str
    description: str
    curriculum_ref: str = ""
    curriculum_page: str = ""
    textbook_pages: str = ""
    textbook_refs: list[SourceLocator] = Field(default_factory=list)


class RubricPoint(Contract):
    point_id: str
    description: str
    max_score: int


class DifficultyAssessment(Contract):
    """难度评估；没有可靠评估时整体为 null，不默认中等。"""

    level: DifficultyLevel
    method: Literal["rule", "model", "teacher"]
    status: Literal["unverified", "verified"]
    reason: str
    evidence_refs: list[dict] = Field(default_factory=list)
    rule_version: str


class Material(Contract):
    material_id: str
    material_type: Literal["question", "card", "textbook"]
    topic_ids: list[str]
    indicator_ids: list[str]
    content: str
    source_locator: SourceLocator
    reference_answer: str | None = None
    reference_explanation: str | None = None
    max_score: int | None = None
    rubric_points: list[RubricPoint] = Field(default_factory=list)
    reviewed_variant_tag: str | None = None
    #: 题目选项每行一个；学习卡/教材为空数组。
    options: list[str] = Field(default_factory=list)
    #: 新题必填，非题 null。
    question_type: Literal["choice", "fill_blank", "solution"] | None = None
    difficulty: DifficultyAssessment | None = None
    #: 赛事新编等非文件来源标记。
    source_label: str | None = None
    #: 数学表达（01 §4）：原始文本永远保留。
    content_math: MathText | None = None
    answer_math: MathText | None = None
    explanation_math: MathText | None = None
    option_math: list[MathText] = Field(default_factory=list)
    #: 稳定展示顺序，导出与前端排序用；不是业务 ID。
    display_order: int | None = None


class CatalogLink(Contract):
    source_id: str
    target_id: str
    relation: Literal["supports", "prerequisite"]
    basis: Literal["standard", "textbook", "team_inference"]
    description: str


class Catalog(Contract):
    version: str
    topics: list[Topic]
    indicators: list[Indicator]
    materials: list[Material]
    links: list[CatalogLink]
    knowledge_version: str | None = None
    created_at: str | None = None


class MaterialSearch(Contract):
    indicator_ids: list[str] = Field(default_factory=list)
    material_types: list[Literal["question", "card", "textbook"]] = Field(default_factory=list)
    limit: int = Field(default=20, ge=1)


class MaterialView(Contract):
    """材料安全投影：学生视图不含答案、解析、评分细则。"""

    material_id: str
    material_type: Literal["question", "card", "textbook"]
    catalog_version: str
    topic_ids: list[str] = Field(default_factory=list)
    indicator_ids: list[str] = Field(default_factory=list)
    content: str
    options: list[str] = Field(default_factory=list)
    question_type: Literal["choice", "fill_blank", "solution"] | None = None
    source_label: str | None = None
    source_locator: SourceLocator | None = None
    max_score: int | None = None
    difficulty_level: DifficultyLevel | None = None
    difficulty_status: Literal["unverified", "verified"] | None = None
    display_order: int | None = None
    #: 教师视图特有：仅在授权角色投影中出现。
    reference_answer: str | None = None
    reference_explanation: str | None = None
    rubric_points: list[RubricPoint] = Field(default_factory=list)
    reviewed_variant_tag: str | None = None
