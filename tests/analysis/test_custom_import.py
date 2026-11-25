"""自建测试六表数据：编号体系不同、列顺序打乱、缺可选列、埋入真实数据陷阱。

用途（17号文档 B-P0）：
- 验证"用户上传自有数据"场景：非公开编号（X前缀学生、Y前缀材料）能完整跑通
- 作为数据陷阱测试的载体（供 test_custom_import.py 使用）
- 不含任何赛题原始数据，明确标记 synthetic=true

与赛题数据的差异点：
| 维度 | 赛题合成样例 | 本数据 |
|---|---|---|
| 学生编号 | S01.. | X-2024-001.. |
| 班级编号 | C01 | 高一(3)班 等真实式班名 |
| 材料编号 | Q01.. | Y-A-01.. |
| 列顺序 | 标准顺序 | 故意打乱 |
| 可选列 | 全齐 | nodes 缺"教学说明"、materials 缺"变式标注" |
| 陷阱 | 少量 | 密集埋入（见 TRAPS 清单） |

TRAPS 埋入清单（每项均可手算核对）：
T1 超满分：X-2024-001 在 VN-2 对 Y-B-02 得 10 分（满分1）
T2 评分点矛盾：X-2024-002 对 Y-C-01 得分3，评分点 JSON 之和为2
T3 越界题目：X-2024-003 在 VN-1 作答 Y-D-01，但该材料标注属于 VN-2
T4 同版冲突：X-2024-004 对 Y-A-01 两条 rev=1 记录分数不同
T5 修订链：X-2024-005 对 Y-A-01 rev1(2分)→rev2(待评分)，旧分不回退
T6 名册空学生：X-2024-008 在名册但无任何作答
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from study_companion.contracts import ImportConfig, RawTables
from study_companion.analysis import analyze_dataset, prepare_dataset
from study_companion.contracts import (
    AnalysisConfig,
    Catalog,
    Indicator,
    Material,
    RunContext,
    SourceLocator,
    Topic,
)

RULE = ImportConfig(rule_version="import-rules-v1", quarantine_invalid_scores=True)
RUN_CTX = RunContext(run_id="custom-run-1", dataset_id="custom-ds-1",
                     catalog_version="custom-cat-1", rule_version="rules-v1")
CONFIG = AnalysisConfig(min_valid_distinct_items=3)


def build_custom_tables() -> RawTables:
    """六表：列顺序与赛题不同、编号不同、缺可选列、埋6类陷阱。"""
    # students：列顺序打乱（班级在前），真实式班名，含年级列
    students = [
        {"年级": "高一", "模拟班级编号": "高一(3)班", "学生编号": "X-2024-001"},
        {"学生编号": "X-2024-002", "模拟班级编号": "高一(3)班"},
        {"学生编号": "X-2024-003", "模拟班级编号": "高一(4)班"},
        {"学生编号": "X-2024-004", "模拟班级编号": "高一(4)班"},
        {"学生编号": "X-2024-005", "模拟班级编号": "高一(3)班"},
        {"学生编号": "X-2024-008", "模拟班级编号": "高一(4)班"},  # T6 名册无作答
    ]
    # nodes：缺"教学说明"可选列，列名用"测验节点编号"别名，带可用题目集合（T3载体）
    nodes = [
        {"测验节点编号": "VN-1", "可用题目编号": "Y-A-01、Y-B-02、Y-C-01"},
        {"测验节点编号": "VN-2", "可用题目编号": "Y-B-02、Y-C-01、Y-D-01"},
    ]
    # materials：列倒序（指标在前），缺"变式标注"可选列
    materials = [
        {"指标编号": "IA-1", "材料编号": "Y-A-01", "主题编号": "TA-1"},
        {"指标编号": "IA-1", "材料编号": "Y-B-02", "主题编号": "TA-1"},
        {"指标编号": "IA-2", "材料编号": "Y-C-01", "主题编号": "TA-1"},
        {"指标编号": "IA-2", "材料编号": "Y-D-01", "主题编号": "TA-1"},  # T3 属VN-2
    ]
    requirements = [
        {"观察指标": "代入求值能力", "指标编号": "IA-1", "主题编号": "TA-1"},
        {"观察指标": "不等式求解", "指标编号": "IA-2", "主题编号": "TA-1"},
    ]
    # records：列顺序完全打乱（修订版本在最前），埋入全部陷阱
    records = [
        # 正常记录（VN-1）
        {"修订版本": "1", "记录编号": "XR-01", "材料编号": "Y-A-01", "节点编号": "VN-1",
         "学生编号": "X-2024-001", "满分": "2", "得分": "2", "评分状态": "已评分",
         "作答事件编号": "XE-01"},
        {"修订版本": "1", "记录编号": "XR-02", "材料编号": "Y-A-01", "节点编号": "VN-1",
         "学生编号": "X-2024-002", "满分": "2", "得分": "1", "评分状态": "已评分",
         "作答事件编号": "XE-02"},
        # T1 超满分：满分1得10
        {"修订版本": "1", "记录编号": "XR-03", "材料编号": "Y-B-02", "节点编号": "VN-2",
         "学生编号": "X-2024-001", "满分": "1", "得分": "10", "评分状态": "已评分",
         "作答事件编号": "XE-03"},
        # T2 评分点之和与得分不符：得分3，评分点 1+1=2（JSON格式）
        {"修订版本": "1", "记录编号": "XR-04", "材料编号": "Y-C-01", "节点编号": "VN-2",
         "学生编号": "X-2024-002", "满分": "3", "得分": "3", "评分状态": "已评分",
         "评分点得分": "{\"P1\": 1, \"P2\": 1}", "作答事件编号": "XE-04"},
        # T2b 真实赛题格式：Q格式分号对，之和1与得分1一致（不误报）
        {"修订版本": "1", "记录编号": "XR-12", "材料编号": "Y-B-02", "节点编号": "VN-2",
         "学生编号": "X-2024-001", "满分": "1", "得分": "1", "评分状态": "已评分",
         "评分点得分": "Y-B-02P1=1；Y-B-02P2=0", "作答事件编号": "XE-10"},
        # T3 越界：Y-D-01 标注属 VN-2，但作答发生在 VN-1
        {"修订版本": "1", "记录编号": "XR-05", "材料编号": "Y-D-01", "节点编号": "VN-1",
         "学生编号": "X-2024-003", "满分": "2", "得分": "2", "评分状态": "已评分",
         "作答事件编号": "XE-05"},
        # T4 同版冲突：两条 rev=1 分数不同
        {"修订版本": "1", "记录编号": "XR-06", "材料编号": "Y-A-01", "节点编号": "VN-1",
         "学生编号": "X-2024-004", "满分": "2", "得分": "0", "评分状态": "已评分",
         "作答事件编号": "XE-06"},
        {"修订版本": "1", "记录编号": "XR-07", "材料编号": "Y-A-01", "节点编号": "VN-1",
         "学生编号": "X-2024-004", "满分": "2", "得分": "2", "评分状态": "已评分",
         "作答事件编号": "XE-06"},
        # T5 修订链：rev1 有分，rev2 待评分（旧分不得回退进入统计）
        {"修订版本": "1", "记录编号": "XR-08", "材料编号": "Y-A-01", "节点编号": "VN-1",
         "学生编号": "X-2024-005", "满分": "2", "得分": "2", "评分状态": "已评分",
         "作答事件编号": "XE-07"},
        {"修订版本": "2", "记录编号": "XR-09", "材料编号": "Y-A-01", "节点编号": "VN-1",
         "学生编号": "X-2024-005", "满分": "2", "评分状态": "待评分",
         "作答事件编号": "XE-07"},
        # 正常待评分 + 未作答（对账口径覆盖）
        {"修订版本": "1", "记录编号": "XR-10", "材料编号": "Y-B-02", "节点编号": "VN-2",
         "学生编号": "X-2024-003", "满分": "1", "评分状态": "待评分",
         "作答事件编号": "XE-08"},
        {"修订版本": "1", "记录编号": "XR-11", "材料编号": "Y-C-01", "节点编号": "VN-2",
         "学生编号": "X-2024-005", "满分": "3", "评分状态": "未作答",
         "作答事件编号": "XE-09"},
    ]
    # dictionary：字段说明存在但列名不同（字段/说明）
    dictionary = [
        {"字段": "学生编号", "含义": "自建测试学生编号"},
        {"字段": "评分点得分", "含义": "JSON格式的评分点明细"},
    ]
    return RawTables(
        requirements=requirements, materials=materials, students=students,
        nodes=nodes, records=records, dictionary=dictionary,
    )


def build_custom_catalog() -> Catalog:
    """与自建数据配套的目录：Y-D-01 明确标注属于 VN-2（供 T3 越界检测）。"""
    def mat(mid: str, indicator: str) -> Material:
        return Material(
            material_id=mid, material_type="question", topic_ids=["TA-1"],
            indicator_ids=[indicator], content=f"自建题目{mid}",
            source_locator=SourceLocator(
                source_file="custom", verification_status="verified"),
            max_score=1 if mid == "Y-B-02" else (3 if mid == "Y-C-01" else 2),
        )

    return Catalog(
        version="custom-cat-1",
        topics=[Topic(topic_id="TA-1", title="自建主题：代数基础")],
        indicators=[
            Indicator(indicator_id="IA-1", topic_id="TA-1", description="代入求值"),
            Indicator(indicator_id="IA-2", topic_id="TA-1", description="不等式求解"),
        ],
        materials=[mat("Y-A-01", "IA-1"), mat("Y-B-02", "IA-1"),
                   mat("Y-C-01", "IA-2"), mat("Y-D-01", "IA-2")],
        links=[],
    )


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def prepared():
    return prepare_dataset(build_custom_tables(), RULE)


def test_custom_ids_import_without_crash(prepared):
    """P0核心：完全不同的编号体系 + 乱序列 + 缺可选列，整包导入不崩溃。"""
    assert {s.student_id for s in prepared.students} == {
        "X-2024-001", "X-2024-002", "X-2024-003", "X-2024-004", "X-2024-005", "X-2024-008"}
    assert {n.node_id for n in prepared.nodes} == {"VN-1", "VN-2"}
    assert prepared.source_manifest.table_row_counts["records"] == 12
    # 12条记录 → 10个事件（XE-06两条合一、XE-07两条合一）
    assert len(prepared.selected_events) == 10


def test_t1_score_exceeds_max_quarantined(prepared):
    """T1 超满分：不裁剪、排除统计。"""
    e = next(e for e in prepared.selected_events if e.event_id == "XE-03")
    assert e.score == 10 and e.max_score == 1
    assert e.disposition == "exclude_score"
    assert any(i.code == "SCORE_EXCEEDS_MAX" for i in prepared.quality_issues)


def test_t4_same_version_conflict(prepared):
    """T4 同版冲突：隔离且双方可查。"""
    e = next(e for e in prepared.selected_events if e.event_id == "XE-06")
    assert e.disposition == "quarantine_event"
    conflict = next(i for i in prepared.quality_issues if i.code == "CONFLICTING_RECORDS")
    assert set(conflict.record_ids) == {"XR-06", "XR-07"}


def test_t5_latest_revision_pending_no_fallback(prepared):
    """T5 修订链：最新版待评分，旧版2分不进入统计。"""
    e = next(e for e in prepared.selected_events if e.event_id == "XE-07")
    assert e.status == "pending"
    assert e.score is None
    assert e.selected_record_id == "XR-09"


def test_t2_rubric_sum_mismatch_flagged(prepared):
    """T2 评分点矛盾：和≠得分 → 标记待复核，保留官方分数不重判。"""
    issue = next(i for i in prepared.quality_issues if i.code == "RUBRIC_SUM_MISMATCH")
    assert issue.record_ids == ["XR-04"]
    assert "2" in issue.message and "3" in issue.message
    e = next(e for e in prepared.selected_events if e.event_id == "XE-04")
    assert e.score == 3 and e.disposition == "include"  # 官方分数保留，正常进统计
    # Q格式一致时不误报（XE-10 无 RUBRIC_SUM_MISMATCH）
    assert not any(
        i.event_id == "XE-10" and i.code == "RUBRIC_SUM_MISMATCH"
        for i in prepared.quality_issues
    )


def test_t3_material_node_mismatch_flagged(prepared):
    """T3 越界题目：Y-D-01 属 VN-2 但作答在 VN-1 → 标记复核。"""
    issue = next(i for i in prepared.quality_issues if i.code == "MATERIAL_NODE_MISMATCH")
    assert issue.event_id == "XE-05"
    assert "VN-1" in issue.message and "VN-2" in issue.message
    e = next(e for e in prepared.selected_events if e.event_id == "XE-05")
    assert e.disposition == "include"  # 标记但不改分


def test_t6_roster_student_without_records(prepared):
    """T6 名册学生无作答不消失。"""
    assert "X-2024-008" in {s.student_id for s in prepared.students}


def test_reconciliation_identity(prepared):
    """对账口径：选中事件 = 已评分 + 待评分 + 未作答（17号文档验收句）。"""
    events = prepared.selected_events
    by_status = {
        "graded": sum(1 for e in events if e.status == "graded"),
        "pending": sum(1 for e in events if e.status == "pending"),
        "unanswered": sum(1 for e in events if e.status == "unanswered"),
    }
    assert sum(by_status.values()) == len(events) == 10
    assert by_status == {"graded": 7, "pending": 2, "unanswered": 1}


def test_custom_data_full_pipeline():
    """端到端：自建数据 + 自建目录 → analyze_dataset 出结果（含班级、名册口径）。"""
    prepared = prepare_dataset(build_custom_tables(), RULE)
    bundle = analyze_dataset(prepared, build_custom_catalog(), RUN_CTX, CONFIG)
    # 每个名册学生都有 StudentResult（含无作答的 X-2024-008）
    students_with_results = {r.student_id for r in bundle.student_results}
    assert "X-2024-008" in students_with_results
    # 两个班都有班级结果
    assert {c.class_id for c in bundle.class_results} == {"高一(3)班", "高一(4)班"}
    # quality_summary 对账可用
    qs = bundle.quality_summary
    assert qs["selected_events"] == qs["graded"] + qs["pending"] + qs["unanswered"]


def test_missing_required_column_clear_error():
    """缺必需列：结构化错误（422来源），不是崩溃。"""
    tables = build_custom_tables()
    tables.students = [{"模拟班级编号": "高一(3)班"}]  # 缺学生编号列
    from study_companion.contracts import DomainError
    with pytest.raises(DomainError) as exc:
        prepare_dataset(tables, RULE)
    assert exc.value.code == "STRUCTURE_INVALID"
    # 错误详情可指明缺哪个列
    assert exc.value.details and "student_id" in str(exc.value.details)


def test_empty_optional_tables_do_not_crash():
    """空表（如 dictionary/requirements 为空）不崩溃。"""
    tables = build_custom_tables()
    tables.dictionary = []
    tables.requirements = []
    ds = prepare_dataset(tables, RULE)
    assert ds.source_manifest.table_row_counts["dictionary"] == 0
