"""学习观察接入与跨运行变化测试（新任务书 P1-1/P1-2 验收标准）。

观察事件按 A 的 LearningObservation 契约构造（四类事件 + hint_level + payload.correct）。
"""

from __future__ import annotations

from study_companion.analysis import (
    compare_student_profiles,
    get_previous_observations,
    rerun_profile_with_observations,
    translate_observations,
)
from study_companion.analysis.learning_profile import (
    InsufficientArea,
    StudentLearningProfile,
    Weakness,
)
from study_companion.contracts import LearningObservation


def obs(oid, event_type, seq=1, activity="A1", hint=None, correct=None, run="run-01", student="S01", node="N01"):
    payload = {} if correct is None else {"correct": correct}
    return LearningObservation(
        observation_id=oid, dataset_id="ds", run_id=run, student_id=student,
        node_id=node, activity_id=activity, material_id=None,
        event_type=event_type, sequence=seq, hint_level=hint,
        payload=payload, observed_at="2026-11-25T10:00:00+08:00", source="student",
    )


# ---------- 观察查询（P1-1：get_previous_observations） ----------
def test_get_previous_observations_filters():
    all_obs = [
        obs("O1", "activity_completed", 1, student="S01"),
        obs("O2", "activity_completed", 1, student="S02"),
        obs("O3", "hint_requested", 2, student="S01", node="N02"),
    ]
    mine = get_previous_observations(all_obs, "S01")
    assert [o.observation_id for o in mine] == ["O1", "O3"]
    assert [o.observation_id for o in get_previous_observations(all_obs, "S01", node_id="N02")] == ["O3"]
    assert get_previous_observations(all_obs, "S03") == []


# ---------- 六种模式转译 ----------
def test_translate_six_patterns():
    cases = [
        # 独立完成（无提示）
        ([obs("O1", "activity_completed", 1, activity="A1")], "positive_practice"),
        # 一级提示后完成
        ([obs("O1", "hint_requested", 1, activity="A2", hint=1),
          obs("O2", "activity_completed", 2, activity="A2")], "supported_success"),
        # 三级提示后完成 → 不能判独立掌握
        ([obs("O1", "hint_requested", 1, activity="A3", hint=1),
          obs("O2", "hint_requested", 2, activity="A3", hint=3),
          obs("O3", "activity_completed", 3, activity="A3")], "heavily_prompted"),
        # 提交但未正确
        ([obs("O1", "answer_submitted", 1, activity="A4", correct=False)], "persistent_difficulty"),
        # 教师介入
        ([obs("O1", "teacher_review", 1, activity="A5")], "teacher_intervention"),
        # 无正确性信息
        ([obs("O1", "answer_submitted", 1, activity="A6")], "neutral"),
    ]
    for events, expected in cases:
        signals = translate_observations(events)
        assert signals[0].pattern == expected, f"{events[0].observation_id} -> {signals[0].pattern} != {expected}"


def test_heavily_prompted_limitation():
    signals = translate_observations([
        obs("O1", "hint_requested", 1, activity="A3", hint=2),
        obs("O2", "activity_completed", 2, activity="A3"),
    ])
    assert "提示后正确不等同于独立掌握" in signals[0].limitations


# ---------- 重新分析（新run_id + 来源分离 + 不覆盖） ----------
def test_rerun_generates_new_run_and_source_separation():
    observations = [obs("O1", "activity_completed", 1, activity="A1"),
                    obs("O2", "hint_requested", 2, activity="A2", hint=1)]
    result = rerun_profile_with_observations("run-01", observations, "S01", "N01")
    assert result["run_id"] == "run-01-obs2"
    assert result["run_id"] != result["previous_run_id"]
    assert result["source_separation"]["original_scores"] == "read-only, from official records"
    assert len(result["observation_signals"]) == 2
    # 每条信号 source=observation，不混入官方成绩
    assert all(s["source"] == "observation" for s in result["observation_signals"])


# ---------- 跨运行变化（P1-2） ----------
def make_profile(run_id, weaknesses, student="S01", node="N01"):
    return StudentLearningProfile(
        profile_id=f"P-{run_id}", run_id=run_id, student_id=student,
        class_id="C01", node_id=node, strengths=[],
        priority_weaknesses=weaknesses, other_findings=[],
        insufficient_areas=[], limitations=[],
        generated_at="2026-11-25T00:00:00+00:00",
    )


def make_weakness(wid, topic, etype, count):
    return Weakness(
        weakness_id=wid, topic_id=topic, indicator_ids=["I01"],
        title="t", error_stage="key_step", error_type=etype,
        rubric_focus=["Q1P2"], severity="medium", priority=1,
        confidence_level="medium", evidence_status="sufficient",
        evidence_count=count, evidence_refs=[f"EVD-R{i}" for i in range(count)],
        evidence_summary=f"{count}次", teacher_message="", student_message="",
    )


def test_comparison_comparable_rules():
    prev = make_profile("run-01", [make_weakness("W01", "T01", "step_error", 3)])
    curr = make_profile("run-02", [make_weakness("W01", "T01", "step_error", 1)])
    cmp = compare_student_profiles(prev, curr, new_observation_count=2)
    assert cmp.comparable
    assert len(cmp.improved_weaknesses) == 1
    assert "新观察证据支持" in cmp.improved_weaknesses[0].change_summary


def test_no_new_evidence_no_improvement():
    # 重复次数下降但无新观察 → 不判改善（防虚假变化）
    prev = make_profile("run-01", [make_weakness("W01", "T01", "step_error", 3)])
    curr = make_profile("run-02", [make_weakness("W01", "T01", "step_error", 1)])
    cmp = compare_student_profiles(prev, curr, new_observation_count=0)
    assert cmp.improved_weaknesses == []
    assert len(cmp.persistent_weaknesses) == 1
    assert "不判定为改善" in cmp.persistent_weaknesses[0].change_summary


def test_comparison_detects_new_and_persistent():
    prev = make_profile("run-01", [make_weakness("W01", "T01", "step_error", 2)])
    curr = make_profile("run-02", [
        make_weakness("W01", "T01", "step_error", 2),   # 持续
        make_weakness("W02", "T03", "concept_error", 2),  # 新增
    ])
    cmp = compare_student_profiles(prev, curr, new_observation_count=1)
    assert [w.error_type for w in cmp.persistent_weaknesses] == ["step_error"]
    assert [w.error_type for w in cmp.new_weaknesses] == ["concept_error"]


def test_comparison_incomparable_different_scope():
    prev = make_profile("run-01", [], node="N01")
    curr = make_profile("run-02", [], node="N02")  # 不同节点
    cmp = compare_student_profiles(prev, curr)
    assert not cmp.comparable
    assert "不可直接比较" in cmp.comparison_limitations[0]


def test_disappeared_weakness_not_resolved_without_evidence():
    prev = make_profile("run-01", [make_weakness("W01", "T01", "step_error", 2)])
    curr = make_profile("run-02", [])
    cmp = compare_student_profiles(prev, curr, new_observation_count=0)
    assert cmp.evidence_changes == []
    assert any("不宣布已解决" in l for l in cmp.comparison_limitations)
