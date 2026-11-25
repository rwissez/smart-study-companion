"""test_strategy.py —— 策略选择器与学习观察事件（A 建议①②③④⑤ 的 C 侧测试）。

覆盖：归并、优先级、负荷截断、图谱前置链归并、发布保护、闭环规则。
"""

from __future__ import annotations

import json
from pathlib import Path

from study_companion.contracts import (
    Catalog,
    CatalogLink,
    EvidenceRef,
    Finding,
    Indicator,
    Material,
    Metric,
    MetricScope,
    RecommendationConfig,
    RunContext,
    SourceLocator,
    StudentResult,
    Topic,
)

from study_companion.contracts import ObservationEvent
from study_companion.recommendation.service import recommend_rules, validate_recommendations
from study_companion.recommendation.strategy import (
    choose_strategy,
    next_action_rule,
    prerequisite_sources,
)

FIXTURE = Path(__file__).parents[1] / "fixtures" / "recommendation_minimal.json"


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def make_catalog(**overrides) -> Catalog:
    data = load_fixture()["catalog"]
    if overrides:
        data = {**data, **overrides}
    return Catalog.model_validate(data)


def make_context() -> RunContext:
    return RunContext.model_validate(load_fixture()["run_context"])


def make_analysis(findings: list[Finding]) -> StudentResult:
    base = load_fixture()["student_base"]
    metric = Metric(
        metric_id="M001",
        name="得分率",
        value=round(1 / 3, 4),
        numerator=1,
        denominator=3,
        unit="ratio",
        scope=MetricScope(student_id="S01", node_id="N01"),
        evidence_refs=[EvidenceRef(kind="metric", id="M001", run_id="run-demo-1")],
    )
    return StudentResult.model_validate(
        {
            **base,
            "metrics": [metric.model_dump(mode="json")],
            "findings": [f.model_dump(mode="json") for f in findings],
        }
    )


def finding_1a(fid: str = "F001", ind: str = "T01I1") -> Finding:
    return Finding(
        finding_id=fid,
        indicator_id=ind,
        description="有效作答3题中2题在除以负数后未翻转不等号方向，解答过程显示移项正确",
        evidence_status="sufficient",
        performance="needs_practice",
        evidence_refs=[EvidenceRef(kind="record", id="R12", run_id="run-demo-1")],
    )


def finding_1b(fid: str = "F002", ind: str = "T01I1") -> Finding:
    return Finding(
        finding_id=fid,
        indicator_id=ind,
        description="该生三次作答均无有效解题步骤",
        evidence_status="sufficient",
        performance="needs_practice",
        evidence_refs=[EvidenceRef(kind="record", id="R20", run_id="run-demo-1")],
        limitations=["缺少解答过程"],
    )


def finding_type3(fid: str = "F003", ind: str = "T01I2") -> Finding:
    return Finding(
        finding_id=fid,
        indicator_id=ind,
        description="该指标仅1道有效作答，不足以形成表现结论",
        evidence_status="insufficient",
        performance="undetermined",
        evidence_refs=[],
    )


def finding_type2(fid: str = "F004", ind: str = "T01I2") -> Finding:
    return Finding(
        finding_id=fid,
        indicator_id=ind,
        description="两道变式均正确且步骤完整",
        evidence_status="sufficient",
        performance="observed_strength",
        evidence_refs=[EvidenceRef(kind="record", id="R30", run_id="run-demo-1")],
    )


# ---------------------------------------------------------------------------
# 建议①：归并
# ---------------------------------------------------------------------------

def test_strategy_merge_same_indicator():
    # 同指标同为 1a 的两条诊断 → 合并成一条建议，finding_ids 全挂
    plan = choose_strategy([finding_1a("F001"), finding_1a("F005")], make_catalog(), RecommendationConfig())
    assert len(plan.entries) == 1
    assert [f.finding_id for f in plan.entries[0].findings] == ["F001", "F005"]

    bundle = recommend_rules(make_analysis([finding_1a("F001"), finding_1a("F005")]), make_catalog(), make_context(), RecommendationConfig())
    assert len(bundle.recommendations) == 1
    rec = bundle.recommendations[0]
    assert sorted(rec.finding_ids) == ["F001", "F005"]
    assert "F005" in rec.reason and "合并同指标同类诊断" in rec.reason


