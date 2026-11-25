"""StrategySignal 错误阶段/类型判定测试（任务书验收：六种情况可区分）。

用例数据均按真实赛题数据的实际模式构造：
- 解答题有过程（269/269），失分模式 首点丢/非首点丢 均存在
- 填空题无过程，评分点即"答案正确"
- 材料表评分点定义格式 "Q006P2 正确列出周长算式 1分"
"""

from __future__ import annotations

from study_companion.analysis.strategy_signal import (
    STAGE_CALCULATION,
    STAGE_CANNOT_START,
    STAGE_CONCEPT,
    STAGE_KEY_STEP,
    STAGE_NONE,
    STAGE_VERIFICATION,
    TYPE_CALC,
    TYPE_CONCEPT,
    TYPE_INSUFFICIENT,
    TYPE_NO_VERIFICATION,
    TYPE_NONE,
    TYPE_STEP,
    build_signal,
    build_strategy_signals,
    classify_point,
    parse_rubric_definitions,
    parse_rubric_scores,
)
from study_companion.contracts import AnswerEvent, ImportConfig, RawTables
from study_companion.analysis import prepare_dataset

# 典型解答题材料（三评分点：建模/步骤/检验）
MAT_SOLUTION = {
    "材料编号": "Q100",
    "题型": "解答题",
    "评分点": "Q100P1 写出数量关系 1分\nQ100P2 正确列出算式 1分\nQ100P3 代入检验 1分",
    "满分": "3",
}
# 概念类首点材料
MAT_CONCEPT = {
    "材料编号": "Q200",
    "题型": "解答题",
    "评分点": "Q200P1 识别概念定义 1分\nQ200P2 列出算式 1分",
    "满分": "2",
}
# 填空题（答案点）
MAT_BLANK = {
    "材料编号": "Q300",
    "题型": "填空题",
    "评分点": "Q300P1 答案正确 1分",
    "满分": "1",
}


def event(score=2, max_score=3, status="graded", disposition="include", mat="Q100", rec="R01"):
    return AnswerEvent(
        event_id="E1", student_id="S01", node_id="N01", material_id=mat,
        selected_record_id=rec, status=status, score=score,
        max_score=max_score, disposition=disposition,
    )


def record(answer="x=11", steps="1. 列式\n2. 求解", rubric="Q100P1=1；Q100P2=1；Q100P3=0"):
    return {"学生最终作答": answer, "解答过程": steps, "评分点得分": rubric}


# ---------- 1. 不会开始 ----------
def test_cannot_start():
    s = build_signal(
        event(score=0, status="unanswered", rec=None),
        {"学生最终作答": "", "解答过程": "", "评分点得分": ""},
        MAT_SOLUTION,
    )
    assert s.error_stage == STAGE_CANNOT_START
    assert s.error_type == TYPE_INSUFFICIENT
    assert "不会开始" in s.reason


# ---------- 2. 概念错误（概念类点丢失，起步点已得） ----------
def test_concept_error():
    s = build_signal(
        event(score=1, max_score=2, mat="Q200"),
        record(answer="4a", steps="1. 依题意…", rubric="Q200P1=0；Q200P2=1"),
        MAT_CONCEPT,
    )
    # Q200P1=识别概念定义=concept类别丢失 → 概念错误
    assert s.error_stage == STAGE_CONCEPT
    assert s.error_type == TYPE_CONCEPT
    assert s.rubric_focus == ["Q200P1"]


# ---------- 3. 步骤错误（中段步骤点丢失） ----------
def test_step_error():
    s = build_signal(
        event(score=2),
        record(rubric="Q100P1=1；Q100P2=0；Q100P3=1"),
        MAT_SOLUTION,
    )
    assert s.error_stage == STAGE_KEY_STEP
    assert s.error_type == TYPE_STEP
    assert s.rubric_focus == ["Q100P2"]


