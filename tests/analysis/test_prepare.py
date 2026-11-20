"""prepare_dataset 手算验证用例。

每条用例对应B工作指南"必须验证的案例"中的一条，数值均可人工推算核对。
"""

from __future__ import annotations

import pytest

from study_companion.analysis import prepare_dataset
from study_companion.contracts import DomainError, ImportConfig, RawRow, RawTables

RULE = ImportConfig(rule_version="import-rules-v1")


def make_tables(records: list[RawRow], students: list[RawRow] | None = None) -> RawTables:
    """构造最小六表输入；students 默认 C01 班 S01、S02 两人（含无记录学生）。"""
    if students is None:
        students = [
            {"学生编号": "S01", "模拟班级编号": "C01"},
            {"学生编号": "S02", "模拟班级编号": "C01"},
        ]
    return RawTables(
        requirements=[{"指标编号": "I01", "主题编号": "T01"}],
        materials=[{"材料编号": "Q01", "指标编号": "I01"}],
        students=students,
        nodes=[{"节点编号": "N01"}, {"节点编号": "N02"}],
        records=records,
        dictionary=[{"字段": "记录编号", "含义": "作答记录编号"}],
    )


def record(record_id: str, student: str = "S01", node: str = "N01", material: str = "Q01",
           revision: str = "1", score: str | None = None, max_score: str | None = "2",
           grading_status: str | None = None, event_id: str | None = None,
           final_answer: str | None = None) -> RawRow:
    row: RawRow = {
        "记录编号": record_id,
        "学生编号": student,
        "材料编号": material,
        "主题编号": "T01",
        "节点编号": node,
        "修订版本": revision,
    }
    if event_id is not None:
        row["作答事件编号"] = event_id
    if grading_status is not None:
        row["评分状态"] = grading_status
    if score is not None:
        row["得分"] = score
    if max_score is not None:
        row["满分"] = max_score
    if final_answer is not None:
        row["学生最终作答"] = final_answer
    return row


def codes(dataset) -> list[str]:
    return [issue.code for issue in dataset.quality_issues]


def event_of(dataset, event_id: str):
    return next(e for e in dataset.selected_events if e.event_id == event_id)


# 1. 同事件两版只计最新；最新版未评分时旧分不进入当前统计
def test_superseded_revision_selects_latest():
    ds = prepare_dataset(make_tables([
        record("R01", revision="1", score="1", grading_status="已评分", event_id="E1"),
        record("R02", revision="2", score=None, grading_status="待评分", event_id="E1"),
    ]), RULE)
    assert event_of(ds, "E1").status == "pending"
    assert event_of(ds, "E1").score is None  # 旧版分数1不回退使用
    assert event_of(ds, "E1").selected_record_id == "R02"
    assert "SUPERSEDED_REVISION" in codes(ds)


# 2. 同最高版本冲突时隔离，并可查看冲突双方
def test_same_version_conflict_quarantined():
    ds = prepare_dataset(make_tables([
        record("R01", revision="2", score="0", grading_status="已评分", event_id="E1"),
        record("R02", revision="2", score="2", grading_status="已评分", event_id="E1"),
    ]), RULE)
    assert event_of(ds, "E1").disposition == "quarantine_event"
    conflict = next(i for i in ds.quality_issues if i.code == "CONFLICTING_RECORDS")
    assert set(conflict.record_ids) == {"R01", "R02"}  # 冲突双方都可查


# 3. 得分超过满分时不自动裁剪到满分
def test_score_exceeds_max_not_clipped():
    ds = prepare_dataset(make_tables([
        record("R01", score="3", max_score="2", grading_status="已评分", event_id="E1"),
    ]), RULE)
    e = event_of(ds, "E1")
    assert e.score == 3 and e.max_score == 2  # 原值保留
    assert e.disposition == "exclude_score"
    assert "SCORE_EXCEEDS_MAX" in codes(ds)


# 4. 未作答、待评分、无记录分别显示，零分是有效数值
def test_status_distinctions_and_zero_is_valid():
    ds = prepare_dataset(make_tables([
        record("R01", student="S01", score="0", grading_status="已评分", event_id="E1"),
        record("R02", student="S01", node="N02", grading_status="待评分", event_id="E2"),
        record("R03", student="S01", node="N02", grading_status="未作答", event_id="E3"),
    ]), RULE)
    assert event_of(ds, "E1").status == "graded" and event_of(ds, "E1").score == 0
    assert event_of(ds, "E2").status == "pending"
    assert event_of(ds, "E3").status == "unanswered"


# 4b. 无评分状态列时按 得分/最终作答 推导
def test_status_derived_when_column_missing():
    ds = prepare_dataset(make_tables([
        record("R01", score="1", event_id="E1"),
        record("R02", node="N02", final_answer="写了过程", event_id="E2"),
        record("R03", node="N02", event_id="E3"),
    ]), RULE)
    assert event_of(ds, "E1").status == "graded"
    assert event_of(ds, "E2").status == "pending"
    assert event_of(ds, "E3").status == "unanswered"


# 5. 班级名册中无记录学生不消失
def test_roster_student_without_records_visible():
    ds = prepare_dataset(make_tables([
        record("R01", student="S01", score="2", grading_status="已评分", event_id="E1"),
    ]), RULE)
    assert {s.student_id for s in ds.students} == {"S01", "S02"}  # S02 无作答仍在名册


# 6. 完全重复的记录去重保留；同号不同内容隔离
def test_duplicate_record_ids():
    row = record("R01", score="1", grading_status="已评分", event_id="E1")
    ds = prepare_dataset(make_tables([row, dict(row)]), RULE)
    assert "DUPLICATE_RECORD" in codes(ds)
    assert len(ds.selected_events) == 1

    ds2 = prepare_dataset(make_tables([
        record("R01", score="1", grading_status="已评分", event_id="E1"),
        record("R01", score="2", grading_status="已评分", event_id="E2"),
    ]), RULE)
    assert "DUPLICATE_ID_CONFLICT" in codes(ds2)
    assert all(e.disposition == "quarantine_event" for e in ds2.selected_events)


# 7. 缺少必需列时整包拒收
def test_structure_error_raises():
    tables = make_tables([record("R01")])
    tables.students = [{"模拟班级编号": "C01"}]  # 缺学生编号列
    with pytest.raises(DomainError) as exc:
        prepare_dataset(tables, RULE)
    assert exc.value.code == "STRUCTURE_INVALID"


# 8. 处理前后数量可对账
def test_source_manifest_reconciles():
    ds = prepare_dataset(make_tables([
        record("R01", revision="1", score="1", grading_status="已评分", event_id="E1"),
        record("R02", revision="2", score="2", grading_status="已评分", event_id="E1"),
        record("R03", student="S02", score="1", grading_status="已评分", event_id="E2"),
    ]), RULE)
    assert ds.source_manifest.table_row_counts["records"] == 3
    assert len(ds.selected_events) == 2  # 两条修订记录只计一个事件
    records_rows = [r for r in ds.raw_records if r.table_name == "records"]
    assert len(records_rows) == 3  # 原始行一条不少
