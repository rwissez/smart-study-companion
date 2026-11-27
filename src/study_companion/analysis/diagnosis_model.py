"""错因的语义诊断（03 §3.1、§6.2—§6.7）。

分层原则：程序只回答"哪里丢了分"，模型只回答"可能为什么错"。

* 候选由程序确定：只取已评分、未满分、有书面解答过程、评分点与得分不冲突的
  解答题事件；题目、正确答案、评分点得失、规则信号和指标全部来自可信数据。
* 模型输出受严格 Pydantic schema 约束：一条候选一条诊断，必须给出受控
  ``error_type``、理由、可定位的原文证据、0—1 置信度和限制说明。
* 程序回验：``candidate_id``/``material_id`` 必须存在，指标必须来自输入，
  证据必须能在题目或学生原文中定位；任何一项不通过就丢弃该条。schema 不含
  分数字段，模型多写 ``score`` 会触发 ``extra="forbid"`` 并整体拒绝。
* 模型失败或输出不合法时返回空结果并记录 limitation，确定性统计与评分不受
  影响。模型结论只作为解释性 ``DiagnosticDetail``，不写入正式错误类型。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from study_companion.analysis.strategy_signal import parse_rubric_scores
from study_companion.contracts import (
    AnswerEvent,
    Catalog,
    Material,
    PreparedDataset,
    StudentResult,
)
from study_companion.model_service import ModelService, ModelTaskRequest

TASK_KEY = "diagnose_errors.v1"
PROMPT_VERSION = "1.0"
PROMPT_NAMESPACE = "b"

#: 单次分析最多送给模型的候选数；超出的失分题保留确定性结果，不生成语义解释。
MAX_CANDIDATES = 12
#: 每个候选最多引用几个指标，避免把整张画像塞进一次调用。
MAX_METRICS_PER_CANDIDATE = 6

#: 与 01 公共 ErrorType 对齐，并额外允许 uncertain（证据不足，不作定论）。
DIAGNOSIS_ERROR_TYPES = (
    "foundation_gap",
    "step_error",
    "hint_dependency",
    "transfer_difficulty",
    "other_supported",
    "uncertain",
)

DiagnosisErrorType = Literal[
    "foundation_gap",
    "step_error",
    "hint_dependency",
    "transfer_difficulty",
    "other_supported",
    "uncertain",
]

EvidenceSource = Literal[
    "question",
    "student_answer",
    "student_steps",
    "reference_answer",
    "rubric_point",
]


# ---------------------------------------------------------------------------
# 严格输出 schema（模型只能填这些字段）
# ---------------------------------------------------------------------------


class DiagnosisEvidenceOut(BaseModel):
    """一条原文证据：引用哪段材料、原话是什么。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    source: EvidenceSource
    quote: str = Field(min_length=1)
    #: 仅 source=rubric_point 时使用；必须是输入里真实存在的评分点编号。
    point_id: str | None = None


class DiagnosisItemOut(BaseModel):
    """一道题（一个错误信号）的语义诊断。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    candidate_id: str = Field(min_length=1)
    material_id: str = Field(min_length=1)
    error_type: DiagnosisErrorType
    reason: str = Field(min_length=1)
    evidence: list[DiagnosisEvidenceOut] = Field(min_length=1)
    #: 只能引用输入 metrics 中的 metric_id；模型不得编造指标。
    metric_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    #: 必须显式给出；信息不足时写清缺什么证据。
    limitations: list[str]


class DiagnosisOutput(BaseModel):
    """模型任务的完整输出。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    items: list[DiagnosisItemOut] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 程序侧输入：候选、评分点得失、指标与规则信号
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DiagnosisRubricPoint:
    point_id: str
    description: str
    max_score: int
    #: 来自评分记录；缺失时为 None，不猜 0 分。
    awarded: int | None = None


@dataclass(frozen=True)
class DiagnosisMetric:
    metric_id: str
    name: str
    value: float | None
    numerator: int | None
    denominator: int | None
    indicator_id: str