# ---------- 4. 计算错误（末位答案/计算结果错，前面步骤对） ----------
def test_calculation_error():
    # 中段步骤全对、末点(结果/检验)丢 → 计算/结果类错误
    s = build_signal(
        event(score=2),
        record(answer="4a+7b", steps="1. 原式=…\n2. 合并", rubric="Q100P1=1；Q100P2=1；Q100P3=0"),
        # 把P3定义改成计算结果类
        {**MAT_SOLUTION, "评分点": "Q100P1 写出数量关系 1分\nQ100P2 正确列出算式 1分\nQ100P3 计算结果正确 1分"},
    )
    assert s.error_stage == STAGE_CALCULATION
    assert s.error_type == TYPE_CALC
    assert s.rubric_focus == ["Q100P3"]


# ---------- 5. 缺少检验（检验点单独丢） ----------
def test_missing_verification():
    s = build_signal(
        event(score=2),
        record(rubric="Q100P1=1；Q100P2=1；Q100P3=0"),
        MAT_SOLUTION,  # P3 = 代入检验 → verification类
    )
    assert s.error_stage == STAGE_VERIFICATION
    assert s.error_type == TYPE_NO_VERIFICATION


# ---------- 6. 证据不足（客观题答案错，无过程） ----------
def test_insufficient_evidence_objective():
    s = build_signal(
        event(score=0, max_score=1, mat="Q300"),
        record(answer="42", steps="", rubric="Q300P1=0"),
        MAT_BLANK,
    )
    assert s.error_stage == "undetermined"
    assert s.error_type == TYPE_INSUFFICIENT
    assert s.evidence_status == "insufficient"
    assert "客观题" in s.reason


# ---------- 7. 证据冲突（评分点和≠得分） ----------
def test_conflicting_evidence():
    s = build_signal(
        event(score=3),
        record(rubric="Q100P1=1；Q100P2=1；Q100P3=0"),  # 和=2 但得分3
        MAT_SOLUTION,
    )
    assert s.evidence_status == "conflicting"
    assert s.error_stage == "undetermined"


# ---------- 8. 满分 → 无错误 ----------
def test_full_score_none():
    s = build_signal(event(score=3), record(rubric="Q100P1=1；Q100P2=1；Q100P3=1"), MAT_SOLUTION)
    assert s.error_stage == STAGE_NONE
    assert s.error_type == TYPE_NONE


# ---------- 9. 首点丢 → 建模/起步失败 ----------
def test_first_point_lost_modeling():
    # Q100P1"写出数量关系"含概念词（数量关系）→ 概念类起步失败
    s = build_signal(
        event(score=2),
        record(rubric="Q100P1=0；Q100P2=1；Q100P3=1"),
        MAT_SOLUTION,
    )
    assert s.error_stage in ("modeling", STAGE_CONCEPT)
    assert s.rubric_focus == ["Q100P1"]
    # 换纯步骤类首点（去括号）→ 建模/步骤失败
    s2 = build_signal(
        event(score=2),
        record(rubric="Q100P1=0；Q100P2=1；Q100P3=1"),
        {**MAT_SOLUTION, "评分点": "Q100P1 正确去分母 1分\nQ100P2 合并同类项 1分\nQ100P3 代入检验 1分"},
    )
    assert s2.error_stage == "modeling"
    assert s2.error_type == TYPE_STEP


# ---------- 10. 解析函数 ----------
def test_parsers():
    scores = parse_rubric_scores("Q015P1=1；Q015P2=1；Q015P3=0")
    assert scores == {"Q015P1": 1, "Q015P2": 1, "Q015P3": 0}
    assert parse_rubric_scores("") == {}
    assert parse_rubric_scores("乱格式") == {}

    defs = parse_rubric_definitions("Q006P1 写出周长关系 1分\nQ006P2 正确列出周长算式 1分")
    assert defs["Q006P1"] == "写出周长关系"
    assert defs["Q006P2"] == "正确列出周长算式"

    assert classify_point("代入检验结果") == "verification"
    assert classify_point("识别概念定义") == "concept"
    assert classify_point("正确列出周长算式") == "step"
    assert classify_point("计算结果正确") == "calculation"
    assert classify_point("答案正确") == "calculation"  # 答案类点归计算/结果类
    assert classify_point("完全未知描述") == "unknown"


