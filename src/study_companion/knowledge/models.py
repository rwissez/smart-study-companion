"""知识点库的数据模型。

四种关系的定义（用户确认，18号文档）：
- has_property 从属：一个概念具有的性质，这些性质从属于该概念。
- derives_from 衍生：同一知识点在不同条件下，某个性质发生了变化。
- composed_of  合并创造：两个及以上知识点组合推导出新的性质或概念。
- special_case 特例：特殊的现象、公理，无法用一般形式举例的情形。

存储方向统一为 source_ids -> target_id：
- has_property:  source=概念,        target=性质
- derives_from:  source=原知识点,    target=条件变化后的知识点
- composed_of:   source=多个来源,    target=新性质/新概念
- special_case:  source=相关一般概念, target=特例/公理
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_EVIDENCE_LENGTH = 160
MAX_SNIPPET_LENGTH = 80

RelationType = Literal["has_property", "derives_from", "composed_of", "special_case"]
Verification = Literal["unverified", "verified", "rejected"]
Basis = Literal["standard", "textbook", "team_inference"]
Proposer = Literal["human", "rule", "agent"]
KpKind = Literal["concept", "property", "theorem", "method", "unknown"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Locator(Model):
    """位置信息。只存位置和短摘，不存大段原文（合规）。"""

    source_file: str
    file_page: int
    printed_page: int | None = None
    chapter: str = ""
    section: str = ""
    verification_status: Verification = "unverified"


class KnowledgePoint(Model):
    kp_id: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    kind: KpKind = "unknown"
    definition_locator: Locator
    snippet: str = Field(default="", max_length=MAX_SNIPPET_LENGTH)  # 定义句的短摘，供核对
    indicator_ids: list[str] = Field(default_factory=list)
    proposer: Proposer = "rule"
    verification_status: Verification = "unverified"
    reviewed_by: str | None = None
    reviewed_at: str | None = None
    topic_ids: list[str] = Field(default_factory=list)
    source_locators: list[Locator] = Field(default_factory=list)
    definition_fingerprint: str | None = None


class Relation(Model):
    relation_id: str
    type: RelationType
    source_ids: list[str]
    target_id: str
    basis: Basis = "textbook"
    evidence: str = Field(default="", max_length=MAX_EVIDENCE_LENGTH)
    evidence_file_page: int | None = None
    source_file: str | None = None
    note: str = ""
    proposer: Proposer = "human"
    verification_status: Literal["candidate", "verified", "rejected"] = "candidate"
    reviewed_by: str | None = None
    reviewed_at: str | None = None

    @model_validator(mode="after")
    def _shape(self) -> "Relation":
        if self.type == "composed_of":
            if len(set(self.source_ids)) < 2:
                raise ValueError("composed_of 的来源知识点必须至少 2 个")
        elif len(self.source_ids) != 1:
            raise ValueError(f"{self.type} 的来源知识点必须恰好 1 个")
        if self.target_id in self.source_ids:
            raise ValueError("关系的两端不能是同一个知识点")
        return self


class Annotation(Model):
    """Legacy material annotation or a versioned source-to-target candidate."""

    material_id: str | None = None
    kp_ids: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    proposer: Proposer = "human"
    verification_status: Verification = "unverified"
    candidate_id: str | None = None
    source_type: Literal["knowledge_point", "material", "rubric_point", "textbook_fragment"] | None = None
    source_id: str | None = None
    target_type: Literal["topic", "indicator", "knowledge_point"] | None = None
    target_ids: list[str] = Field(default_factory=list)
    evidence: str = Field(default="", max_length=MAX_EVIDENCE_LENGTH)
    source_file: str | None = None
    file_page: int | None = Field(default=None, ge=1)
    printed_page: str | None = None
    reason: str = Field(default="", max_length=240)
    reviewed_by: str | None = None
    reviewed_at: str | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> "Annotation":
        candidate_fields = (
            self.candidate_id, self.source_type, self.source_id, self.target_type,
        )
        is_candidate = any(value is not None for value in candidate_fields)
        if is_candidate:
            if any(value is None for value in candidate_fields) or not self.target_ids:
                raise ValueError("归属候选必须完整提供 candidate/source/target 类型与 ID")
            if self.source_type == "knowledge_point" and self.target_type == "knowledge_point":
                raise ValueError("知识点归属候选不能以 knowledge_point 同时作为来源与归属目标")
            if self.source_type != "knowledge_point" and self.target_type != "knowledge_point":
                raise ValueError("材料、评分点和片段只能归属到知识点")
            if self.verification_status == "verified" and self.proposer != "human":
                if not self.reviewed_by or not self.reviewed_at:
                    raise ValueError("自动生成的归属候选需要人工审核后才能 verified")
        elif self.material_id is None or not self.kp_ids:
            raise ValueError("旧式 Annotation 必须提供 material_id 和 kp_ids")
        return self


class MergeCandidate(Model):
    """An explicit equivalence proposal requiring human review."""

    candidate_id: str
    source_id: str
    target_id: str
    reason: str = Field(min_length=1, max_length=240)
    match_basis: list[Literal["name", "alias", "definition"]] = Field(default_factory=list)
    source_name: str = ""
    target_name: str = ""
    source_snippet: str = Field(default="", max_length=MAX_SNIPPET_LENGTH)
    target_snippet: str = Field(default="", max_length=MAX_SNIPPET_LENGTH)
    source_locator: Locator | None = None
    target_locator: Locator | None = None
    proposer: Proposer = "rule"
    verification_status: Verification = "unverified"
    reviewed_by: str | None = None
    reviewed_at: str | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> "MergeCandidate":
        if not self.source_id.strip() or not self.target_id.strip() or self.source_id == self.target_id:
            raise ValueError("Merge candidates require two distinct knowledge points")
        if self.verification_status == "verified" and (not self.reviewed_by or not self.reviewed_at):
            raise ValueError("Merge candidates require human review before verification")
        return self


class KnowledgeBase(Model):
    kb_version: str
    points: list[KnowledgePoint] = Field(default_factory=list)
    relations: list[Relation] = Field(default_factory=list)
    annotations: list[Annotation] = Field(default_factory=list)
    merge_candidates: list[MergeCandidate] = Field(default_factory=list)
    parent_version: str | None = None
    status: Literal["draft", "verified"] = "draft"
    created_at: str | None = None
    source_summary: dict[str, str | int | bool | None] = Field(default_factory=dict)
    change_summary: list[str] = Field(default_factory=list)
