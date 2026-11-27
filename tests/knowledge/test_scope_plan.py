"""章节切分与范围计划（02 §6.1）：标题规则、页码保留、术语匹配与 unknown 的保守判定。"""

from __future__ import annotations

from study_companion.knowledge.pdf_reader import PageText, PdfDocument
from study_companion.knowledge.scope_plan import (
    Chapter,
    ScopeDecision,
    ScopePlan,
    detect_heading_kind,
    is_structural_heading,
    plan_scope,
    split_chapters,
)

TOPICS = ["有理数"]
INDICATORS = ["能进行有理数的加减运算", "7.1.2 正数和负数"]


def _page(number: int, text: str, quality: str = "usable") -> PageText:
    return PageText(
        page=number,
        text=text,
        char_count=sum(1 for char in text if not char.isspace()),
        quality=quality,
    )


def _document() -> PdfDocument:
    pages = [
        _page(
            1,
            "第一章 有理数\n"
            "1.1 正数和负数\n"
            "像3、1.5这样的数叫做正数。\n"
            "有理数是整数和分数的统称。",
        ),
        _page(2, "1.2 有理数的加减法\n同号两数相加，取相同的符号，并把绝对值相加。"),
        _page(3, "第二章 整式的加减\n单项式中的数字因数叫做这个单项式的系数。"),
        _page(4, "第三章 复习与回顾\n复习前面学过的加减法。"),
        _page(5, "附录 参考答案\n1. A 2. B"),
        _page(6, "", quality="image_only"),
    ]
    return PdfDocument(file_id="file-scope", page_count=6, pages=pages, limitations=[])


def test_split_chapters_keeps_heading_page_and_page_range() -> None:
    chapters = split_chapters(_document())

    assert [chapter.chapter_id for chapter in chapters] == [
        "ch-001",
        "ch-002",
        "ch-003",
        "ch-004",
        "ch-005",
    ]
    assert [chapter.title for chapter in chapters] == [
        "第一章 有理数",
        "1.2 有理数的加减法",
        "第二章 整式的加减",
        "第三章 复习与回顾",
        "附录 参考答案",
    ]
    assert [(chapter.start_page, chapter.end_page) for chapter in chapters] == [
        (1, 1),
        (2, 2),
        (3, 3),
        (4, 4),
        (5, 6),
    ]
    # 标题行保留来源页；最后两页的无文本层页不产生正文，但页范围保留。
    assert "第一章 有理数" in chapters[0].text
    assert chapters[-1].page_spans == [(5, 0, len("附录 参考答案\n1. A 2. B"))]
    assert chapters[-1].text == "附录 参考答案\n1. A 2. B"


def test_chapter_text_excludes_unusable_pages() -> None:
    document = _document()
    document.pages[0].quality = "garbled"
    chapters = split_chapters(document)

    # 第 1 页不可用：第一章没有正文，但章节仍按标题存在，供人工复核。
    assert chapters[0].text == ""
    assert chapters[0].start_page == 1


def test_split_chapters_without_headings_falls_back_to_single_chapter() -> None:
    document = PdfDocument(
        file_id="file-flat",
        page_count=2,
        pages=[
            _page(1, "正文一开始就是一段说明文字，没有章节标题，也没有编号。"),
            _page(2, "第二页继续写正文内容，仍然没有章节标题。"),
        ],
        limitations=[],
    )
    chapters = split_chapters(document)
    assert len(chapters) == 1
    assert chapters[0].title == "全文（未识别章节标题）"
    assert chapters[0].start_page == 1
    assert chapters[0].end_page == 2
    assert "正文一开始" in chapters[0].text


def test_split_chapters_returns_empty_when_no_usable_text() -> None:
    document = PdfDocument(
        file_id="file-scan",
        page_count=2,
        pages=[_page(1, "", "image_only"), _page(2, "", "image_only")],
        limitations=["第 1 页无文本层", "第 2 页无文本层"],
    )
    assert split_chapters(document) == []

    plan = plan_scope([], topics=TOPICS, indicators=INDICATORS)
    assert plan.chapters == []
    assert plan.decisions == []
    assert any("未切分出任何章节" in item for item in plan.limitations)