# ---------- 11. 批量入口端到端 ----------
def test_build_strategy_signals_batch():
    tables = RawTables(
        requirements=[{"指标编号": "I01", "主题编号": "T01"}],
        materials=[{"材料编号": "Q100", "指标编号": "I01", "题型": "解答题",
                    "评分点": "Q100P1 写出数量关系 1分\nQ100P2 正确列出算式 1分\nQ100P3 代入检验 1分"}],
        students=[{"学生编号": "S01", "模拟班级编号": "C01"}],
        nodes=[{"节点编号": "N01", "可用题目编号": "Q100"}],
        records=[
            {"记录编号": "R01", "学生编号": "S01", "材料编号": "Q100", "节点编号": "N01",
             "修订版本": "1", "得分": "2", "满分": "3", "评分状态": "已评分",
             "学生最终作答": "x=5", "解答过程": "1. 设…", "评分点得分": "Q100P1=1；Q100P2=1；Q100P3=0",
             "作答事件编号": "E1"},
        ],
        dictionary=[],
    )
    ds = prepare_dataset(tables, ImportConfig(rule_version="r1"))
    materials_rows = [r.payload for r in ds.raw_records if r.table_name == "materials"]
    signals = build_strategy_signals(ds, materials_rows)
    assert len(signals) == 1
    s = signals[0]
    assert s.error_stage == STAGE_VERIFICATION  # 检验点丢
    assert s.rubric_focus == ["Q100P3"]
    assert s.record_id == "R01"  # 可追溯
    assert s.evidence_refs  # 有证据引用


# ---------- 12. 主题层聚合（18号核对表校准规则） ----------
from study_companion.analysis import aggregate_signals_by_topic
from study_companion.analysis.strategy_signal import (
    STAGE_NONE,
    TYPE_CONCEPT,
    TYPE_STEP,
)


def _signal(student, material, qtype, error_type, stage):
    from study_companion.analysis.strategy_signal import StrategySignal
    return StrategySignal(
        student_id=student, node_id="N01", material_id=material,
        event_id=f"{student}-{material}", record_id=f"R-{material}",
        question_type=qtype, error_stage=stage, error_type=error_type,
    )


def test_topic_aggregation_confirms_repeated_error():
    # D01在T01主题：两道解答题都出step_error → 确认；一道concept_error → 未确认
    signals = [
        _signal("D01", "Q1", "解答题", TYPE_STEP, "key_step"),
        _signal("D01", "Q2", "解答题", TYPE_STEP, "key_step"),
        _signal("D01", "Q3", "解答题", TYPE_CONCEPT, "concept_understanding"),
        _signal("D01", "Q4", "填空题", "insufficient_evidence", "undetermined"),
    ]
    agg = aggregate_signals_by_topic(
        signals,
        indicator_to_topic={"I1": "T01"},
        material_to_indicators={"Q1": ["I1"], "Q2": ["I1"], "Q3": ["I1"], "Q4": ["I1"]},
    )
    assert len(agg) == 1
    g = agg[0]
    assert g["topic_id"] == "T01"
    assert g["confirmed_error_types"] == {"step_error": ["Q1", "Q2"]}
    assert g["unconfirmed_error_types"] == {"concept_error": ["Q3"]}
    assert g["solution_material_count"] == 3
    assert g["evidence_status"] == "sufficient"  # 有效题>=3且解答题>=1


def test_topic_aggregation_insufficient_when_few_questions():
    # 仅1道解答题 + 1道客观题 → 不足
    signals = [
        _signal("D02", "Q1", "解答题", TYPE_STEP, "key_step"),
        _signal("D02", "Q2", "填空题", "insufficient_evidence", "undetermined"),
    ]
    agg = aggregate_signals_by_topic(
        signals,
        indicator_to_topic={"I1": "T01"},
        material_to_indicators={"Q1": ["I1"], "Q2": ["I1"]},
    )
    assert agg[0]["evidence_status"] == "insufficient"
    assert agg[0]["confirmed_error_types"] == {}