@dataclass(frozen=True)
class DiagnosisCandidate:
    """一道"规则未能解释原因"的失分题。"""

    candidate_id: str
    student_id: str
    node_id: str
    material_id: str
    indicator_ids: tuple[str, ...]
    question: str
    question_type: str
    student_answer: str
    solution_steps: str
    reference_answer: str | None
    rubric_points: tuple[DiagnosisRubricPoint, ...]
    rule_signal: dict[str, Any]
    metrics: tuple[DiagnosisMetric, ...]
    record_id: str | None = None

    def to_payload(self) -> dict[str, Any]:
        """送模的严格输入；字段与提示词一一对应。"""

        return {
            "candidate_id": self.candidate_id,
            "material_id": self.material_id,
            "question": self.question,
            "question_type": self.question_type,
            "student_answer": self.student_answer,
            "solution_steps": self.solution_steps,
            "reference_answer": self.reference_answer,
            "rubric_points": [
                {
                    "point_id": point.point_id,
                    "description": point.description,
                    "max_score": point.max_score,
                    "awarded": point.awarded,
                }
                for point in self.rubric_points
            ],
            "rule_signal": dict(self.rule_signal),
            "metrics": [
                {
                    "metric_id": metric.metric_id,
                    "indicator_id": metric.indicator_id,
                    "name": metric.name,
                    "value": metric.value,
                    "numerator": metric.numerator,
                    "denominator": metric.denominator,
                }
                for metric in self.metrics
            ],
        }


