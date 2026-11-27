"""定义句候选、去重与增量 ChangeSet（02 §6.2—6.3、§11）。"""

from __future__ import annotations

from dataclasses import replace

from study_companion.knowledge.candidates import (
    MAX_DEFINITION_CHARS,
    MAX_SNIPPET_CHARS,
    SIMILARITY_THRESHOLD,
    KnowledgeCandidate,
    change_set,
    dedupe_candidates,
    definition_similarity,
    extract_definition_candidates,
)
from study_companion.knowledge.pdf_reader import PageText, PdfDocument
from study_companion.knowledge.scope_plan import (
    Chapter,
    ScopePlan,
    plan_scope,
    split_chapters,
)

PAGE_ONE = (
    "第一章 有理数\n"
    "1.1 正数和负数\n"
    "在数学中，像3、1.5这样的数叫做正数。\n"
    "有理数是整数和分数的统称。\n"
    "把这样的两个数叫做互为相反数。\n"
    "若两个数之和为0，则这两个数互为相反数。"
)
PAGE_TWO = (
    "1.2 有理数的加减法\n"
    "有理数是整数和分数的统称。\n"
    "正整数和负整数统称为整数。"
)


def _page(number: int, text: str, quality: str = "usable") -> PageText:
    return PageText(
        page=number,
        text=text,
        char_count=sum(1 for char in text if not char.isspace()),
        quality=quality,
    )


def _plan() -> ScopePlan:
    document = PdfDocument(
        file_id="file-cand",
        page_count=3,
        pages=[_page(1, PAGE_ONE), _page(2, PAGE_TWO), _page(3, "", "image_only")],
        limitations=[],
    )
    return plan_scope(
        split_chapters(document),
        topics=["有理数"],
        indicators=["能进行有理数的加减运算"],
    )


def _chapter(title: str, text: str) -> Chapter:
    return Chapter(chapter_id="ch-001", title=title, start_page=1, end_page=1, text=text)


def _cand(
    candidate_id: str,
    name: str,
    definition: str,
    *,
    page: int = 1,
    start: int = 0,
    end: int = 10,
    chapter_id: str = "ch-001",
    method: str = "rule:def_copula",
    review_status: str = "needs_review",
    source_pages: list[int] | None = None,
) -> KnowledgeCandidate:
    return KnowledgeCandidate(
        candidate_id=candidate_id,
        name=name,
        definition=definition,
        snippet=definition[:MAX_SNIPPET_CHARS],
        chapter_id=chapter_id,
        page=page,
        char_start=start,
        char_end=end,
        method=method,
        review_status=review_status,
        content_hash=f"hash-{candidate_id}",
        source_pages=list(source_pages or [page]),
    )


def test_extracts_the_expected_definition_patterns() -> None:
    candidates = extract_definition_candidates(_plan())
    names = [candidate.name for candidate in candidates]

    assert names == ["正数", "有理数", "互为相反数", "相反数", "有理数", "整数"]
    by_name: dict[str, KnowledgeCandidate] = {}
    for candidate in candidates:
        by_name.setdefault(candidate.name, candidate)

    assert by_name["正数"].method == "rule:def_called"
    assert by_name["正数"].page == 1
    assert by_name["有理数"].method == "rule:def_copula"
    assert by_name["互为相反数"].method == "rule:def_called"
    assert by_name["相反数"].method == "rule:def_conditional"
    assert by_name["整数"].method == "rule:def_collective"


def test_candidates_carry_review_status_hash_snippet_and_source_location() -> None:
    plan = _plan()
    chapter_by_id = {chapter.chapter_id: chapter for chapter in plan.chapters}

    for candidate in extract_definition_candidates(plan):
        assert candidate.review_status == "needs_review"
        assert len(candidate.content_hash) == 64
        assert len(candidate.snippet) <= MAX_SNIPPET_CHARS
        assert candidate.snippet == candidate.definition[:MAX_SNIPPET_CHARS]
        assert candidate.page >= 1
        assert candidate.source_pages == [candidate.page]

        chapter = chapter_by_id[candidate.chapter_id]
        original = " ".join(chapter.text[candidate.char_start : candidate.char_end].split())
        assert original == candidate.definition


def test_char_offsets_map_to_the_page_the_sentence_starts_on() -> None:
    # 第 2 页只有一句定义，候选页码必须是 2，而不是章节起始页 1。
    candidates = extract_definition_candidates(_plan())
    page_two = [item for item in candidates if item.page == 2]
    assert [item.name for item in page_two] == ["有理数", "整数"]


def test_definition_sentence_with_trailing_clause_still_matches() -> None:
    text = "把只有符号不同的两个数叫做互为相反数，它们在数轴上位于原点两侧。"
    plan = ScopePlan(
        chapters=[_chapter("第一章 有理数", text)],
        decisions=[],
        limitations=[],
    )
    candidates = extract_definition_candidates(plan)

    assert [candidate.name for candidate in candidates] == ["互为相反数"]
    assert candidates[0].definition == text


