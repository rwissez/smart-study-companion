"""本机课程出处配置（可选）。

真实教材页码与课标出处属于受控本地资料，不进入 Git。配置文件缺失时返回空
配置，目录构建继续可用，只是没有已核验出处。
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from study_companion.contracts import CatalogLink, DomainError, SourceLocator


class CourseReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    textbook_refs: dict[str, list[SourceLocator]] = Field(default_factory=dict)
    links: list[CatalogLink] = Field(default_factory=list)


def read_course_review(path: Path) -> CourseReview:
    if not path.is_file():
        return CourseReview()
    try:
        return CourseReview.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValidationError, OSError, UnicodeError) as exc:
        raise DomainError("UNPROCESSABLE", "本机课程出处配置无法读取，请核对格式。") from exc