@dataclass
class DiagnosisCollection:
    """候选收集结果；限制说明供服务层原样记录。"""

    candidates: list[DiagnosisCandidate] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    #: 因单次上限被截断的 (student_id, node_id)，服务层据此补限制说明。
    omitted_scopes: list[tuple[str, str]] = field(default_factory=list)
    #: 因评分点与得分冲突而未送诊断的 (student_id, node_id)。
    conflicting_scopes: list[tuple[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class DiagnosisItem:
    """程序校验通过的诊断结论。"""

    candidate_id: str
    student_id: str
    node_id: str
    material_id: str
    indicator_ids: tuple[str, ...]
    record_id: str | None
    error_type: str
    reason: str
    evidence: tuple[DiagnosisEvidenceOut, ...]
    metric_ids: tuple[str, ...]
    confidence: float
    limitations: tuple[str, ...]


@dataclass
class DiagnosisResult:
    """一次语义诊断调用的结果；失败时 items 为空。"""

    items: list[DiagnosisItem] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    called: bool = False
    prompt_version: str = PROMPT_VERSION
    error_code: str | None = None
    usage: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# 规范化与候选收集
# ---------------------------------------------------------------------------


def _normalise(text: str | None) -> str:
    if not text:
        return ""
    value = unicodedata.normalize("NFKC", text)
    value = value.replace("，", ",").replace("。", ".")
    return re.sub(r"\s+", "", value).lower()


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _metrics_index(
    student_results: Iterable[StudentResult] | None,
) -> dict[tuple[str, str, str], list[DiagnosisMetric]]:
    index: dict[tuple[str, str, str], list[DiagnosisMetric]] = {}
    for result in student_results or []:
        for metric in result.metrics:
            indicator_id = metric.scope.indicator_id
            if not indicator_id:
                continue
            index.setdefault((result.student_id, result.node_id, indicator_id), []).append(
                DiagnosisMetric(
                    metric_id=metric.metric_id,
                    name=metric.name,
                    value=metric.value,
                    numerator=metric.numerator,
                    denominator=metric.denominator,
                    indicator_id=indicator_id,
                )
            )
    return index


def _candidate_id(student_id: str, node_id: str, material_id: str) -> str:
    return f"{student_id}:{node_id}:{material_id}"


def _lost_points(points: tuple[DiagnosisRubricPoint, ...]) -> list[dict[str, Any]]:
    return [
        {
            "point_id": point.point_id,
            "description": point.description,
            "awarded": point.awarded,
            "max_score": point.max_score,
        }
        for point in points
        if point.awarded is not None and point.awarded < point.max_score
    ]


def _rule_signal(
    *,
    material: Material,
    event: AnswerEvent,
    student_answer: str,
    points: tuple[DiagnosisRubricPoint, ...],
) -> dict[str, Any]:
    """规则层已有的确定性信号：只定位失分，不解释原因。"""

    answer_matches = bool(_normalise(student_answer)) and _normalise(
        student_answer
    ) == _normalise(material.reference_answer)
    lost = _lost_points(points)
    if answer_matches:
        return {
            "error_type": "step_error",
            "evidence_status": "sufficient",
            "reason": "最终答案与参考答案一致但未得满分，规则层判断为步骤/过程失分，具体原因未解释",
            "lost_points": lost,
            "score": event.score,
            "max_score": event.max_score,
        }
    return {
        "error_type": "undetermined",
        "evidence_status": "insufficient",
        "reason": "规则层只能定位失分评分点，无法从文字过程判断错误原因",
        "lost_points": lost,
        "score": event.score,
        "max_score": event.max_score,
    }


def collect_unexplained_errors(
    data: PreparedDataset,
    catalog: Catalog,
    *,
    student_results: Iterable[StudentResult] | None = None,
    limit: int = MAX_CANDIDATES,
) -> DiagnosisCollection:
    """收集"有书面过程、有失分、但规则层未解释原因"的解答题事件。

    客观题没有过程时不推断根因（03 §6.6）；评分点之和与得分冲突时先保留
    冲突事实，不送模型。相同学生/节点/题目只保留一条。
    """

    materials = {material.material_id: material for material in catalog.materials}
    records = {
        record.record_id: record.payload
        for record in data.raw_records
        if record.table_name == "records"
    }
    metrics = _metrics_index(student_results)

    candidates: list[DiagnosisCandidate] = []
    limitations: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    conflicting = 0
    conflicting_scopes: set[tuple[str, str]] = set()

    for event in data.selected_events:
        if event.status != "graded" or event.disposition != "include":
            continue
        material = materials.get(event.material_id)
        if (
            material is None
            or material.material_type != "question"
            or material.question_type != "solution"
        ):
            continue
        if event.score is None or event.max_score is None or event.score >= event.max_score:
            continue
        raw = records.get(event.selected_record_id or "")
        if not raw:
            continue
        steps = _text(raw.get("解答过程")).strip()
        if not steps:
            # 只有最终答案、没有过程时不推断根因。
            continue
        key = (event.student_id, event.node_id, event.material_id)
        if key in seen:
            continue

        awarded = parse_rubric_scores(raw.get("评分点得分"))
        if awarded and sum(awarded.values()) != event.score:
            conflicting += 1
            conflicting_scopes.add((event.student_id, event.node_id))
            continue
        points = tuple(
            DiagnosisRubricPoint(
                point_id=point.point_id,
                description=point.description,
                max_score=point.max_score,
                awarded=awarded.get(point.point_id),
            )
            for point in material.rubric_points
        )
        indicator_ids = tuple(material.indicator_ids)
        metric_facts: list[DiagnosisMetric] = []
        for indicator_id in indicator_ids:
            for metric in metrics.get((event.student_id, event.node_id, indicator_id), []):
                if len(metric_facts) >= MAX_METRICS_PER_CANDIDATE:
                    break
                metric_facts.append(metric)
        student_answer = _text(raw.get("学生最终作答"))
        seen.add(key)
        candidates.append(
            DiagnosisCandidate(
                candidate_id=_candidate_id(event.student_id, event.node_id, event.material_id),
                student_id=event.student_id,
                node_id=event.node_id,
                material_id=event.material_id,
                indicator_ids=indicator_ids,
                question=material.content,
                question_type=material.question_type,
                student_answer=student_answer,
                solution_steps=steps,
                reference_answer=material.reference_answer,
                rubric_points=points,
                rule_signal=_rule_signal(
                    material=material,
                    event=event,
                    student_answer=student_answer,
                    points=points,
                ),
                metrics=tuple(metric_facts),
                record_id=event.selected_record_id,
            )
        )

    if conflicting:
        limitations.append(
            f"{conflicting}道题的评分点之和与得分不一致，证据冲突，未送语义诊断，待教师复核"
        )

    omitted_scopes: list[tuple[str, str]] = []
    if len(candidates) > limit:
        dropped = candidates[limit:]
        candidates = candidates[:limit]
        omitted_scopes = sorted({(item.student_id, item.node_id) for item in dropped})
        limitations.append(
            f"本次有{len(dropped)}道未解释失分题超出单次语义诊断上限{limit}道，"
            "保留确定性统计，未生成模型解释"
        )
    return DiagnosisCollection(
        candidates=candidates,
        limitations=limitations,
        omitted_scopes=omitted_scopes,
        conflicting_scopes=sorted(conflicting_scopes),
    )


# ---------------------------------------------------------------------------
# 证据与程序校验
# ---------------------------------------------------------------------------


def _candidate_texts(candidate: DiagnosisCandidate) -> dict[str, str]:
    return {
        "question": candidate.question,
        "student_answer": candidate.student_answer,
        "student_steps": candidate.solution_steps,
        "reference_answer": candidate.reference_answer or "",
    }


def _validate_evidence(
    evidence: list[DiagnosisEvidenceOut], candidate: DiagnosisCandidate
) -> bool:
    """证据必须非空，且能在原始材料/学生作答中逐字定位。"""

    point_ids = {point.point_id for point in candidate.rubric_points}
    texts = _candidate_texts(candidate)
    for item in evidence:
        quote = item.quote.strip()
        if not quote:
            return False
        if item.point_id is not None and item.point_id not in point_ids:
            return False
        if item.source == "rubric_point":
            if item.point_id is None:
                return False
            continue
        source_text = texts.get(item.source, "")
        if not _normalise(source_text) or _normalise(quote) not in _normalise(source_text):
            return False
        if item.source == "reference_answer" and not candidate.reference_answer:
            return False
    return True


def _validation_limitation(exc: ValidationError) -> str:
    errors = exc.errors()
    fields = [
        ".".join(str(part) for part in item.get("loc", ())) for item in errors
    ]
    if any("score" in field for field in fields):
        return "模型试图改写分数（输出含分数字段），语义诊断已整体拒绝，分数保持不变"
    detail = "；".join(
        f"{field or '根对象'}: {item.get('msg', '')}"
        for field, item in zip(fields, errors)
    )
    return f"模型输出不符合严格结构，语义诊断已整体丢弃：{detail}"


def _validate_output(
    value: Any, candidates: list[DiagnosisCandidate]
) -> tuple[list[DiagnosisItem], list[str]]:
    if isinstance(value, BaseModel):
        raw = value.model_dump(mode="json")
    else:
        raw = value
    try:
        output = DiagnosisOutput.model_validate(raw)
    except ValidationError as exc:
        return [], [_validation_limitation(exc)]
    except (TypeError, ValueError):
        return [], ["模型输出无法解析为语义诊断结构，已整体丢弃"]

    by_id = {candidate.candidate_id: candidate for candidate in candidates}
    items: list[DiagnosisItem] = []
    limitations: list[str] = []
    seen: set[str] = set()
    unknown = 0
    duplicated = 0
    ungrounded = 0
    unknown_metrics = 0

    for item in output.items:
        candidate = by_id.get(item.candidate_id)
        if candidate is None or item.material_id != candidate.material_id:
            unknown += 1
            continue
        if candidate.candidate_id in seen:
            duplicated += 1
            continue
        if not item.reason.strip():
            ungrounded += 1
            continue
        if not _validate_evidence(item.evidence, candidate):
            ungrounded += 1
            continue
        allowed_metrics = {metric.metric_id for metric in candidate.metrics}
        if any(metric_id not in allowed_metrics for metric_id in item.metric_ids):
            unknown_metrics += 1
            continue
        item_limitations = [text.strip() for text in item.limitations if text and text.strip()]
        if item.error_type == "uncertain" and not item_limitations:
            item_limitations = ["模型标记为不确定，未给出具体限制说明"]
        seen.add(candidate.candidate_id)
        items.append(
            DiagnosisItem(
                candidate_id=candidate.candidate_id,
                student_id=candidate.student_id,
                node_id=candidate.node_id,
                material_id=candidate.material_id,
                indicator_ids=candidate.indicator_ids,
                record_id=candidate.record_id,
                error_type=item.error_type,
                reason=item.reason.strip(),
                evidence=tuple(item.evidence),
                metric_ids=tuple(item.metric_ids),
                confidence=item.confidence,
                limitations=tuple(item_limitations),
            )
        )

    if unknown:
        limitations.append(
            f"模型返回了输入中不存在的题目或诊断对象，已丢弃{unknown}条，不写入学情结果"
        )
    if duplicated:
        limitations.append(f"模型对同一题目重复诊断，已丢弃{duplicated}条重复项")
    if ungrounded:
        limitations.append(
            f"模型给出的原因缺少可定位的原文证据（或理由为空），已丢弃{ungrounded}条"
        )
    if unknown_metrics:
        limitations.append(
            f"模型引用了输入中不存在的指标，已丢弃{unknown_metrics}条，不采信该结论"
        )
    if output.limitations:
        limitations.extend(
            f"模型自述限制：{text.strip()}"
            for text in output.limitations
            if text and text.strip()
        )
    if not items and not limitations:
        limitations.append("模型未返回可用的错因诊断，已保留确定性统计")
    return items, limitations


# ---------------------------------------------------------------------------
# 模型调用入口
# ---------------------------------------------------------------------------


def diagnose_unexplained_errors(
    candidates: list[DiagnosisCandidate],
    *,
    service: ModelService,
) -> DiagnosisResult:
    """一次批量调用模型解释全部候选；失败或输出不合法时返回空结果。"""

    if not candidates:
        return DiagnosisResult(limitations=["没有需要语义解释的未解释失分题"])
    if not getattr(service, "configured", True):
        return DiagnosisResult(limitations=["模型未配置，未生成错因语义诊断"])

    request = ModelTaskRequest(
        task_key=TASK_KEY,
        prompt_version=PROMPT_VERSION,
        namespace=PROMPT_NAMESPACE,
        input_payload={"candidates": [candidate.to_payload() for candidate in candidates]},
        context_refs=[
            {"kind": "record", "id": candidate.record_id}
            for candidate in candidates
            if candidate.record_id
        ],
        allow_external=False,
    )
    try:
        result = service.generate_structured(request, DiagnosisOutput)
    except Exception as exc:  # 模型适配层任何异常都不得影响确定性结果
        return DiagnosisResult(
            limitations=[
                f"语义诊断调用异常（{type(exc).__name__}），已保留确定性统计与评分，不生成模型解释"
            ],
            called=True,
        )

    if not getattr(result, "succeeded", False) or getattr(result, "value", None) is None:
        error = getattr(result, "error", None) or {}
        code = error.get("code", "MODEL_BAD_RESPONSE")
        return DiagnosisResult(
            limitations=[
                f"模型语义诊断未成功（{code}），已保留确定性统计与评分，不生成模型解释"
            ],
            called=True,
            error_code=code,
            usage=getattr(result, "usage", None),
        )

    items, limitations = _validate_output(result.value, candidates)
    return DiagnosisResult(
        items=items,
        limitations=limitations,
        called=True,
        usage=getattr(result, "usage", None),
    )
