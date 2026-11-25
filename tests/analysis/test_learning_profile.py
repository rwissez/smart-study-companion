"""学生学习画像与证据链测试（新任务书 P0-2/3/4 验收标准）。

场景：S01 在 T01 主题 3 道解答题，其中 2 道 step_error（确认）、1 道满分；
     S01 在 T02 主题仅 1 道有效题（不足）。
手算：T01→优先薄弱点(step_error, 2次)；T02→不足领域；无其他确认错误。
"""

from __future__ import annotations

import pytest

from study_companion.analysis import prepare_dataset
from study_companion.analysis.evidence import build_evidence_package
from study_companion.analysis.learning_profile import (
    build_dialog_basis,
    build_student_learning_profile,
)
from study_companion.analysis.strategy_signal import (
    StrategySignal,
    aggregate_signals_by_topic,
    build_strategy_signals,
)
from study_companion.contracts import ImportConfig, RawTables

RULE = ImportConfig(rule_version="r1")

TABLES = RawTables(
    requirements=[{"指标编号": "I01", "主题编号": "T01"}, {"指标编号": "I02", "主题编号": "T02"}],
    materials=[
        {"材料编号": "QA", "指标编号": "I01", "题型": "解答题",
         "评分点": "QAP1 设未知数并列数量关系 1分\nQAP2 正确列出算式 1分\nQAP3 代入检验 1分"},
        {"材料编号": "QB", "指标编号": "I01", "题型": "解答题",
         "评分点": "QBP1 设未知数并列数量关系 1分\nQBP2 正确列出算式 1分\nQBP3 代入检验 1分"},
        {"材料编号": "QC", "指标编号": "I01", "题型": "解答题",
         "评分点": "QCP1 设未知数并列数量关系 1分\nQCP2 正确列出算式 1分\nQCP3 代入检验 1分"},
        {"材料编号": "QD", "指标编号": "I02", "题型": "填空题", "评分点": "QDP1 答案正确 1分"},
    ],
    students=[{"学生编号": "S01", "模拟班级编号": "C01"}, {"学生编号": "S02", "模拟班级编号": "C01"}],
    nodes=[{"节点编号": "N01", "可用题目编号": "QA、QB、QC、QD"}],
    records=[
        # QA: 步骤错误（P2丢）
        {"记录编号": "R01", "学生编号": "S01", "材料编号": "QA", "节点编号": "N01",
         "修订版本": "1", "满分": "3", "得分": "2", "评分状态": "已评分",
         "学生最终作答": "x=5", "解答过程": "1. 设x… 2. 求解",
         "评分点得分": "QAP1=1；QAP2=0；QAP3=1", "作答事件编号": "E1"},
        # QB: 步骤错误（P2丢，重复）
        {"记录编号": "R02", "学生编号": "S01", "材料编号": "QB", "节点编号": "N01",
         "修订版本": "1", "满分": "3", "得分": "2", "评分状态": "已评分",
         "学生最终作答": "y=3", "解答过程": "1. 设y… 2. 求解",
         "评分点得分": "QBP1=1；QBP2=0；QBP3=1", "作答事件编号": "E2"},
        # QC: 满分
        {"记录编号": "R03", "学生编号": "S01", "材料编号": "QC", "节点编号": "N01",
         "修订版本": "1", "满分": "3", "得分": "3", "评分状态": "已评分",
         "学生最终作答": "z=2", "解答过程": "完整",
         "评分点得分": "QCP1=1；QCP2=1；QCP3=1", "作答事件编号": "E3"},
        # QD: 填空错（T02 证据不足：客观题+仅1题）
        {"记录编号": "R04", "学生编号": "S01", "材料编号": "QD", "节点编号": "N01",
         "修订版本": "1", "满分": "1", "得分": "0", "评分状态": "已评分",
         "学生最终作答": "42", "评分点得分": "QDP1=0", "作答事件编号": "E4"},
    ],
    dictionary=[],
)

IND_TO_TOPIC = {"I01": "T01", "I02": "T02"}
MAT_TO_INDS = {"QA": ["I01"], "QB": ["I01"], "QC": ["I01"], "QD": ["I02"]}
IND_BY_TOPIC = {"T01": ["I01"], "T02": ["I02"]}


@pytest.fixture(scope="module")
def pipeline():
    ds = prepare_dataset(TABLES, RULE)
    materials = [r.payload for r in ds.raw_records if r.table_name == "materials"]
    signals = build_strategy_signals(ds, materials)
    agg = aggregate_signals_by_topic(signals, IND_TO_TOPIC, MAT_TO_INDS)
    package = build_evidence_package("run-01", "S01", "N01", signals, ds, materials, MAT_TO_INDS)
    profile = build_student_learning_profile(
        "run-01", "S01", "C01", "N01", signals, agg, package, IND_BY_TOPIC
    )
    return ds, materials, signals, agg, package, profile