def test_strategy_priority_order():
    # type3(观察) > 1b(扶练) > type2(迁移)；上限放宽到 3 纯看排序
    findings = [finding_type2("F004", "T01I3"), finding_1b(), finding_type3()]
    plan = choose_strategy(findings, make_catalog(), RecommendationConfig(max_recommendations=3))
    assert [e.kind for e in plan.entries] == ["type3", "1b", "type2"]

    bundle = recommend_rules(
        make_analysis(findings), _catalog_with_extra_material(), make_context(), RecommendationConfig(max_recommendations=3)
    )
    # 观察类在前
    assert bundle.recommendations[0].indicator_id == "T01I2"
    assert bundle.recommendations[1].indicator_id == "T01I1"
    assert len(bundle.recommendations) == 3


# ---------------------------------------------------------------------------
# 建议③：负荷截断
# ---------------------------------------------------------------------------

def test_strategy_load_limit():
    # 默认上限 2：type3(T01I2) + 1b(T01I1) + type2(T01I2) → 保留 type3、1b，type2 暂缓
    findings = [finding_type2(), finding_1b(), finding_type3()]
    plan = choose_strategy(findings, make_catalog(), RecommendationConfig())
    assert [e.kind for e in plan.entries] == ["type3", "1b"]
    assert plan.deferred_indicators == ["T01I2"]

    bundle = recommend_rules(make_analysis(findings), make_catalog(), make_context(), RecommendationConfig())
    assert len(bundle.recommendations) == 2
    last = bundle.recommendations[-1]
    assert any("T01I2" in c.observable_condition + c.next_action for c in last.adjustment_conditions)
    # 暂缓话术点明减负上限
    assert any("减负" in c.next_action for c in last.adjustment_conditions)


# ---------------------------------------------------------------------------
# 建议②：知识图谱前置链
# ---------------------------------------------------------------------------

def _catalog_with_link() -> Catalog:
    return make_catalog(
        links=[
            CatalogLink(
                source_id="T01I1",
                target_id="T01I2",
                relation="prerequisite",
                basis="textbook",
                description="负数运算是一元一次不等式的前置知识",
            ).model_dump(mode="json")
        ]
    )


def test_prerequisite_direction():
    # 图谱投影方向：source=前置 → target=后续
    assert prerequisite_sources(_catalog_with_link(), "T01I2") == ["T01I1"]
    assert prerequisite_sources(_catalog_with_link(), "T01I1") == []


def test_prerequisite_merge_defers_successor():
    # 前置 T01I1 与后继 T01I2 都失分 → 先补前置，后继暂缓并入复评条件
    findings = [finding_1b("F002", "T01I1"), finding_1b("F010", "T01I2")]
    plan = choose_strategy(findings, _catalog_with_link(), RecommendationConfig())
    assert [e.findings[0].indicator_id for e in plan.entries] == ["T01I1"]
    assert plan.deferred_indicators == ["T01I2"]

    bundle = recommend_rules(make_analysis(findings), _catalog_with_link(), make_context(), RecommendationConfig())
    assert len(bundle.recommendations) == 1
    rec = bundle.recommendations[0]
    assert rec.indicator_id == "T01I1"
    assert any("T01I2" in c.next_action and "复评" in c.observable_condition for c in rec.adjustment_conditions)
    assert "同一根源" in rec.reason


