"""A 教材语义提取：切块、校验、独立复核与泄题拦截（02 §6）。"""

from __future__ import annotations

from dataclasses import dataclass

from study_companion.knowledge.textbook_model import (
    ModelPoint,
    ModelPointBatch,
    PointDecision,
    PointReview,
    ScopeScreen,
    PointSource,
    build_chunks,
    extract_points_with_model,
)


@dataclass
class Page:
    page: int
    text: str
    quality: str = "usable"


@dataclass
class Document:
    pages: list[Page]
    page_count: int


@dataclass
class Chapter:
    chapter_id: str
    title: str
    start_page: int
    end_page: int
    text: str
    page_spans: list = None  # type: ignore[assignment]


@dataclass
class Decision:
    chapter_id: str
    role: str
    reason: str = ""
    matched_terms: list = None  # type: ignore[assignment]


@dataclass
class Scope:
    chapters: list[Chapter]
    decisions: list[Decision]
    limitations: list[str]


@dataclass
class Indicator:
    indicator_id: str
    topic_id: str
    description: str


@dataclass
class Catalog:
    indicators: list[Indicator]
    materials: list = None  # type: ignore[assignment]


@dataclass
class Result:
    succeeded: bool
    value: object
    error: dict | None = None


class FakeService:
    configured = True

    def __init__(self, points: list[ModelPoint], decisions: list[PointDecision]) -> None:
        self.points = points
        self.decisions = decisions
        self.calls: list[str] = []

    def generate_structured(self, request, schema):  # noqa: ANN001
        self.calls.append(request.task_key)
        if request.task_key.endswith("screen_scope.v1"):
            return Result(True, ScopeScreen(decisions=[]))
        if request.task_key.endswith("extract_points.v1"):
            return Result(True, ModelPointBatch(points=self.points))
        return Result(True, PointReview(decisions=self.decisions))


def _scope() -> Scope:
    return Scope(
        chapters=[Chapter("ch-1", "一元一次方程", 1, 4, "方程正文", [])],
        decisions=[Decision("ch-1", "core")],
        limitations=[],
    )


def _document() -> Document:
    pages = [Page(number, f"第{number}页 等式两边同加同减同一个数仍相等。") for number in range(1, 6)]
    return Document(pages=pages, page_count=5)


def _catalog() -> Catalog:
    return Catalog(
        indicators=[Indicator("T01I1", "T01", "运用等式性质解一元一次方程")],
        materials=[],
    )


def _point(page: int = 2, name: str = "等式性质") -> ModelPoint:
    return ModelPoint(
        local_key="p1",
        name=name,
        definition="等式两边同加同减同一个数，等式仍成立。",
        kind="principle",
        scope_role="core",
        indicator_candidate_ids=["T01I1"],
        topic_candidate_ids=["T01"],
        sources=[PointSource(file_id="f1", pdf_page=page, quote="等式两边同加同减同一个数仍相等")],
        reason="教材正文定义",
    )


def test_chunks_split_by_pages() -> None:
    chunks = build_chunks(_document(), _scope())
    assert len(chunks) == 1
    assert chunks[0].start_page == 1 and chunks[0].end_page == 4


def test_validated_candidate_only_after_review_pass() -> None:
    service = FakeService([_point()], [PointDecision(index=0, decision="pass")])
    points, stats, notes = extract_points_with_model(
        service=service,  # type: ignore[arg-type]
        document=_document(),
        scope=_scope(),
        catalog=_catalog(),
        protected_texts=[],
    )
    assert len(points) == 1
    assert points[0].review_status == "validated"
    assert points[0].indicator_ids == ["T01I1"]
    assert stats["validated"] == 1
    assert service.calls == ["a.screen_scope.v1", "a.extract_points.v1", "a.review_point.v1"]


def test_review_uncertain_keeps_candidate_pending() -> None:
    service = FakeService(
        [_point()], [PointDecision(index=0, decision="uncertain", reason="原文不足以判断")]
    )
    points, stats, notes = extract_points_with_model(
        service=service,  # type: ignore[arg-type]
        document=_document(),
        scope=_scope(),
        catalog=_catalog(),
        protected_texts=[],
    )
    assert points[0].review_status == "needs_review"
    assert stats["validated"] == 0
    assert any("未通过复核" in item for item in notes)


def test_page_outside_chunk_is_dropped() -> None:
    service = FakeService([_point(page=9)], [])
    points, stats, notes = extract_points_with_model(
        service=service,  # type: ignore[arg-type]
        document=_document(),
        scope=_scope(),
        catalog=_catalog(),
        protected_texts=[],
    )
    assert points == []
    assert any("页码不在块内" in item for item in notes)


def test_answer_text_in_point_is_dropped() -> None:
    service = FakeService([_point()], [])
    points, stats, notes = extract_points_with_model(
        service=service,  # type: ignore[arg-type]
        document=_document(),
        scope=_scope(),
        catalog=_catalog(),
        protected_texts=["等式两边同加同减同一个数，等式仍成立。"],
    )
    assert points == []
    assert any("题目原文" in item for item in notes)


def test_unmapped_point_stays_pending() -> None:
    point = _point()
    point.indicator_candidate_ids = []
    point.topic_candidate_ids = []
    service = FakeService([point], [PointDecision(index=0, decision="pass")])
    points, stats, notes = extract_points_with_model(
        service=service,  # type: ignore[arg-type]
        document=_document(),
        scope=_scope(),
        catalog=_catalog(),
        protected_texts=[],
    )
    assert points[0].review_status == "needs_review"
    assert any("未关联课程范围" in item for item in notes)


def test_duplicate_name_is_skipped() -> None:
    service = FakeService([_point(), _point(page=3)], [PointDecision(index=0, decision="pass")])
    points, stats, notes = extract_points_with_model(
        service=service,  # type: ignore[arg-type]
        document=_document(),
        scope=_scope(),
        catalog=_catalog(),
        protected_texts=[],
    )
    assert len(points) == 1
