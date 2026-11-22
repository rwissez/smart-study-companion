"""B工具包装器测试：输入输出、范围校验与越界拒绝（09验收第1、4条）。

数据构造与 test_analyze.py 相同口径：
  S01(有效) / S02(越界) / S03(名册无记录)
  C01班 N01节点，材料Q01-Q03，指标I01
"""

from __future__ import annotations

import pytest

from study_companion.analysis import analyze_dataset
from study_companion.contracts import (
    AnalysisConfig,
    AnswerEvent,
    Catalog,
    Indicator,
    Material,
    PreparedDataset,
    QualityIssue,
    RawRow,
    RawTables,
    RunContext,
    SourceLocator,
    SourceManifest,
    SourceRecord,
    Student,
    Topic,
)
from study_companion.analysis.prepare import prepare_dataset
from study_companion.contracts import ImportConfig
from study_companion.tools.b_analysis import (
    ServerContext,
    get_answer_evidence,
    get_class_analysis,
    get_quality_issues,
    get_student_analysis,
)

CTX = RunContext(run_id="run-1", dataset_id="ds-1", catalog_version="cat-v1", rule_version="rules-v1")
CONFIG = AnalysisConfig()


def make_ctx(allowed: frozenset[str]) -> ServerContext:
    """走真实 prepare_dataset + analyze_dataset 构造上下文，保证工具与统计同源。"""
    records: list[RawRow] = [
        {"记录编号": "R01", "学生编号": "S01", "材料编号": "Q01", "节点编号": "N01",
         "修订版本": "1", "得分": "2", "满分": "2", "评分状态": "已评分", "作答事件编号": "E1"},
        {"记录编号": "R02", "学生编号": "S01", "材料编号": "Q02", "节点编号": "N01",
         "修订版本": "1", "得分": "5", "满分": "2", "评分状态": "已评分", "作答事件编号": "E2"},
        {"记录编号": "R03", "学生编号": "S02", "材料编号": "Q01", "节点编号": "N01",
         "修订版本": "1", "得分": "1", "满分": "2", "评分状态": "已评分", "作答事件编号": "E3"},
    ]
    tables = RawTables(
        requirements=[{"指标编号": "I01", "主题编号": "T01"}],
        materials=[{"材料编号": "Q01", "指标编号": "I01"}, {"材料编号": "Q02", "指标编号": "I01"}],
        students=[{"学生编号": "S01", "模拟班级编号": "C01"},
                  {"学生编号": "S02", "模拟班级编号": "C01"},
                  {"学生编号": "S03", "模拟班级编号": "C01"}],
        nodes=[{"节点编号": "N01"}],
        records=records,
        dictionary=[],
    )
    dataset = prepare_dataset(tables, ImportConfig(rule_version="rules-v1"))
    catalog = Catalog(
        version="cat-v1",
        topics=[Topic(topic_id="T01", title="主题")],
        indicators=[Indicator(indicator_id="I01", topic_id="T01", description="指标")],
        materials=[
            Material(material_id=m, material_type="question", topic_ids=["T01"],
                     indicator_ids=["I01"], content=f"题{m}",
                     source_locator=SourceLocator(source_file="t", verification_status="verified"),
                     max_score=2)
            for m in ("Q01", "Q02")
        ],
        links=[],
    )
    bundle = analyze_dataset(dataset, catalog, CTX, CONFIG)
    return ServerContext(
        run_id=CTX.run_id, dataset_id=CTX.dataset_id, catalog_version=CTX.catalog_version,
        allowed_student_ids=allowed, bundle=bundle, dataset=dataset,
    )


# 1. 正常查询：返回结果本体 + 版本信息 + metric证据
def test_student_analysis_returns_versioned_result():
    ctx = make_ctx(frozenset({"S01"}))
    r = get_student_analysis(ctx, "c1", "S01", "N01")
    assert r.status == "succeeded" and r.error is None
    assert r.data.result.student_id == "S01"
    assert r.data.run_id == "run-1" and r.data.catalog_version == "cat-v1"
    assert r.evidence_refs and all(e["run_id"] == "run-1" for e in r.evidence_refs)