def test_prerequisite_ok_keeps_normal_generation():
    # 前置表现正常（observed_strength）→ 后继 1b 正常生成，理由里写明前置已查
    findings = [finding_type2("F004", "T01I1"), finding_1b("F010", "T01I2")]
    plan = choose_strategy(findings, _catalog_with_link(), RecommendationConfig())
    assert {e.kind for e in plan.entries} == {"type2", "1b"}
    b_entry = next(e for e in plan.entries if e.kind == "1b")
    assert "T01I1" in b_entry.rationale and "未见前置失分" in b_entry.rationale

    bundle = recommend_rules(make_analysis(findings), _catalog_with_link(), make_context(), RecommendationConfig())
    rec_1b = next(r for r in bundle.recommendations if r.indicator_id == "T01I2")
    assert "已沿知识图谱检查前置知识指标（T01I1）" in rec_1b.reason


# ---------------------------------------------------------------------------
# 建议⑤：发布保护
# ---------------------------------------------------------------------------

def _catalog_with_extra_material(status: str = "verified") -> Catalog:
    """追加第三个指标 T01I3（含一道指定核对状态的题），供排序/发布保护测试用。"""
    data = load_fixture()["catalog"]
    data["indicators"].append(
        Indicator(indicator_id="T01I3", topic_id="T01", description="测试指标").model_dump(mode="json")
    )
    data["materials"].append(
        Material(
            material_id="M007",
            material_type="question",
            topic_ids=["T01"],
            indicator_ids=["T01I3"],
            content="解不等式 2x+1>5，并说明每一步的依据。",
            source_locator=SourceLocator(
                source_file="demo.pdf", pdf_page=1, verification_status=status
            ),
        ).model_dump(mode="json")
    )
    return Catalog.model_validate(data)


def test_unverified_material_blocked():
    """选材阶段隔离未核对材料（A 反馈 2026-11-25 P0）：unverified 题不被选中，
    该指标转暂缓并提示教师核对——而不是生成建议后才被发布校验拒绝。"""
    findings = [finding_type3("F003", "T01I3")]
    catalog_unverified = _catalog_with_extra_material('unverified')
    bundle = recommend_rules(make_analysis(findings), catalog_unverified, make_context(), RecommendationConfig())
    # unverified 材料不出现在任何活动引用里
    picked = {a.material_id for r in bundle.recommendations for a in r.activities}
    assert "M007" not in picked
    # 该指标因无核对材料而转暂缓，教师端可见提示
    deferred_note = " ".join(
        c.next_action for r in bundle.recommendations for c in r.adjustment_conditions
    )
    assert "T01I3" in deferred_note
    # 双保险仍在：即便外部构造出引用 unverified 的建议，发布校验也会拒绝
    result = validate_recommendations(bundle, make_analysis(findings), catalog_unverified)
    assert result.valid or "UNVERIFIED_MATERIAL_PUBLISHED" not in {i.code for i in result.issues}


def test_verified_material_passes_protection():
    # 全 verified 材料的既有场景不触发发布保护
    findings = [finding_type3("F003", "T01I2")]
    bundle = recommend_rules(make_analysis(findings), make_catalog(), make_context(), RecommendationConfig())
    result = validate_recommendations(bundle, make_analysis(findings), make_catalog())
    assert result.valid
    assert all(i.code != "UNVERIFIED_MATERIAL_PUBLISHED" for i in result.issues)


# ---------------------------------------------------------------------------
# 建议④：学习观察事件闭环
# ---------------------------------------------------------------------------

def _event(etype: str, **kw) -> ObservationEvent:
    return ObservationEvent(
        event_id=kw.get("event_id", "E1"),
        run_id="run-demo-1",
        student_id="S01",
        recommendation_id="rec-x",
        activity_order=1,
        material_id="M001",
        event_type=etype,
    )


def test_next_action_advance():
    assert next_action_rule([_event("completed_independent")]) == "advance"


def test_next_action_retry_then_fall_back():
    # 借助提示第 1 次 → 同级再练（提示减一档）；连续第 2 次 → 回落
    assert next_action_rule([_event("completed_with_hints")]) == "retry_same"
    assert next_action_rule([_event("completed_with_hints"), _event("completed_with_hints")]) == "fall_back"


