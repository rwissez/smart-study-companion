"""Optional local curriculum review data. Source tables are kept outside Git."""

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
