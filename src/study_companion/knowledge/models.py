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
    snippet: str = Field(default="", max_length=80)  # 定义句的短摘，供核对
    indicator_ids: list[str] = Field(default_factory=list)
    proposer: Proposer = "rule"
    verification_status: Verification = "unverified"


class Relation(Model):
    relation_id: str
    type: RelationType
    source_ids: list[str]
    target_id: str
    basis: Basis = "textbook"
    evidence: str = ""  # 必须是教材原文中的一个子串，程序核对
    evidence_file_page: int | None = None
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
    """题目/学习卡/评分点 -> 知识点。"""

    material_id: str
    kp_ids: list[str]
    keywords: list[str] = Field(default_factory=list)
    proposer: Proposer = "human"
    verification_status: Verification = "unverified"


class KnowledgeBase(Model):
    kb_version: str
    points: list[KnowledgePoint] = Field(default_factory=list)
    relations: list[Relation] = Field(default_factory=list)
    annotations: list[Annotation] = Field(default_factory=list)