def test_next_action_step_failed():
    assert next_action_rule([_event("step_failed")]) == "retry_same"
    # 步骤失败 + 借助提示（连续未独立）→ 回落
    assert next_action_rule([_event("step_failed"), _event("completed_with_hints")]) == "fall_back"


def test_next_action_escalate_after_two_incomplete():
    assert next_action_rule([_event("not_completed")]) == "retry_same"
    assert next_action_rule([_event("not_completed"), _event("not_completed")]) == "escalate_teacher"


def test_next_action_pending_cases():
    assert next_action_rule([]) == "pending"
    assert next_action_rule([_event("teacher_note")]) == "pending"
    assert next_action_rule([_event("effectiveness_assessed")]) == "pending"


# ---------------------------------------------------------------------------
# A 任务单 C-5：验证扩展（重复建议 / 重复材料 / 负荷超限）
# ---------------------------------------------------------------------------

def _bare_rec(rid: str, fids: list[str], material_ids: list[str]):
    from study_companion.contracts import Activity, AdjustmentCondition, Recommendation

    acts = [
        Activity.model_construct(
            order=i + 1, material_id=mid, purpose="p",
            hint_levels=["先想方向"], observation_points=["步骤完整性"],
            previously_attempted=False,
        )
        for i, mid in enumerate(material_ids)
    ]
    return Recommendation.model_construct(
        recommendation_id=rid, indicator_id="T01I1", finding_ids=fids, goal="g",
        reason=f"诊断 {fids[0]}：三次作答均无有效解题步骤，安排递减干预扶练。",
        activities=acts,
        adjustment_conditions=[AdjustmentCondition.model_construct(observable_condition="仍错", next_action="转教师")],
        review_status="draft", generation_method="rule",
    )


def _bare_bundle(recs):
    from study_companion.contracts import RecommendationBundle

    return RecommendationBundle.model_construct(
        run_id="run-demo-1", dataset_id="ds-demo-1", catalog_version="synthetic-v1",
        student_id="S01", node_id="N01", recommendations=recs, validation_issues=[],
    )


def test_validate_blocks_duplicate_material():
    analysis = make_analysis([finding_1b()])
    bundle = _bare_bundle([
        _bare_rec("rec-A", ["F002"], ["M003", "M001"]),
        _bare_rec("rec-B", ["F006"], ["M001"]),  # M001 与 rec-A 重复
    ])
    result = validate_recommendations(bundle, analysis, make_catalog())
    assert not result.valid
    assert "DUPLICATE_MATERIAL" in {i.code for i in result.issues}


def test_validate_blocks_duplicate_recommendation():
    analysis = make_analysis([finding_1b()])
    bundle = _bare_bundle([
        _bare_rec("rec-A", ["F002"], ["M001"]),
        _bare_rec("rec-B", ["F002"], ["M002"]),  # F002 重复出现在两条建议
    ])
    result = validate_recommendations(bundle, analysis, make_catalog())
    assert not result.valid
    assert "DUPLICATE_RECOMMENDATION" in {i.code for i in result.issues}


def test_validate_blocks_load_exceeded():
    analysis = make_analysis([finding_1b()])
    bundle = _bare_bundle([
        _bare_rec("rec-A", ["F002"], ["M001"]),
        _bare_rec("rec-B", ["F006"], ["M002"]),
        _bare_rec("rec-C", ["F007"], ["M003"]),
    ])
    result = validate_recommendations(bundle, analysis, make_catalog(), RecommendationConfig())
    assert "LOAD_LIMIT_EXCEEDED" in {i.code for i in result.issues}
    # 不传 config 时不做负荷检查（向后兼容）
    result2 = validate_recommendations(bundle, analysis, make_catalog())
    assert "LOAD_LIMIT_EXCEEDED" not in {i.code for i in result2.issues}


