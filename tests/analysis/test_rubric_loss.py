"""班级评分点丢分率测试（B-L2，全部手算可核对）。

场景：C01班 3人（S01/S02/S03），N01 节点，材料 Q100 三评分点
P1(概念)/P2(步骤)/P3(检验)。S04 属 C02 班不参与。
作答：
  S01: P1=1 P2=0 P3=0   S02: P1=0 P2=0 P3=1   S03: P1=0 P2=0 P3=0
另有 S01 一条未作答（不进分母）、S02 一条超满分排除（不进分母）。
手算：
  P1: 观测3 丢2（S02,S03）率 2/3
  P2: 观测3 丢3 率 1.0  ← 共性问题（全丢）
  P3: 观测3 丢2（S01,S03）率 2/3
"""

from __future__ import annotations

from study_companion.analysis import (
    common_loss_issues,
    compute_class_rubric_losses,
    prepare_dataset,
)
from study_companion.contracts import ImportConfig, RawTables

RULE = ImportConfig(rule_version="r1")

TABLES = RawTables(
    requirements=[{"指标编号": "I01", "主题编号": "T01"}],
    materials=[{
        "材料编号": "Q100", "指标编号": "I01", "题型": "解答题",
        "评分点": "Q100P1 识别数量关系 1分\nQ100P2 正确列出算式 1分\nQ100P3 代入检验 1分",
    }],
    students=[
        {"学生编号": "S01", "模拟班级编号": "C01"},
        {"学生编号": "S02", "模拟班级编号": "C01"},
        {"学生编号": "S03", "模拟班级编号": "C01"},
        {"学生编号": "S04", "模拟班级编号": "C02"},
    ],
    nodes=[{"节点编号": "N01", "可用题目编号": "Q100"}],
    records=[
        {"记录编号": "R01", "学生编号": "S01", "材料编号": "Q100", "节点编号": "N01",
         "修订版本": "1", "满分": "3", "得分": "1", "评分状态": "已评分",
         "评分点得分": "Q100P1=1；Q100P2=0；Q100P3=0", "作答事件编号": "E1"},
        {"记录编号": "R02", "学生编号": "S02", "材料编号": "Q100", "节点编号": "N01",
         "修订版本": "1", "满分": "3", "得分": "1", "评分状态": "已评分",
         "评分点得分": "Q100P1=0；Q100P2=0；Q100P3=1", "作答事件编号": "E2"},
        {"记录编号": "R03", "学生编号": "S03", "材料编号": "Q100", "节点编号": "N01",
         "修订版本": "1", "满分": "3", "得分": "0", "评分状态": "已评分",
         "评分点得分": "Q100P1=0；Q100P2=0；Q100P3=0", "作答事件编号": "E3"},
        # 未作答：不进分母
        {"记录编号": "R04", "学生编号": "S01", "材料编号": "Q100", "节点编号": "N01",
         "修订版本": "1", "满分": "3", "评分状态": "未作答", "作答事件编号": "E4"},
        # 超满分排除：不进分母
        {"记录编号": "R05", "学生编号": "S02", "材料编号": "Q100", "节点编号": "N01",
         "修订版本": "1", "满分": "3", "得分": "9", "评分状态": "已评分",
         "评分点得分": "Q100P1=1；Q100P2=1；Q100P3=1", "作答事件编号": "E5"},
    ],
    dictionary=[],
)

CLASS_C01 = ["S01", "S02", "S03"]


def _losses():
    ds = prepare_dataset(TABLES, RULE)
    materials = [r.payload for r in ds.raw_records if r.table_name == "materials"]
    return compute_class_rubric_losses(ds, CLASS_C01, materials)


def test_point_level_stats_hand_calculated():
    losses = _losses()
    by_point = {r.point_id: r for r in losses}
    assert set(by_point) == {"Q100P1", "Q100P2", "Q100P3"}

    p1 = by_point["Q100P1"]
    assert (p1.observed_count, p1.lost_count) == (3, 2)
    assert p1.loss_rate == 2 / 3
    assert sorted(p1.lost_by_students) == ["S02", "S03"]
    assert p1.category == "concept"  # 识别数量关系

    p2 = by_point["Q100P2"]
    assert (p2.observed_count, p2.lost_count) == (3, 3)
    assert p2.loss_rate == 1.0
    assert p2.category == "step"

    p3 = by_point["Q100P3"]
    assert (p3.observed_count, p3.lost_count) == (3, 2)
    assert p3.category == "verification"


def test_excluded_and_unanswered_not_in_denominator():
    losses = _losses()
    # 未作答 E4 与超满分 E5 都不进分母：观测仍是3而非5
    for r in losses:
        assert r.observed_count == 3


def test_class_scoping():
    ds = prepare_dataset(TABLES, RULE)
    materials = [r.payload for r in ds.raw_records if r.table_name == "materials"]
    # C02 只有 S04（无作答）：无统计
    c02 = compute_class_rubric_losses(ds, ["S04"], materials)
    assert c02 == []
    # 空班级：空统计，不崩溃
    assert compute_class_rubric_losses(ds, [], materials) == []


def test_common_loss_issues_thresholds():
    losses = _losses()
    issues = common_loss_issues(losses, min_observed=2, min_rate=0.5)
    # P2 率1.0 全丢 → 共性；P1/P3 率2/3 >= 0.5 → 也是共性
    ids = [i["point_id"] for i in issues]
    assert "Q100P2" in ids
    # 提高门槛到 0.9：只剩 P2
    strict = common_loss_issues(losses, min_observed=2, min_rate=0.9)
    assert [i["point_id"] for i in strict] == ["Q100P2"]
    # 单次丢分不构成共性
    single = common_loss_issues(losses, min_observed=5, min_rate=0.5)
    assert single == []


def test_sorting_by_loss_rate():
    losses = _losses()
    rates = [r.loss_rate for r in losses]
    assert rates == sorted(rates, reverse=True)