def test_rejects_over_long_names_and_definitions() -> None:
    long_name = (
        "有理数整数分数正数负数零绝对值相反数数轴概念"
        "是数学中的基本概念。"
    )
    long_definition = "绝对值是" + "很" * (MAX_DEFINITION_CHARS + 40) + "。"
    plan = ScopePlan(
        chapters=[_chapter("第一章 有理数", long_name + long_definition)],
        decisions=[],
        limitations=[],
    )

    assert extract_definition_candidates(plan) == []


def test_dedupe_merges_same_name_and_keeps_both_source_pages() -> None:
    candidates = extract_definition_candidates(_plan())
    kept, reports = dedupe_candidates(candidates)

    rational = [item for item in kept if item.name == "有理数"]
    assert len(rational) == 1
    assert rational[0].source_pages == [1, 2]
    assert rational[0].page == 1

    merged = [item for item in reports if item["action"] == "merged"]
    assert len(merged) == 1
    assert merged[0]["pages"] == [1, 2]
    assert merged[0]["similarity"] == 1.0
    assert merged[0]["basis"] == "same_name"
    assert merged[0]["reason"]
    assert len(kept) == len(candidates) - 1


def test_dedupe_merges_near_identical_definitions_with_different_names() -> None:
    definition = "等式两边加上同一个数，结果仍相等。"
    left = _cand("kc-a", "等式的性质", definition, start=0, end=20)
    right = _cand("kc-b", "等式性质", definition.rstrip("。") + "。", start=30, end=50)

    assert definition_similarity(left.definition, right.definition) >= SIMILARITY_THRESHOLD
    kept, reports = dedupe_candidates([left, right])

    assert [item.candidate_id for item in kept] == ["kc-a"]
    assert reports[0]["basis"] == "definition_similarity"
    assert reports[0]["similarity"] >= SIMILARITY_THRESHOLD


def test_dedupe_keeps_same_name_with_different_definition_as_conflict() -> None:
    left = _cand("kc-a", "绝对值", "绝对值是数轴上一个数到原点的距离。", start=0, end=20)
    right = _cand("kc-b", "绝对值", "绝对值恒为非负数。", start=40, end=60)

    kept, reports = dedupe_candidates([left, right])

    assert [item.candidate_id for item in kept] == ["kc-a", "kc-b"]
    assert reports[0]["action"] == "conflict"
    assert reports[0]["pages"] == [1]
    assert "同名但定义域、条件不同者分开" in reports[0]["reason"]
    assert reports[0]["similarity"] < SIMILARITY_THRESHOLD


def test_change_set_reports_added_removed_renamed_and_changed() -> None:
    previous = [
        _cand("kc-a", "有理数", "有理数是整数和分数的统称。", start=0, end=20),
        _cand("kc-b", "整数", "整数是正整数、零和负整数的统称。", start=30, end=50),
        _cand("kc-d", "数轴", "数轴是规定了原点、正方向和单位长度的直线。", start=60, end=80),
    ]
    current = [
        # 同一位置改名 → renamed
        _cand("kc-a2", "有理数概念", "有理数是整数和分数的统称。", start=0, end=20),
        # 同一位置改定义 → changed
        _cand("kc-b2", "整数", "整数包括正整数、零和负整数。", start=30, end=50),
        # 新出现 → added
        _cand("kc-c", "相反数", "互为相反数的两个数之和为0。", start=90, end=110),
    ]

    result = change_set(previous, current)

    assert [item["candidate_id"] for item in result["renamed"]] == ["kc-a2"]
    assert result["renamed"][0]["from"] == "有理数"
    assert result["renamed"][0]["to"] == "有理数概念"
    assert result["renamed"][0]["definition_changed"] is False

    assert [item["candidate_id"] for item in result["changed"]] == ["kc-b2"]
    assert result["changed"][0]["fields"] == ["definition"]
    assert "definition" in result["changed"][0]["reason"]

    assert [item["candidate_id"] for item in result["added"]] == ["kc-c"]
    assert "新候选" in result["added"][0]["reason"]

    assert [item["candidate_id"] for item in result["removed"]] == ["kc-d"]
    assert "候选消失" in result["removed"][0]["reason"]


def test_change_set_is_empty_for_identical_lists() -> None:
    candidates = extract_definition_candidates(_plan())
    result = change_set(candidates, candidates)
    assert result == {"added": [], "removed": [], "renamed": [], "changed": []}


def test_change_set_detects_review_and_location_updates() -> None:
    old = _cand("kc-a", "有理数", "有理数是整数和分数的统称。", start=0, end=20, page=1)
    moved = replace(old, page=2, char_start=40, char_end=60)
    approved = replace(old, review_status="validated")

    moved_result = change_set([old], [moved])
    assert moved_result["changed"][0]["fields"] == ["page", "char_span"]
    assert moved_result["renamed"] == []
    assert moved_result["added"] == []
    assert moved_result["removed"] == []

    status_result = change_set([old], [approved])
    assert status_result["changed"][0]["fields"] == ["review_status"]