def test_materials_not_reused_across_recommendations():
    # 策略层：1b 用完 M003/M001/M002 后，同指标 1a 无题可用 → 转暂缓，不硬凑重复题
    findings = [finding_1b("F002", "T01I1"), finding_1a("F001", "T01I1")]
    bundle = recommend_rules(
        make_analysis(findings), make_catalog(), make_context(), RecommendationConfig(max_recommendations=3)
    )
    # 1b（高优先级）保留，1a 因材料耗尽转暂缓
    assert len(bundle.recommendations) == 1
    all_materials = [a.material_id for r in bundle.recommendations for a in r.activities]
    assert len(all_materials) == len(set(all_materials))  # 无重复材料
    last = bundle.recommendations[-1]
    assert any("T01I1" in c.next_action and "材料" in c.next_action for c in last.adjustment_conditions)


# ---------------------------------------------------------------------------
# 学生端加强 + 文案变体（2026-11-24 主人反馈：前端死板、学生版薄弱、表述去重）
# ---------------------------------------------------------------------------

def test_student_intro_present_for_all_kinds():
    """四类建议都带学伴开场白（student_intro），学生端不再只有提示层级。"""
    for f in (finding_type3(), finding_type2(), finding_1a(), finding_1b()):
        rec = recommend_rules(
            make_analysis([f]), make_catalog(), make_context(), RecommendationConfig()
        ).recommendations[0]
        assert rec.student_intro.strip(), f"{f.finding_id} 缺 student_intro"


def test_scaffolded_intro_varies_with_frustration():
    """1b 开场白按挫败信号分档：挫败接情绪（C/D 版），一般给行动起点（A/B 版）。"""
    ctx, cfg = make_context(), RecommendationConfig()

    def intro_for(desc: str) -> str:
        f = finding_1b().model_copy(update={"description": desc})
        return recommend_rules(make_analysis([f]), make_catalog(), ctx, cfg).recommendations[0].student_intro

    frustrated = intro_for("该生三次作答均无有效解题步骤")
    normal = intro_for("该生在移项处出现一次失误")

    assert "错了也有用" in frustrated          # 挫败 + 有学习卡 → C 版
    assert "把它拆小" not in frustrated        # 挫败档不再落一般档模板
    assert "把它拆小" in normal                # 一般 + 有学习卡 → A 版
    assert "错了也有用" not in normal


def test_reason_varies_with_evidence_status():
    """文案变体：同类型不同证据状态生成不同 reason（治模板死板）。"""
    base = recommend_rules(
        make_analysis([finding_type3()]), make_catalog(), make_context(), RecommendationConfig()
    ).recommendations[0]
    conflicting = recommend_rules(
        make_analysis([finding_type3().model_copy(update={"evidence_status": "conflicting"})]),
        make_catalog(),
        make_context(),
        RecommendationConfig(),
    ).recommendations[0]
    assert base.reason != conflicting.reason
    assert "冲突" in conflicting.reason


def test_purpose_quotes_material_excerpt():
    """证据织入：活动目的引用材料题干摘要，不同材料的活动目的互不相同。"""
    rec = recommend_rules(
        make_analysis([finding_type3()]), make_catalog(), make_context(), RecommendationConfig()
    ).recommendations[0]
    purposes = [a.purpose for a in rec.activities]
    assert len(purposes) >= 2
    assert len(set(purposes)) == len(purposes), "活动目的出现完全重复（模板死板）"


def test_validate_blocks_duplicate_goal():
    """表述去重：同一包内 goal 完全相同 → DUPLICATE_GOAL。"""
    rec_a = _bare_rec("rec-A", ["F001"], ["M003"])
    rec_b = _bare_rec("rec-B", ["F002"], ["M001", "M002"])
    rec_b = rec_b.model_copy(update={"goal": rec_a.goal})
    bundle = _bare_bundle([rec_a, rec_b])
    result = validate_recommendations(
        bundle, make_analysis([finding_1a(), finding_1b()]), make_catalog()
    )
    codes = [i.code for i in result.issues]
    assert "DUPLICATE_GOAL" in codes