# 2. 范围拒绝：S02 不在 allowed_student_ids，即使结果存在也拒绝
def test_student_analysis_scope_denied():
    ctx = make_ctx(frozenset({"S01"}))  # 只允许S01
    r = get_student_analysis(ctx, "c2", "S02", "N01")
    assert r.status == "failed" and r.error["code"] == "SCOPE_DENIED"
    assert r.data is None


# 3. 名册学生无记录：可见但指标为空、带"无作答记录"说明（名册学生不消失）
def test_roster_student_returns_empty_result_with_note():
    ctx = make_ctx(frozenset({"S03"}))
    r = get_student_analysis(ctx, "c3", "S03", "N01")
    assert r.status == "succeeded"
    assert r.data.result.metrics == []
    assert any("无作答记录" in lim for lim in r.data.result.limitations)
    # 不存在的节点才返回 NOT_FOUND
    r2 = get_student_analysis(ctx, "c3b", "S03", "N99")
    assert r2.status == "failed" and r2.error["code"] == "NOT_FOUND"


# 4. 班级查询正常返回（教师角色）
def test_class_analysis_ok():
    ctx = make_ctx(frozenset({"S01", "S02", "S03"}))
    r = get_class_analysis(ctx, "c4", "C01", "N01")
    assert r.status == "succeeded"
    assert r.data.result.class_id == "C01"
    assert r.data.result.roster_count == 3


# 5. 质量问题查询：默认全部，按学生过滤，越界拒绝
def test_quality_issues_filters_by_student():
    ctx = make_ctx(frozenset({"S01", "S02"}))
    all_issues = get_quality_issues(ctx, "c5")
    assert all_issues.status == "succeeded" and len(all_issues.data) >= 1  # 至少有R02超满分

    s01 = get_quality_issues(ctx, "c6", student_id="S01")
    assert s01.status == "succeeded"
    assert s01.data, "S01 的超满分问题应被过滤出来"

    denied = get_quality_issues(make_ctx(frozenset({"S01"})), "c7", student_id="S02")  # S02不在该ctx允许范围
    assert denied.status == "failed" and denied.error["code"] == "SCOPE_DENIED"

    bad_page = get_quality_issues(ctx, "c8", page=0)
    assert bad_page.status == "failed" and bad_page.error["code"] == "INVALID_ARGUMENT"


# 6. 证据查询：返回原始作答 + 处置说明；越界记录拒绝
def test_answer_evidence_with_disposition():
    ctx = make_ctx(frozenset({"S01"}))
    r = get_answer_evidence(ctx, "c9", ["R01", "R02"])
    assert r.status == "succeeded"
    by_id = {e.record_id: e for e in r.data}
    assert by_id["R01"].selected_event["status"] == "graded"
    assert "正常纳入统计" in by_id["R01"].disposition_note
    assert by_id["R02"].selected_event["disposition"] == "exclude_score"
    assert "排除" in by_id["R02"].disposition_note
    # 原始payload原样保留（可追溯）
    assert by_id["R02"].payload["得分"] == "5"


# 7. 证据查询越界：R03 属于范围外学生 S02，拒绝访问
def test_answer_evidence_scope_denied():
    ctx = make_ctx(frozenset({"S01"}))
    r = get_answer_evidence(ctx, "c10", ["R03"])
    assert r.status == "failed" and r.error["code"] == "SCOPE_DENIED"
    r2 = get_answer_evidence(ctx, "c11", ["R999"])  # 不存在的记录
    assert r2.status == "failed" and r2.error["code"] == "NOT_FOUND"
    r3 = get_answer_evidence(ctx, "c12", [])  # 空参数
    assert r3.status == "failed" and r3.error["code"] == "INVALID_ARGUMENT"


# 8. 智能体数值与B结果一致（09验收第3条）
def test_tool_values_match_analysis():
    ctx = make_ctx(frozenset({"S01"}))
    r = get_student_analysis(ctx, "c13", "S01", "N01")
    direct = next(s for s in ctx.bundle.student_results
                  if s.student_id == "S01" and s.node_id == "N01")
    assert r.data.result == direct  # 工具直接引用同一份结果对象序列化，不复制统计