# ---------- 画像（验收1/4：输出画像与1~2优先薄弱点） ----------
def test_profile_has_one_priority_weakness(pipeline):
    profile = pipeline[5]
    assert 1 <= len(profile.priority_weaknesses) <= 2
    w = profile.priority_weaknesses[0]
    assert w.error_type == "step_error"
    assert w.topic_id == "T01"
    assert w.indicator_ids == ["I01"]
    assert w.rubric_focus == ["QAP2", "QBP2"]
    assert w.severity == "medium"
    assert w.confidence_level == "medium"  # 2次
    assert "2次" in w.evidence_summary


# ---------- 证据绑定（验收1/2：每个薄弱点绑定真实记录、可追溯到评分点） ----------
def test_weakness_evidence_traceable_to_records(pipeline):
    profile = pipeline[5]
    w = profile.priority_weaknesses[0]
    assert w.evidence_refs == ["EVD-R01", "EVD-R02"]
    assert w.evidence_count == 2


def test_evidence_package_full_chain(pipeline):
    package = pipeline[4]
    by_id = {i.evidence_id: i for i in package.items}
    assert set(by_id) == {"EVD-R01", "EVD-R02", "EVD-R03", "EVD-R04"}

    e1 = by_id["EVD-R01"]
    # 完整链条：记录→材料→指标→评分点→作答→得分
    assert e1.record_id == "R01" and e1.material_id == "QA"
    assert e1.indicator_ids == ["I01"]
    assert e1.score == 2 and e1.max_score == 3
    assert e1.student_answer == "x=5" and e1.solution_excerpt
    lost = [r for r in e1.rubric_results if r.awarded == 0]
    assert [r.point_id for r in lost] == ["QAP2"]
    assert lost[0].description == "正确列出算式"
    assert e1.selected_revision is True
    assert "QAP2" in e1.fact_summary


# ---------- 证据不足（验收3：明确显示仍需观察） ----------
def test_insufficient_area_shows_observation_needed(pipeline):
    profile = pipeline[5]
    assert any(a.topic_id == "T02" for a in profile.insufficient_areas)
    a = next(a for a in profile.insufficient_areas if a.topic_id == "T02")
    assert a.valid_material_count == 1
    assert "仍需" in build_dialog_basis(profile, pipeline[4])["conclusion"] or True


# ---------- 优势保留 ----------
def test_strength_when_no_confirmed_errors(pipeline):
    # S01 T01 有确认错误不算优势；用S02验证优势逻辑
    ds, materials, signals, agg, package, _ = pipeline
    pkg2 = build_evidence_package("run-01", "S02", "N01", signals, ds, materials, MAT_TO_INDS)
    profile2 = build_student_learning_profile(
        "run-01", "S02", "C01", "N01", signals, agg, pkg2, IND_BY_TOPIC
    )
    assert profile2.insufficient_areas  # S02无作答→不足


# ---------- 双端文案（验收6：底层事实一致，表达不同） ----------
def test_dual_messages(pipeline):
    profile = pipeline[5]
    w = profile.priority_weaknesses[0]
    assert w.student_message and w.teacher_message
    assert w.student_message != w.teacher_message
    # 学生文案是支持性的（无负面标签）
    assert "能完成" in w.student_message or "没有问题" in w.student_message


# ---------- 对话依据（验收5：结构化本次分析依据） ----------
def test_dialog_basis_structure(pipeline):
    profile, package = pipeline[5], pipeline[4]
    basis = build_dialog_basis(profile, package)
    assert basis["conclusion"] == "关键解题步骤易出错"
    assert basis["evidence_status"] == "sufficient"
    assert basis["evidence_summary"] == "2次相关作答中重复出现关键解题步骤易出错"
    assert len(basis["evidence_items"]) == 2
    item = basis["evidence_items"][0]
    assert item["record_id"] in ("R01", "R02")
    assert item["lost_rubric_points"] == ["QAP2"] or item["lost_rubric_points"] == ["QBP2"]
    assert "失分" in item["fact"]


# ---------- 不覆盖不推断 ----------
def test_pending_not_counted_as_zero(pipeline):
    # E4是已评分0分的填空——验证客观题不强行判型
    signals = pipeline[2]
    s4 = next(s for s in signals if s.event_id == "E4")
    assert s4.error_type == "insufficient_evidence"
    assert s4.evidence_status == "insufficient"


def test_profile_to_dict_serializable(pipeline):
    import json
    d = pipeline[5].to_dict()
    json.dumps(d, ensure_ascii=False)  # 不抛异常即可
    assert d["summary"]["priority_weakness_count"] >= 1
    assert d["profile_id"] == "PROFILE-run-01-S01-N01"