def test_plan_scope_matches_topics_indicators_and_curriculum_codes() -> None:
    plan = plan_scope(split_chapters(_document()), topics=TOPICS, indicators=INDICATORS)
    roles = {decision.chapter_id: decision.role for decision in plan.decisions}

    assert roles == {
        "ch-001": "core",
        "ch-002": "core",
        "ch-003": "unknown",
        "ch-004": "prerequisite",
        "ch-005": "irrelevant",
    }
    first = plan.decisions[0]
    assert "有理数" in first.matched_terms
    assert "命中主题/指标/课标编号术语" in first.reason
    assert "有理数" in first.reason


def test_plan_scope_does_not_mark_unmatched_chapter_irrelevant() -> None:
    chapters = [
        Chapter(
            chapter_id="ch-001",
            title="第二章 整式的加减",
            start_page=3,
            end_page=3,
            text="单项式中的数字因数叫做这个单项式的系数。",
        )
    ]
    plan = plan_scope(chapters, topics=TOPICS, indicators=INDICATORS)
    decision = plan.decisions[0]

    assert decision.role == "unknown"
    assert decision.matched_terms == []
    assert "字面不匹配不等于无关" in decision.reason
    assert any("未命中主题或指标术语" in item for item in plan.limitations)


def test_plan_scope_uses_out_of_scope_terms_only_as_an_explicit_marker() -> None:
    chapters = split_chapters(_document())
    plan = plan_scope(chapters, topics=TOPICS, indicators=INDICATORS)
    appendix = plan.decisions[-1]

    assert appendix.role == "irrelevant"
    assert "附录" in appendix.matched_terms
    assert "超范围" in appendix.reason
    assert "不是质量失败" in appendix.reason


def test_plan_scope_prerequisite_reason_names_the_matched_terms() -> None:
    plan = plan_scope(split_chapters(_document()), topics=TOPICS, indicators=INDICATORS)
    review = next(item for item in plan.decisions if item.chapter_id == "ch-004")

    assert review.role == "prerequisite"
    assert "复习" in review.matched_terms
    assert "仅命中先修术语" in review.reason
    assert "需教师确认" in review.reason


def test_plan_scope_without_terms_keeps_everything_unknown() -> None:
    plan = plan_scope(split_chapters(_document()), topics=[], indicators=[])

    assert {decision.role for decision in plan.decisions} == {"unknown"}
    assert all(decision.matched_terms == [] for decision in plan.decisions)
    assert any("未提供主题或指标术语" in item for item in plan.limitations)


def test_plan_scope_marks_chapter_without_text_as_unknown() -> None:
    chapters = [
        Chapter(
            chapter_id="ch-001",
            title="第二章 整式的加减",
            start_page=1,
            end_page=1,
            text="",
        )
    ]
    plan = plan_scope(chapters, topics=TOPICS, indicators=INDICATORS)

    assert plan.decisions[0].role == "unknown"
    assert "没有可用文本层页面" in plan.decisions[0].reason


def test_heading_helpers_distinguish_structural_and_short_titles() -> None:
    assert detect_heading_kind("第一章 有理数") == "chapter"
    assert detect_heading_kind("第二节 绝对值") == "section"
    assert detect_heading_kind("1.2 有理数的加减法") == "numbered"
    assert detect_heading_kind("绝对值") == "short"
    assert detect_heading_kind("有理数是整数和分数的统称。") is None

    assert is_structural_heading("1.2 有理数的加减法") is True
    assert is_structural_heading("第一章 有理数") is True
    assert is_structural_heading("绝对值") is False

def test_page_stats_count_relevant_and_usable_pages():
    from study_companion.knowledge.scope_plan import page_stats

    class Page:
        def __init__(self, number, quality):
            self.page = number
            self.quality = quality

    class Document:
        page_count = 5
        pages = [Page(1, "usable"), Page(2, "sparse"), Page(3, "usable"), Page(4, "usable")]

    plan = ScopePlan(
        chapters=[
            Chapter("c1", "第一章", 1, 2, "x"),
            Chapter("c2", "第二章", 3, 4, "y"),
        ],
        decisions=[
            ScopeDecision("c1", "core", "命中", ["主题"]),
            ScopeDecision("c2", "unknown", "未命中", []),
        ],
        limitations=[],
    )
    stats = page_stats(Document(), plan)
    assert stats["total_pages"] == 5
    assert stats["relevant_pages"] == 2
    assert stats["usable_relevant_pages"] == 1
    assert stats["unusable_relevant_pages"] == 1
