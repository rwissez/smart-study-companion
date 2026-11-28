"""错因语义诊断：成功、非法题号丢弃、模型失败降级、分数不可改写。

全部使用 FakeService，不调用真实模型。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from study_companion.analysis.diagnosis_model import (
    DiagnosisCandidate,
    DiagnosisEvidenceOut,
    DiagnosisItemOut,
    DiagnosisMetric,
    DiagnosisOutput,
    DiagnosisRubricPoint,
    collect_unexplained_errors,
    diagnose_unexplained_errors,
)
from study_companion.analysis.service import analyze_dataset
from study_companion.contracts import (
    AnalysisConfig,
    AnswerEvent,
    Catalog,
    Indicator,
    Material,
    Node,
    PreparedDataset,
    RubricPoint,
    RunContext,
    SourceLocator,
    SourceManifest,
    SourceRecord,
    Student,
    Topic,
)

METRIC_ID = "M-STU-S01-N01-I01-valid_score_ratio"


@dataclass
class _ModelResult:
    succeeded: bool
    value: Any = None
    error: dict | None = None
    usage: dict | None = None
    prompt_version: str = "1.0"


class FakeService:
    """最小模型服务替身：记录请求，返回预设值或失败。"""

    configured = True

    def __init__(self, value: Any = None, *, failed: bool = False) -> None:
        self._value = value
        self._failed = failed
        self.requests: list[Any] = []

    def generate_structured(self, request, schema):  # noqa: ANN001
        self.requests.append(request)
        if self._failed:
            return _ModelResult(
                False,
                None,
                {"code": "MODEL_TIMEOUT", "message": "模型调用超时", "details": []},
            )
        return _ModelResult(True, self._value)


def _candidate() -> DiagnosisCandidate:
    return DiagnosisCandidate(
        candidate_id="S01:N01:Q1",
        student_id="S01",
        node_id="N01",
        material_id="Q1",
        indicator_ids=("I01",),
        question="化简 3(x+2)+x",
        question_type="solution",
        student_answer="4x+6",
        solution_steps="3(x+2)+x = 3x+6+x = 4x+6",
        reference_answer="4x+6",
        rubric_points=(
            DiagnosisRubricPoint("Q1P1", "去括号", 1, 1),
            DiagnosisRubricPoint("Q1P2", "合并同类项", 2, 1),
        ),
        rule_signal={
            "error_type": "step_error",
            "evidence_status": "sufficient",
            "reason": "最终答案一致但未得满分",
            "lost_points": [{"point_id": "Q1P2", "awarded": 1, "max_score": 2}],
            "score": 2,
            "max_score": 3,
        },
        metrics=(
            DiagnosisMetric(
                metric_id=METRIC_ID,
                name="valid_score_ratio",
                value=2 / 3,
                numerator=2,
                denominator=3,
                indicator_id="I01",
            ),
        ),
        record_id="R01",
    )


def _valid_item(**overrides: Any) -> DiagnosisItemOut:
    payload: dict[str, Any] = {
        "candidate_id": "S01:N01:Q1",
        "material_id": "Q1",
        "error_type": "step_error",
        "reason": "合并同类项时把 3x+x 算成 3x，漏加了 x",
        "evidence": [
            DiagnosisEvidenceOut(source="student_steps", quote="3x+6+x"),
            DiagnosisEvidenceOut(source="question", quote="3(x+2)+x"),
        ],
        "metric_ids": [METRIC_ID],
        "confidence": 0.78,
        "limitations": [],
    }
    payload.update(overrides)
    return DiagnosisItemOut(**payload)


# ---------------------------------------------------------------------------
# 1. 成功：批量输入、严格输出、程序回验
# ---------------------------------------------------------------------------


def test_success_returns_validated_diagnosis() -> None:
    candidate = _candidate()
    service = FakeService(DiagnosisOutput(items=[_valid_item()], limitations=[]))

    result = diagnose_unexplained_errors([candidate], service=service)

    assert result.called is True
    assert len(result.items) == 1
    item = result.items[0]
    assert item.material_id == "Q1"
    assert item.student_id == "S01"
    assert item.error_type == "step_error"
    assert item.confidence == pytest.approx(0.78)
    assert item.evidence[0].quote == "3x+6+x"

    assert len(service.requests) == 1
    request = service.requests[0]
    assert request.task_key == "diagnose_errors.v1"
    assert request.namespace == "b"
    assert request.prompt_version == "1.0"
    assert request.allow_external is True  # 任务声明可外发；是否真正外发仍由全局设置决定
    payload = request.input_payload["candidates"][0]
    assert payload["candidate_id"] == "S01:N01:Q1"
    assert payload["rubric_points"][1]["awarded"] == 1
    assert payload["metrics"][0]["metric_id"] == METRIC_ID
    # 分数事实只作为输入，输出没有分数字段；原候选对象不被改写。
    assert payload["rule_signal"]["score"] == 2
    assert candidate.rubric_points[1].awarded == 1


# ---------------------------------------------------------------------------
# 2. 非法题号/诊断对象被丢弃
# ---------------------------------------------------------------------------


def test_unknown_material_id_is_dropped() -> None:
    candidate = _candidate()
    bad = _valid_item(material_id="Q404", reason="凭空编造的题目")
    service = FakeService(DiagnosisOutput(items=[_valid_item(), bad], limitations=[]))

    result = diagnose_unexplained_errors([candidate], service=service)

    assert len(result.items) == 1
    assert result.items[0].material_id == "Q1"
    assert any("不存在" in text and "丢弃" in text for text in result.limitations)


def test_unknown_candidate_id_is_dropped() -> None:
    candidate = _candidate()
    bad = _valid_item(candidate_id="S99:N99:Q1")
    service = FakeService(DiagnosisOutput(items=[bad], limitations=[]))

    result = diagnose_unexplained_errors([candidate], service=service)

    assert result.items == []
    assert any("不存在" in text for text in result.limitations)


def test_ungrounded_evidence_is_dropped() -> None:
    candidate = _candidate()
    bad = _valid_item(
        evidence=[DiagnosisEvidenceOut(source="student_steps", quote="学生写了不存在的步骤")]
    )
    service = FakeService(DiagnosisOutput(items=[bad], limitations=[]))

    result = diagnose_unexplained_errors([candidate], service=service)

    assert result.items == []
    assert any("证据" in text and "丢弃" in text for text in result.limitations)


# ---------------------------------------------------------------------------
# 3. 模型失败降级为空结果，确定性结果不受影响
# ---------------------------------------------------------------------------


def test_model_failure_degrades_to_empty_result() -> None:
    candidate = _candidate()
    service = FakeService(failed=True)

    result = diagnose_unexplained_errors([candidate], service=service)

    assert result.called is True
    assert result.items == []
    assert result.error_code == "MODEL_TIMEOUT"
    assert any("MODEL_TIMEOUT" in text and "确定性" in text for text in result.limitations)
    assert candidate.rule_signal["score"] == 2


def test_unconfigured_service_does_not_call_model() -> None:
    class Unconfigured:
        configured = False

        def generate_structured(self, request, schema):  # noqa: ANN001
            raise AssertionError("未配置时不应调用模型")

    result = diagnose_unexplained_errors([_candidate()], service=Unconfigured())  # type: ignore[arg-type]

    assert result.called is False
    assert result.items == []
    assert any("未配置" in text for text in result.limitations)


# ---------------------------------------------------------------------------
# 4. 模型试图改写分数：整体拒绝，原分数不变
# ---------------------------------------------------------------------------


def test_model_cannot_rewrite_scores() -> None:
    candidate = _candidate()
    raw = {
        "items": [
            {
                "candidate_id": "S01:N01:Q1",
                "material_id": "Q1",
                "error_type": "step_error",
                "reason": "试图同时改写分数",
                "evidence": [{"source": "student_steps", "quote": "3x+6+x"}],
                "metric_ids": [METRIC_ID],
                "confidence": 0.9,
                "limitations": [],
                "score": 3,
            }
        ],
        "limitations": [],
    }
    service = FakeService(raw)

    result = diagnose_unexplained_errors([candidate], service=service)

    assert result.items == []
    assert any("改写分数" in text and "拒绝" in text for text in result.limitations)
    assert candidate.rubric_points[1].awarded == 1
    assert candidate.rule_signal["score"] == 2


# ---------------------------------------------------------------------------
# 5. 服务层接入：一次调用、只加解释、不改指标
# ---------------------------------------------------------------------------


def _prepared_and_catalog() -> tuple[PreparedDataset, Catalog, RunContext, AnalysisConfig]:
    material = Material(
        material_id="Q1",
        material_type="question",
        topic_ids=["T01"],
        indicator_ids=["I01"],
        content="化简 3(x+2)+x",
        reference_answer="4x+6",
        reference_explanation="去括号后合并同类项",
        max_score=3,
        rubric_points=[
            RubricPoint(point_id="Q1P1", description="去括号", max_score=1),
            RubricPoint(point_id="Q1P2", description="合并同类项", max_score=2),
        ],
        question_type="solution",
        source_locator=SourceLocator(
            source_file="unit",
            source_kind="standard",
            verification_status="verified",
        ),
    )
    catalog = Catalog(
        version="cat1",
        topics=[Topic(topic_id="T01", title="整式运算")],
        indicators=[Indicator(indicator_id="I01", topic_id="T01", description="整式化简")],
        materials=[material],
        links=[],
    )
    record = SourceRecord(
        record_id="R01",
        table_name="records",
        source_row_number=1,
        payload={
            "记录编号": "R01",
            "学生编号": "S01",
            "材料编号": "Q1",
            "节点编号": "N01",
            "修订版本": "1",
            "得分": "2",
            "满分": "3",
            "评分状态": "已评分",
            "学生最终作答": "4x+6",
            "解答过程": "3(x+2)+x = 3x+6+x = 4x+6",
            "评分点得分": "Q1P1=1；Q1P2=1",
            "作答事件编号": "E1",
        },
    )
    prepared = PreparedDataset(
        students=[Student(student_id="S01", class_id="C01")],
        nodes=[Node(node_id="N01", label="整式练习", topic_ids=["T01"], available_question_ids=["Q1"])],
        raw_records=[record],
        selected_events=[
            AnswerEvent(
                event_id="E1",
                student_id="S01",
                node_id="N01",
                material_id="Q1",
                selected_record_id="R01",
                status="graded",
                score=2,
                max_score=3,
                disposition="include",
            )
        ],
        quality_issues=[],
        source_manifest=SourceManifest(
            dataset_id="ds1", table_row_counts={"records": 1}, synthetic=True
        ),
    )
    context = RunContext(
        run_id="run1",
        dataset_id="ds1",
        catalog_version="cat1",
        rule_version="b-rules-test",
    )
    return prepared, catalog, context, AnalysisConfig(rule_version="b-rules-test")


class EchoDiagnosisService(FakeService):
    """按请求里的候选生成合法输出，用来验证服务层接入。"""

    def generate_structured(self, request, schema):  # noqa: ANN001
        self.requests.append(request)
        candidate = request.input_payload["candidates"][0]
        metric_ids = [metric["metric_id"] for metric in candidate["metrics"]][:1]
        value = DiagnosisOutput(
            items=[
                DiagnosisItemOut(
                    candidate_id=candidate["candidate_id"],
                    material_id=candidate["material_id"],
                    error_type="step_error",
                    reason="合并同类项时漏加 x，后续答案沿用了错误结果",
                    evidence=[
                        DiagnosisEvidenceOut(
                            source="student_steps", quote=candidate["solution_steps"]
                        )
                    ],
                    metric_ids=metric_ids,
                    confidence=0.72,
                    limitations=[],
                )
            ],
            limitations=[],
        )
        return _ModelResult(True, value)


def test_analyze_dataset_attaches_diagnosis_without_touching_metrics() -> None:
    prepared, catalog, context, config = _prepared_and_catalog()

    # 先取无模型时的确定性基线。
    baseline = analyze_dataset(prepared, catalog, context, config)
    baseline_metrics = {
        metric.metric_id: (metric.value, metric.numerator, metric.denominator)
        for metric in baseline.student_results[0].metrics
    }

    service = EchoDiagnosisService()
    bundle = analyze_dataset(prepared, catalog, context, config, service=service)

    assert len(service.requests) == 1, "单次分析最多一次模型调用"
    finding = bundle.student_results[0].findings[0]
    assert len(finding.diagnostic_details) == 1
    assert finding.generation_method == "hybrid"
    detail = finding.diagnostic_details[0]
    assert "语义诊断" in detail.summary
    assert "step_error" not in finding.error_types  # 模型不直接写正式错误类型
    assert any(ref.kind == "record" and ref.id == "R01" for ref in detail.evidence_refs)
    assert any(
        limitation.startswith("模型语义诊断只解释原因")
        for limitation in detail.limitations
    )

    current_metrics = {
        metric.metric_id: (metric.value, metric.numerator, metric.denominator)
        for metric in bundle.student_results[0].metrics
    }
    assert current_metrics == baseline_metrics


def test_analyze_dataset_model_failure_keeps_metrics_and_records_limitation() -> None:
    prepared, catalog, context, config = _prepared_and_catalog()
    baseline = analyze_dataset(prepared, catalog, context, config)
    baseline_metrics = {
        metric.metric_id: (metric.value, metric.numerator, metric.denominator)
        for metric in baseline.student_results[0].metrics
    }

    bundle = analyze_dataset(
        prepared, catalog, context, config, service=FakeService(failed=True)
    )

    finding = bundle.student_results[0].findings[0]
    assert finding.diagnostic_details == []
    assert finding.generation_method == "rule"
    assert any("MODEL_TIMEOUT" in text for text in bundle.student_results[0].limitations)
    current_metrics = {
        metric.metric_id: (metric.value, metric.numerator, metric.denominator)
        for metric in bundle.student_results[0].metrics
    }
    assert current_metrics == baseline_metrics


def test_collect_skips_objective_questions() -> None:
    prepared, catalog, _, _ = _prepared_and_catalog()
    # 把题目改成填空且无过程：不推断根因。
    catalog.materials[0] = catalog.materials[0].model_copy(
        update={"question_type": "fill_blank"}
    )
    collection = collect_unexplained_errors(prepared, catalog)
    assert collection.candidates == []


def test_collect_skips_conflicting_rubric_scores() -> None:
    prepared, catalog, _, _ = _prepared_and_catalog()
    record = prepared.raw_records[0]
    prepared.raw_records[0] = record.model_copy(
        update={"payload": {**record.payload, "评分点得分": "Q1P1=1"}}
    )
    collection = collect_unexplained_errors(prepared, catalog)
    assert collection.candidates == []
    assert any("证据冲突" in text for text in collection.limitations)
