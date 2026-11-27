from __future__ import annotations

from study_companion.sources import MaterialInput, TextbookPage, enrich_sources

CONTENT = "不等式两边加（或减）同一个数，不等号的方向不变。"


def _textbook(
    *,
    material_id: str = "B02",
    content: str = CONTENT,
    source_locator: dict | None = None,
) -> MaterialInput:
    return MaterialInput(
        material_id=material_id,
        material_type="textbook",
        content=content,
        source_locator=source_locator
        if source_locator is not None
        else {
            "source_file": "5_课程标准及相关教学资源/B02_相关资料切片.pdf",
            "verification_status": "unverified",
            "source_kind": "competition_material",
        },
        source_label="5_课程标准及相关教学资源/B02_相关资料切片.pdf",
    )


def test_page_written_only_on_exact_hit() -> None:
    pages = [
        TextbookPage(
            file_id="file-b02",
            pdf_page=3,
            text="性质1 不等式两边加（或减）同一个数，不等号的方向不变。",
            source_file="B02_相关资料切片.pdf",
        )
    ]
    report = enrich_sources(
        [_textbook()],
        textbook_pages=pages,
        file_ids={"B02_相关资料切片.pdf": "file-b02"},
    )

    item = report.items[0]
    assert item.verification_status == "verified"
    assert item.reason == "textbook_quote_found"
    assert item.file_id == "file-b02"
    assert item.pdf_page == 3
    assert item.page == 3
    assert item.quote == CONTENT
    assert item.source_row_number is None
    assert item.source_ref()["pdf_page"] == 3


def test_page_number_comes_from_hit_page_not_first_page() -> None:
    pages = [
        TextbookPage(file_id="file-b02", pdf_page=1, text="目录与封面"),
        TextbookPage(file_id="file-b02", pdf_page=2, text="第十一章 不等式与不等式组"),
        TextbookPage(
            file_id="file-b02",
            pdf_page=7,
            text="一般地，不等式有如下性质：" + CONTENT,
            source_file="B02_相关资料切片.pdf",
        ),
    ]
    report = enrich_sources(
        [_textbook()],
        textbook_pages=pages,
        file_ids={"B02_相关资料切片.pdf": "file-b02"},
    )

    item = report.items[0]
    assert item.verification_status == "verified"
    assert item.pdf_page == 7
    assert item.quote == CONTENT


def test_near_match_does_not_write_page() -> None:
    pages = [
        TextbookPage(
            file_id="file-b02",
            pdf_page=4,
            text="不等式两边加同一个数，不等号的方向不变。",
            source_file="B02_相关资料切片.pdf",
        )
    ]
    report = enrich_sources(
        [_textbook()],
        textbook_pages=pages,
        file_ids={"B02_相关资料切片.pdf": "file-b02"},
    )

    item = report.items[0]
    assert item.verification_status == "unverified"
    assert item.reason == "textbook_quote_not_found"
    assert item.pdf_page is None
    assert item.quote is None
    assert item.file_id == "file-b02"


def test_duplicate_page_hits_do_not_pick_a_page() -> None:
    pages = [
        TextbookPage(
            file_id="file-b02",
            pdf_page=2,
            text=CONTENT,
            source_file="B02_相关资料切片.pdf",
        ),
        TextbookPage(
            file_id="file-b02",
            pdf_page=9,
            text="复述：" + CONTENT,
            source_file="B02_相关资料切片.pdf",
        ),
    ]
    report = enrich_sources(
        [_textbook()],
        textbook_pages=pages,
        file_ids={"B02_相关资料切片.pdf": "file-b02"},
    )

    item = report.items[0]
    assert item.verification_status == "unverified"
    assert item.reason == "textbook_quote_ambiguous"
    assert item.pdf_page is None
    assert item.quote is None


def test_whitespace_only_difference_still_finds_quote() -> None:
    pages = [
        TextbookPage(
            file_id="file-b02",
            pdf_page=5,
            text="性质1 不等式两边加（或减）同一个数，\n不等号的方向不变。",
            source_file="B02_相关资料切片.pdf",
        )
    ]
    report = enrich_sources(
        [_textbook()],
        textbook_pages=pages,
        file_ids={"B02_相关资料切片.pdf": "file-b02"},
    )

    item = report.items[0]
    assert item.verification_status == "verified"
    assert item.pdf_page == 5
    assert item.quote is not None
    assert "不等号的方向不变" in item.quote
    assert "\n" in item.quote


def test_unregistered_file_is_unverified_without_page() -> None:
    report = enrich_sources(
        [_textbook()],
        textbook_pages=[],
        file_ids={},
    )

    item = report.items[0]
    assert item.verification_status == "unverified"
    assert item.reason == "textbook_file_not_registered"
    assert item.file_id is None
    assert item.pdf_page is None


def test_registered_file_without_text_is_unverified_without_page() -> None:
    report = enrich_sources(
        [_textbook()],
        textbook_pages=[],
        file_ids={"B02_相关资料切片.pdf": "file-b02"},
    )

    item = report.items[0]
    assert item.verification_status == "unverified"
    assert item.reason == "textbook_text_unavailable"
    assert item.file_id == "file-b02"
    assert item.pdf_page is None


def test_explicit_not_applicable_marks_toc_or_cover() -> None:
    report = enrich_sources(
        [_textbook(material_id="B00", content="目录")],
        textbook_pages=[],
        file_ids={"B02_相关资料切片.pdf": "file-b02"},
        not_applicable_material_ids={"B00"},
    )

    item = report.items[0]
    assert item.verification_status == "not_applicable"
    assert item.reason == "textbook_not_applicable"
    assert item.pdf_page is None
    assert item.quote is None


def test_chapter_label_is_not_verified_even_if_page_matches() -> None:
    material = _textbook(
        material_id="B01",
        content="七年级下册 第八章 实数",
        source_locator={
            "source_file": "B01_相关资料切片.pdf",
            "verification_status": "unverified",
            "source_kind": "competition_material",
        },
    )
    pages = [
        TextbookPage(
            file_id="file-b01",
            pdf_page=1,
            text="七年级下册 第八章 实数",
            source_file="B01_相关资料切片.pdf",
        )
    ]
    report = enrich_sources(
        [material],
        textbook_pages=pages,
        file_ids={"B01_相关资料切片.pdf": "file-b01"},
    )

    item = report.items[0]
    assert item.verification_status == "unverified"
    assert item.reason == "textbook_content_not_quotable"
    assert item.pdf_page is None
    assert item.quote is None


def test_existing_page_is_not_carried_into_unverified_result() -> None:
    material = _textbook(
        source_locator={
            "source_file": "5_课程标准及相关教学资源/B02_相关资料切片.pdf",
            "verification_status": "unverified",
            "source_kind": "competition_material",
            "file_id": "file-b02",
            "pdf_page": 19,
            "quote": "旧页码",
        }
    )
    report = enrich_sources(
        [material],
        textbook_pages=[
            TextbookPage(
                file_id="file-b02",
                pdf_page=3,
                text="完全不同的页面文本",
                source_file="B02_相关资料切片.pdf",
            )
        ],
        file_ids={"B02_相关资料切片.pdf": "file-b02"},
    )

    item = report.items[0]
    assert item.verification_status == "unverified"
    assert item.reason == "textbook_quote_not_found"
    assert item.pdf_page is None
    assert item.quote is None
