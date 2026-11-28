"""A 质量报告口径：材料分类、来源可核验与空分母（02 §4—§12）。

覆盖四类回归：
* 58/54 场景：讲义、教材不再进入题目分子；
* 题目、学习卡（讲义）、教材分开统计；
* 未核验、无页码、没有定位的来源 SOURCE_RESOLVABLE 不再恒为 true；
* 正常 100% 边界与空分母返回 null 并写原因。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from study_companion.contracts import Catalog, Indicator, Material, SourceLocator, Topic
from study_companion.knowledge.service import KnowledgeConfig, build_knowledge
from study_companion.knowledge.summary import (
    coverage_item,
    material_mapping,
    source_resolvability,
)


@pytest.fixture(autouse=True)
def _no_model(monkeypatch: pytest.MonkeyPatch):
    """这些是规则构建测试，不读取本机模型配置。"""

    from study_companion.config import reset_settings_cache

    for name in ("MODEL_PROVIDER", "MODEL_BASE_URL", "MODEL_NAME", "MODEL_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    reset_settings_cache()
    yield
    reset_settings_cache()


def _locator(
    *, page: int | None = 12, status: str = "verified", source_file: str = "教材.pdf"
) -> SourceLocator:
    return SourceLocator(
        source_file=source_file,
        source_kind="textbook",
        pdf_page=page,
        verification_status=status,
    )


def _material(
    material_id: str, material_type: str, indicator_ids: list[str]
) -> Material:
    return Material(
        material_id=material_id,
        material_type=material_type,
        topic_ids=["T1"],
        indicator_ids=indicator_ids,
        content=f"{material_id} 的自拟内容",
        source_locator=SourceLocator(
            source_file="题目与材料.csv", verification_status="unverified"
        ),
    )


def _catalog(
    indicators: list[Indicator], materials: list[Material], version: str = "cat-v1"
) -> Catalog:
    return Catalog(
        version=version,
        topics=[Topic(topic_id="T1", title="测试主题")],
        indicators=indicators,
        materials=materials,
        links=[],
    )


def _build(catalog: Catalog, **kwargs):
    return build_knowledge(
        catalog,
        context=None,
        config=KnowledgeConfig(),
        dataset_id="ds-test",
        knowledge_version="kn-test",
        model_configured=False,
        **kwargs,
    )


def _coverage(summary: dict, key: str) -> dict:
    return next(item for item in summary["coverage"] if item["key"] == key)


def _source_check(result, kp_id: str):
    point = next(item for item in result.candidate_snapshot.points if item.kp_id == kp_id)
    check = next(
        item for item in point.review_info.checks if item.code == "SOURCE_RESOLVABLE"
    )
    return point, check


def test_58_of_54_scenario_counts_questions_cards_textbooks_separately() -> None:
    """正式目录 65 项：54 题全关联，3 张学习卡全关联，8 项教材只关联 B01。"""

    indicators = [Indicator(indicator_id="I1", topic_id="T1", description="观察指标一")]
    questions = [_material(f"Q{index:03d}", "question", ["I1"]) for index in range(1, 55)]
    cards = [_material(f"L0{index}", "card", ["I1"]) for index in range(1, 4)]
    textbooks = [
        _material("B01", "textbook", ["I1"]),
        *[_material(f"B{index:02d}", "textbook", ["I-unknown"]) for index in range(2, 9)],
    ]
    result = _build(_catalog(indicators, questions + cards + textbooks))
    summary = result.candidate_snapshot.review_summary
    counts = summary["counts"]

    assert counts["catalog_materials"] == 65
    assert counts["materials_with_annotation"] == 58
    assert (counts["question_total"], counts["question_with_annotation"]) == (54, 54)
    assert (counts["card_total"], counts["card_with_annotation"]) == (3, 3)
    assert (counts["textbook_total"], counts["textbook_with_annotation"]) == (8, 1)

    question = _coverage(summary, "question_mapping")
    assert (question["numerator"], question["denominator"]) == (54, 54)
    assert question["value"] == 1.0
    assert question["status"] == "computed"
    assert "题目关联率" in question["scope_description"]
    assert "学习卡（讲义）" in question["scope_description"]
    assert question["population"] == "question"

    # 旧口径会拿 58/54 得到 1.074；现在三类材料各自闭合。
    for key, expected in (("card_mapping", 1.0), ("textbook_mapping", 0.125)):
        item = _coverage(summary, key)
        assert item["value"] == pytest.approx(expected)
        assert item["population"] in {"card", "textbook"}
    assert all(
        item["value"] is None or 0.0 <= item["value"] <= 1.0
        for item in summary["coverage"]
    )
    assert summary["mapping_gaps"]["questions_without_annotation"] == []
    assert summary["mapping_gaps"]["textbooks_without_annotation"] == [
        "B02",
        "B03",
        "B04",
        "B05",
        "B06",
        "B07",
        "B08",
    ]


def test_question_card_textbook_all_full_is_exactly_100_percent() -> None:
    """正常边界：三类材料都 100% 关联时，各自等于 1.0，不出现 100% 以上。"""

    indicators = [Indicator(indicator_id="I1", topic_id="T1", description="观察指标一")]
    materials = [
        _material("Q001", "question", ["I1"]),
        _material("Q002", "question", ["I1"]),
        _material("L01", "card", ["I1"]),
        _material("B01", "textbook", ["I1"]),
    ]
    summary = _build(_catalog(indicators, materials)).candidate_snapshot.review_summary

    assert _coverage(summary, "question_mapping")["value"] == 1.0
    assert _coverage(summary, "card_mapping")["value"] == 1.0
    assert _coverage(summary, "textbook_mapping")["value"] == 1.0
    assert summary["counts"]["question_total"] == 2


def test_source_resolvable_false_for_unverified_missing_and_page_less_sources() -> None:
    """只有已核验且带页码的来源才算可解析；其余返回 false 并写原因。"""

    indicators = [
        Indicator(
            indicator_id="I-verified",
            topic_id="T1",
            description="有核验页码",
            textbook_refs=[_locator(page=12)],
        ),
        Indicator(
            indicator_id="I-unverified",
            topic_id="T1",
            description="来源未核验",
            textbook_refs=[_locator(page=20, status="unverified")],
        ),
        Indicator(
            indicator_id="I-nopage",
            topic_id="T1",
            description="已核验但无页码",
            textbook_refs=[_locator(page=None, status="verified")],
        ),
        Indicator(indicator_id="I-missing", topic_id="T1", description="没有登记来源"),
    ]
    result = _build(_catalog(indicators, []))
    summary = result.candidate_snapshot.review_summary

    verified_point, verified_check = _source_check(result, "kp-I-verified")
    assert verified_check.passed is True
    assert "第 12 页" in verified_check.reason
    assert verified_point.review_info.status == "validated"

    for kp_id, keyword in (
        ("kp-I-unverified", "未核验"),
        ("kp-I-nopage", "页码"),
        ("kp-I-missing", "未核验"),
    ):
        point, check = _source_check(result, kp_id)
        assert check.passed is False
        assert keyword in check.reason
        assert point.review_info.status == "needs_review"

    assert summary["counts"]["points_with_verified_source"] == 1
    assert summary["counts"]["points_without_verified_source"] == 3
    assert summary["mapping_gaps"]["points_without_verified_source"] == [
        "kp-I-missing",
        "kp-I-nopage",
        "kp-I-unverified",
    ]
    assert _coverage(summary, "point_sourcing")["value"] == pytest.approx(0.25)
    assert any("SOURCE_RESOLVABLE 记 false" in note for note in summary["limitations"])


def test_textbook_candidate_keeps_verified_page_source() -> None:
    """教材候选带真实页码时 SOURCE_RESOLVABLE 为 true，原因里能核对页码。"""

    from study_companion.knowledge.textbook_model import TextbookPoint

    candidate = TextbookPoint(
        candidate_id="c1",
        name="教材概念",
        definition="教材定义句",
        snippet="教材定义句",
        chapter_id="ch1",
        page=23,
        char_start=0,
        char_end=6,
        method="rule",
        review_status="validated",
        content_hash="hash-c1",
    )
    result = _build(_catalog([], []), textbook_candidates=[candidate])
    point, check = _source_check(result, "kp-c1")
    assert check.passed is True
    assert "第 23 页" in check.reason
    assert point.source_locators[0].verification_status == "verified"


def test_empty_denominator_returns_null_with_reason() -> None:
    """没有题目、指标或知识点时返回 null，不补 0 或 1。"""

    empty = _build(_catalog([], []))
    summary = empty.candidate_snapshot.review_summary
    for key in ("indicator_coverage", "question_mapping", "card_mapping", "textbook_mapping", "point_sourcing"):
        item = _coverage(summary, key)
        assert item["value"] is None
        assert item["status"] == "unknown"
        assert item["reason"]
        assert "为 0" in item["reason"]

    indicator_only = _build(
        _catalog([Indicator(indicator_id="I1", topic_id="T1", description="只有一个指标")], [])
    )
    only_summary = indicator_only.candidate_snapshot.review_summary
    question = _coverage(only_summary, "question_mapping")
    assert question["denominator"] == 0
    assert question["value"] is None
    assert "无法计算比率" in question["reason"]


def test_coverage_item_refuses_numerator_over_denominator() -> None:
    """防御性口径检查：58/54 这类输入不再产出 107.4%。"""

    item = coverage_item(
        key="question_mapping",
        label="题目知识关联率",
        population="question",
        numerator=58,
        denominator=54,
        numerator_label="有正式知识点关联的题目数",
        denominator_label="全部正式题目数",
        scope_description="题目关联率",
    )
    assert item["value"] is None
    assert item["status"] == "unknown"
    assert "大于分母" in item["reason"]


def test_material_mapping_does_not_mix_types_and_flags_unclassified() -> None:
    materials = [
        SimpleNamespace(material_id="Q1", material_type="question"),
        SimpleNamespace(material_id="Q2", material_type="question"),
        SimpleNamespace(material_id="L1", material_type="card"),
        SimpleNamespace(material_id="B1", material_type="textbook"),
    ]
    annotations = [
        SimpleNamespace(source_id="Q1", target_ids=["kp-1"]),
        SimpleNamespace(source_id="L1", target_ids=["kp-1"]),
        SimpleNamespace(source_id="B1", target_ids=["kp-1"]),
        SimpleNamespace(source_id="Q-ghost", target_ids=["kp-1"]),
        SimpleNamespace(source_id="Q2", target_ids=["kp-404"]),
    ]
    result = material_mapping(materials, annotations, {"kp-1"})
    assert result.totals == {"question": 2, "card": 1, "textbook": 1}
    assert result.mapped == {"question": 1, "card": 1, "textbook": 1}
    assert result.mapped_material_ids == {"Q1", "L1", "B1", "Q-ghost"}
    assert result.unclassified_material_ids == ["Q-ghost"]


def test_source_resolvability_states() -> None:
    assert source_resolvability([]).state == "missing"
    assert source_resolvability([_locator(status="unverified")]).state == "unverified"
    assert source_resolvability([_locator(page=None)]).state == "unverified"
    assert source_resolvability([_locator(page=0)]).state == "unverified"
    assert source_resolvability([_locator(page=7)]).state == "verified"
    printed = SourceLocator(
        source_file="教材.pdf",
        printed_page="第 7 页",
        verification_status="verified",
        source_kind="textbook",
    )
    assert source_resolvability([printed]).state == "verified"
