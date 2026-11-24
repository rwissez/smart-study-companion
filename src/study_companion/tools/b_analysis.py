"""B模块工具包装器（对应09文档工具目录）。

四个工具：get_student_analysis / get_class_analysis / get_quality_issues / get_answer_evidence。
- 统一返回 ToolResult；失败 data=None、error 含 code/message。
- 隐式接收服务端 Context（run_id/dataset_id/catalog_version/allowed_student_ids），
  显式参数仅选择该范围内的对象；越界请求一律拒绝，模型不能自行扩大数据范围。
- 统计逻辑只有 analyze_dataset 一份，工具只做查询与范围校验，不复制计算。
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field

from study_companion.contracts import (
    AnalysisBundle,
    AnswerEvent,
    ClassResult,
    DomainError,
    PreparedDataset,
    QualityIssue,
    StudentResult,
)

PAGE_SIZE = 20


@dataclass(frozen=True)
class ServerContext:
    """服务端绑定的运行上下文；由A的api层构造，模型不可修改。"""

    run_id: str
    dataset_id: str
    catalog_version: str
    allowed_student_ids: frozenset[str]
    bundle: AnalysisBundle
    dataset: PreparedDataset


class AnswerEvidenceItem(BaseModel):
    """单条作答证据：原始行 + 所属事件 + 处置说明。"""

    record_id: str
    payload: dict
    source_row_number: int
    selected_event: dict | None = None
    disposition_note: str


class ToolResult(BaseModel):
    """工具统一返回结构（09文档），成功 data 有值 error 为 null；失败相反。"""

    call_id: str
    tool_name: str
    status: str = Field(pattern="^(succeeded|failed)$")
    data: BaseModel | list[BaseModel] | None = None
    error: dict[str, str] | None = None
    evidence_refs: list[dict[str, str]] = Field(default_factory=list)


def _fail(call_id: str, name: str, code: str, message: str) -> ToolResult:
    return ToolResult(call_id=call_id, tool_name=name, status="failed",
                      error={"code": code, "message": message})


def _ok(call_id: str, name: str, data, refs: list[dict[str, str]]) -> ToolResult:
    return ToolResult(call_id=call_id, tool_name=name, status="succeeded",
                      data=data, evidence_refs=refs)


# ---------------------------------------------------------------------------
# 工具实现
# ---------------------------------------------------------------------------

def get_student_analysis(ctx: ServerContext, call_id: str,
                         student_id: str, node_id: str) -> ToolResult:
    """查询指定学生的分析结果（含版本信息）。"""
    if student_id not in ctx.allowed_student_ids:
        return _fail(call_id, "get_student_analysis", "SCOPE_DENIED",
                     f"学生 {student_id} 不在当前会话允许范围内")
    result = next(
        (r for r in ctx.bundle.student_results
         if r.student_id == student_id and r.node_id == node_id),
        None,
    )
    if result is None:
        return _fail(call_id, "get_student_analysis", "NOT_FOUND",
                     f"学生 {student_id} 在节点 {node_id} 无分析结果")
    return _ok(
        call_id, "get_student_analysis",
        _with_version(result, ctx),
        [{"kind": "metric", "id": m.metric_id, "run_id": ctx.run_id}
         for m in result.metrics],
    )


def get_class_analysis(ctx: ServerContext, call_id: str,
                       class_id: str, node_id: str) -> ToolResult:
    """查询指定班级的分析结果（含版本信息）。学生角色会话不可调用。"""
    result = next(
        (r for r in ctx.bundle.class_results
         if r.class_id == class_id and r.node_id == node_id),
        None,
    )
    if result is None:
        return _fail(call_id, "get_class_analysis", "NOT_FOUND",
                     f"班级 {class_id} 在节点 {node_id} 无分析结果")
    return _ok(
        call_id, "get_class_analysis",
        _with_version(result, ctx),
        [{"kind": "metric", "id": m.metric_id, "run_id": ctx.run_id}
         for m in result.metrics],
    )


def get_quality_issues(ctx: ServerContext, call_id: str,
                       student_id: str | None = None, page: int = 1) -> ToolResult:
    """查询当前范围的质量问题，分页返回；student_id 可选过滤。"""
    if page < 1:
        return _fail(call_id, "get_quality_issues", "INVALID_ARGUMENT", "page 必须 >= 1")
    if student_id is not None and student_id not in ctx.allowed_student_ids:
        return _fail(call_id, "get_quality_issues", "SCOPE_DENIED",
                     f"学生 {student_id} 不在当前会话允许范围内")

    records = {r.record_id: r for r in ctx.dataset.raw_records}
    event_students = {
        event.selected_record_id: event.student_id
        for event in ctx.dataset.selected_events
        if event.selected_record_id is not None
    }

    def record_student(record_id: str) -> str | None:
        if record_id in event_students:
            return event_students[record_id]
        record = records.get(record_id)
        if record is None:
            return None
        return record.payload.get("学生编号") or record.payload.get("学号") or record.payload.get("student_id")

    issues: list[QualityIssue] = []
    for issue in ctx.dataset.quality_issues:
        if student_id is None:
            issues.append(issue)
            continue
        if any(record_student(record_id) == student_id for record_id in issue.record_ids):
            issues.append(issue)

    start = (page - 1) * PAGE_SIZE
    chunk = issues[start:start + PAGE_SIZE]
    record_ids = {
        record_id for issue in chunk for record_id in issue.record_ids
        if student_id is None or record_student(record_id) == student_id
    }
    return _ok(call_id, "get_quality_issues", chunk or [],
               [{"kind": "record", "id": record_id, "run_id": ctx.run_id}
                for record_id in sorted(record_ids)])


def get_answer_evidence(ctx: ServerContext, call_id: str,
                        record_ids: list[str]) -> ToolResult:
    """按记录编号查询原始作答及处置说明；只能查当前范围内学生的记录。"""
    if not record_ids:
        return _fail(call_id, "get_answer_evidence", "INVALID_ARGUMENT", "record_ids 不能为空")

    records = {r.record_id: r for r in ctx.dataset.raw_records}
    events_by_record = {e.selected_record_id: e for e in ctx.dataset.selected_events}

    evidence: list[AnswerEvidenceItem] = []
    for record_id in record_ids:
        record = records.get(record_id)
        if record is None:
            return _fail(call_id, "get_answer_evidence", "NOT_FOUND",
                         f"记录 {record_id} 不存在")
        event = events_by_record.get(record_id)
        student_id = (
            event.student_id if event else
            record.payload.get("学生编号") or record.payload.get("学号") or record.payload.get("student_id")
        )
        if student_id not in ctx.allowed_student_ids:
            return _fail(call_id, "get_answer_evidence", "SCOPE_DENIED",
                         f"记录 {record_id} 属于范围外学生，拒绝访问")
        evidence.append(AnswerEvidenceItem(
            record_id=record_id,
            payload=record.payload,
            source_row_number=record.source_row_number,
            selected_event=event.model_dump() if event else None,
            disposition_note=_disposition_note(event),
        ))
    return _ok(call_id, "get_answer_evidence", evidence,
               [{"kind": "record", "id": rid, "run_id": ctx.run_id} for rid in record_ids])


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

class _Versioned(BaseModel):
    """结果本体 + 运行版本标识，保证消费方可核对数据来源。"""

    result: BaseModel
    run_id: str
    dataset_id: str
    catalog_version: str


def _with_version(result: StudentResult | ClassResult, ctx: ServerContext) -> _Versioned:
    return _Versioned(result=result, run_id=ctx.run_id,
                      dataset_id=ctx.dataset_id, catalog_version=ctx.catalog_version)


def _disposition_note(event: AnswerEvent | None) -> str:
    if event is None:
        return "该记录未被任何事件选中（历史修订版本或未关联）"
    if event.disposition == "exclude_score":
        return "分数已排除：得分超过满分或缺少满分，原值保留在记录中"
    if event.disposition == "quarantine_event":
        return "事件已隔离：同一版本记录冲突，双方均可查看，不进入统计"
    if event.status == "pending":
        return "待评分：分数为空是状态而非零分，不进入得分统计"
    if event.status == "unanswered":
        return "未作答：无学生作答内容"
    return "正常纳入统计"


# ---------------------------------------------------------------------------
# 工具注册表（A 的 agent 循环按名称调用；参数校验后进入各包装器）
# ---------------------------------------------------------------------------

TOOL_REGISTRY: dict[str, callable] = {
    "get_student_analysis": get_student_analysis,
    "get_class_analysis": get_class_analysis,
    "get_quality_issues": get_quality_issues,
    "get_answer_evidence": get_answer_evidence,
}
