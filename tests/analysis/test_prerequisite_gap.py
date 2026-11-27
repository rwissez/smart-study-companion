"""前置知识缺口判断测试（新任务书 P1-3 验收：四态区分+只verified+不编造）。"""

from __future__ import annotations

import pytest

from study_companion.analysis import detect_prerequisite_gap, gap_summary_for_c
from study_companion.analysis.learning_profile import (
    StudentLearningProfile,
    Weakness,
)
from study_companion.knowledge.models import (
    KnowledgeBase,
    KnowledgePoint,
    Locator,
    Relation,
)


def kp(kp_id, name, indicators):
    return KnowledgePoint(
        kp_id=kp_id, name=name, kind="concept",
        definition_locator=Locator(source_file="t", file_page=1),
        indicator_ids=indicators,
    )


def rel(rid, source, target, status="verified"):
    return Relation(
        relation_id=rid, type="derives_from", source_ids=[source],
        target_id=target, basis="textbook", verification_status=status,
    )


# 知识库：T02 方程 K-T02 派生自 T01 代数式 K-T01（已核对）
#         T03 不等式 K-T03 前置 T01（candidate 未核对）
KB = KnowledgeBase(
    kb_version="kb-v1",
    points=[
        kp("K-T01", "代数式", ["T01I1", "T01I2"]),
        kp("K-T02", "一元一次方程", ["T02I1", "T02I2"]),
        kp("K-T03", "不等式", ["T03I1"]),
    ],
    relations=[
        rel("REL-T01-T02", "K-T01", "K-T02", status="verified"),
        rel("REL-T01-T03", "K-T01", "K-T03", status="candidate"),  # 未核对
    ],
)


def make_profile(run_id, weaknesses):
    return StudentLearningProfile(
        profile_id=f"P-{run_id}", run_id=run_id, student_id="S01",
        class_id="C01", node_id="N01", strengths=[],
        priority_weaknesses=weaknesses, other_findings=[],
        insufficient_areas=[], limitations=[],
        generated_at="2026-11-27T00:00:00+00:00",
    )


def make_weakness(wid, topic, etype="step_error", status="sufficient", count=2):
    return Weakness(
        weakness_id=wid, topic_id=topic, indicator_ids=[f"{topic}I1"],
        title="t", error_stage="key_step", error_type=etype,
        rubric_focus=["Q1P2"], severity="medium", priority=1,
        confidence_level="medium", evidence_status=status,
        evidence_count=count, evidence_refs=[f"EVD-R{i}" for i in range(count)],
        evidence_summary=f"{count}次", teacher_message="", student_message="",
    )


# ---------- 1. supported：前置有充分证据薄弱 ----------
def test_supported_with_prereq_weakness():
    profile = make_profile("run-02", [
        make_weakness("W01", "T02"),          # 当前：方程步骤错
        make_weakness("W02", "T01"),          # 前置：代数式有薄弱（充分证据）
    ])
    signals = detect_prerequisite_gap("run-02", "S01", "N01", "W01", KB, profile)
    assert any(s.status == "supported" for s in signals)
    s = next(s for s in signals if s.status == "supported")
    assert s.prerequisite_topic_id == "T01"
    assert s.relation_id == "REL-T01-T02"
    assert s.relation_review_status == "verified"
    assert s.prerequisite_weakness_refs == ["W02"]
    assert "不能证明唯一因果关系" in s.limitations[0]


# ---------- 2. possible：关系已核对但前置无证据 ----------
def test_possible_without_prereq_evidence():
    profile = make_profile("run-02", [make_weakness("W01", "T02")])  # 无前置薄弱
    signals = detect_prerequisite_gap("run-02", "S01", "N01", "W01", KB, profile)
    s = next(s for s in signals if s.prerequisite_topic_id == "T01")
    assert s.status == "possible"
    assert "无充分证据薄弱点" in s.reason


# ---------- 3. 红线：candidate 关系不用（T03 不等式前置是 candidate） ----------
def test_candidate_relation_not_used():
    profile = make_profile("run-02", [
        make_weakness("W01", "T03"),  # 当前：不等式
        make_weakness("W02", "T01"),  # 前置有薄弱
    ])
    signals = detect_prerequisite_gap("run-02", "S01", "N01", "W01", KB, profile)
    # T03 的前置关系是 candidate → 不产生 supported；K-T03 无 verified 前置 → not_supported
    assert not any(s.status == "supported" for s in signals)
    assert all(s.relation_id != "REL-T01-T03" for s in signals)


# ---------- 4. not_supported：无已核对前置关系 ----------
def test_not_supported_when_no_verified_relation():
    # T01 代数式无前置关系
    profile = make_profile("run-02", [make_weakness("W01", "T01")])
    signals = detect_prerequisite_gap("run-02", "S01", "N01", "W01", KB, profile)
    assert signals
    assert all(s.status == "not_supported" for s in signals)
    assert "知识点本身" in signals[0].reason


# ---------- 5. unknown：薄弱点不存在 / 知识库无映射 ----------
def test_unknown_when_weakness_missing():
    profile = make_profile("run-02", [make_weakness("W01", "T02")])
    signals = detect_prerequisite_gap("run-02", "S01", "N01", "W99", KB, profile)
    assert signals[0].status == "unknown"
    assert "不存在" in signals[0].reason


def test_unknown_when_topic_not_mapped():
    empty_kb = KnowledgeBase(kb_version="kb-v2", points=[], relations=[])
    profile = make_profile("run-02", [make_weakness("W01", "T02")])
    signals = detect_prerequisite_gap("run-02", "S01", "N01", "W01", empty_kb, profile)
    assert signals[0].status == "unknown"


# ---------- 6. 单次错误不推断：前置薄弱必须"充分证据" ----------
def test_insufficient_prereq_weakness_not_supported():
    # 前置薄弱点证据不足（insufficient）→ 不构成 supported
    profile = make_profile("run-02", [
        make_weakness("W01", "T02"),
        make_weakness("W02", "T01", status="insufficient"),
    ])
    signals = detect_prerequisite_gap("run-02", "S01", "N01", "W01", KB, profile)
    s = next(s for s in signals if s.prerequisite_topic_id == "T01")
    assert s.status == "possible"  # 前置证据不足 → possible，不判 supported


# ---------- 7. 历史画像参与证据池 ----------
def test_historical_profile_counts_as_prereq_evidence():
    old_profile = make_profile("run-01", [make_weakness("W01", "T01")])
    current = make_profile("run-02", [make_weakness("W02", "T02")])
    signals = detect_prerequisite_gap(
        "run-02", "S01", "N01", "W02", KB, current,
        historical_profiles=[old_profile],
    )
    s = next(s for s in signals if s.prerequisite_topic_id == "T01")
    assert s.status == "supported"
    assert "run:run-01" in s.evidence_refs  # 历史画像作为证据来源


# ---------- 8. 给C的精简衔接结构 ----------
def test_gap_summary_for_c():
    profile = make_profile("run-02", [
        make_weakness("W01", "T02"), make_weakness("W02", "T01"),
    ])
    signals = detect_prerequisite_gap("run-02", "S01", "N01", "W01", KB, profile)
    summary = gap_summary_for_c(signals)
    supported = next(s for s in summary if s["prerequisite_gap"]["status"] == "supported")
    assert supported["weakness_id"] == "W01"
    assert supported["prerequisite_gap"]["prerequisite_topic_id"] == "T01"
    assert supported["evidence_status"] == "sufficient"