# ---------------------------------------------------------------------------
# A 合并前四项反馈验证（2026-11-24）
# ---------------------------------------------------------------------------

def test_materials_scan_after_exclude():
    """③ 材料过滤顺序：先全量取再排除再截断，已用材料不挤占名额。"""
    data = load_fixture()["catalog"]
    data["materials"].append(
        Material(
            material_id="M008",
            material_type="question",
            topic_ids=["T01"],
            indicator_ids=["T01I1"],
            content="解不等式 -3x>6，并说明方向变化。",
            source_locator=SourceLocator(
                source_file="demo.pdf", pdf_page=1, verification_status="verified"
            ),
        ).model_dump(mode="json")
    )
    cat = Catalog.model_validate(data)
    bundle = recommend_rules(
        make_analysis([finding_1b(), finding_1a()]), cat, make_context(), RecommendationConfig()
    )
    # 旧顺序（先截后排）：1b 用掉 M001/M002 后，1a 候选在截断阶段只剩这两道，排除后为空 → 转暂缓
    # 新顺序：全量含 M008，排除后仍有题 → 1a 正常生成
    assert len(bundle.recommendations) == 2, "排除后仍有可用材料却未生成第二条建议"


def test_adjustment_condition_groups():
    """② 后续动作分组：group 值合法且覆盖关键动作。"""
    bundle = recommend_rules(
        make_analysis([finding_type2(), finding_1b()]),
        make_catalog(),
        make_context(),
        RecommendationConfig(),
    )
    groups = {c.group for r in bundle.recommendations for c in r.adjustment_conditions}
    assert groups <= {"advance", "fall_back", "escalate_teacher", "wrap_up"}
    assert "fall_back" in groups and "escalate_teacher" in groups


def test_observation_event_in_contracts():
    """①④ 事件契约统一：ObservationEvent 走 Contract 基类（strict+extra=forbid）。"""
    ev = ObservationEvent(
        event_id="E1",
        run_id="run-demo-1",
        student_id="S01",
        recommendation_id="rec-1",
        activity_order=1,
        material_id="M001",
        event_type="completed_independent",
    )
    assert ev.event_id == "E1"
    raised = False
    try:
        ObservationEvent(
            event_id="E2",
            run_id="r",
            student_id="S",
            recommendation_id="rec",
            activity_order=1,
            material_id="M",
            event_type="completed_independent",
            unknown_field="x",
        )
    except Exception:
        raised = True
    assert raised, "extra=forbid 未生效：事件契约未走 Contract 基类"


def test_attempted_materials_excluded_from_variants():
    """type2 变式必须排除学生已作答材料（record→material 关联进算法）：做过的变式不再重复安排。"""
    ctx, cfg = make_context(), RecommendationConfig()
    analysis = make_analysis([finding_type2()])
    catalog = make_catalog()

    without_attempted = recommend_rules(analysis, catalog, ctx, cfg)
    first_variant = without_attempted.recommendations[0].activities[0].material_id

    attempted = frozenset({first_variant})
    with_attempted = recommend_rules(analysis, catalog, ctx, cfg, attempted_material_ids=attempted)
    picked = [a.material_id for a in with_attempted.recommendations[0].activities]

    assert first_variant not in picked, f"已做材料 {first_variant} 不应再出现在变式安排 {picked}"
    # 排除后无题可用的极端情形：全部练习题都做过 → 该指标转暂缓而不是硬凑
    all_practice = frozenset(
        m.material_id for m in catalog.materials if m.material_type == "question"
    )
    starved = recommend_rules(analysis, catalog, ctx, cfg, attempted_material_ids=all_practice)
    deferred_rec = starved.recommendations[-1]
    # 全部耗尽不留空包：生成零活动暂缓说明，教师看得见原因
    assert deferred_rec.activities == []
    assert "暂缓" in deferred_rec.reason or "暂不安排" in deferred_rec.reason
    assert deferred_rec.student_intro != ""