def test_topic_aggregation_objective_only_never_sufficient():
    # 全客观题即使数量多也不充分（0%可判型）
    signals = [_signal("D03", f"Q{i}", "填空题", "insufficient_evidence", "undetermined") for i in range(1, 6)]
    agg = aggregate_signals_by_topic(
        signals, {"I1": "T01"}, {f"Q{i}": ["I1"] for i in range(1, 6)}
    )
    assert agg[0]["evidence_status"] == "insufficient"


# ---------- 13. 根因优先判定（人工复核案例3修正回归） ----------
def test_root_cause_priority_calc_before_concept():
    """案例3场景：P2化简(计算)丢 + P3解释(概念)丢 → 根因是P2计算错，P3是下游。"""
    from study_companion.analysis.strategy_signal import build_signal, STAGE_CALCULATION, TYPE_CALC
    mat = {
        "材料编号": "Q011", "题型": "解答题",
        "评分点": "Q011P1 列出三个数的和 1分\nQ011P2 正确化简 1分\nQ011P3 由n为整数解释倍数关系 1分",
    }
    s = build_signal(
        event(score=1, max_score=3, mat="Q011"),
        record(answer="3n+2", steps="1. (n-1)+n+(n+1)。2. =3n+2。", rubric="Q011P1=1；Q011P2=0；Q011P3=0"),
        mat,
    )
    assert s.error_stage == STAGE_CALCULATION
    assert s.error_type == TYPE_CALC
    assert "根因为计算" in s.reason
    # 下游受影响有说明
    assert any("下游" in l for l in s.limitations)
    assert s.rubric_focus == ["Q011P2", "Q011P3"]  # 焦点仍含全部丢分点


def test_root_cause_p1_lost_still_concept():
    """P1概念点丢仍是概念错误（根因在起步，优先级不变）。"""
    from study_companion.analysis.strategy_signal import build_signal, STAGE_CONCEPT
    mat = {
        "材料编号": "Q043", "题型": "解答题",
        "评分点": "Q043P1 识别概念定义 1分\nQ043P2 正确列出算式 1分\nQ043P3 代入检验 1分",
    }
    s = build_signal(
        event(score=1, max_score=3, mat="Q043"),
        record(answer="x", steps="过程", rubric="Q043P1=0；Q043P2=1；Q043P3=0"),
        mat,
    )
    assert s.error_stage == STAGE_CONCEPT
    assert s.error_type == "concept_error"


# ---------- 14. 产出结果型动词归计算类（人工复核案例9回归） ----------
def test_output_verbs_classified_as_calculation():
    """案例9场景：设列都对(P1✓)，消元求值运算错 → calculation，不是step。"""
    from study_companion.analysis.strategy_signal import build_signal, classify_point, STAGE_CALCULATION, TYPE_CALC
    mat = {
        "材料编号": "Q036", "题型": "解答题",
        "评分点": "Q036P1 设未知数并列数量关系 1分\nQ036P2 消元求两种数量 1分\nQ036P3 检验实际意义 1分",
    }
    s = build_signal(
        event(score=1, max_score=3, mat="Q036"),
        record(answer="鸡2只兔8只", steps="1. 设鸡x只兔y只。2. x+y=10, 2x+4y=26。3. y=8, x=2。",
               rubric="Q036P1=1；Q036P2=0；Q036P3=0"),
        mat,
    )
    assert s.error_stage == STAGE_CALCULATION
    assert s.error_type == TYPE_CALC
    assert "根因为计算" in s.reason
    # 词表直接验证
    assert classify_point("求出x") == "calculation"
    assert classify_point("消元求两种数量") == "calculation"
    assert classify_point("正确建立消元步骤") == "step"  # 步骤型消元仍是step
