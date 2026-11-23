"""prepare_dataset：六表结构校验、字段映射、修订版本选择与异常隔离。

设计原则（对应B工作指南与01文档"数据处理顺序"）：
- 原始数据只读不改：原始行原样进入 raw_records，问题通过 QualityIssue 与 disposition 标记。
- 不自动修正：超满分不裁剪、同版冲突不猜测、最新版未评分不回退旧分。
- 每个事件有处置日志，导入前后数量可对账（source_manifest）。
"""

from __future__ import annotations

import re

import hashlib
import json

from study_companion.contracts import (
    AnswerEvent,
    DomainError,
    ImportConfig,
    Node,
    PreparedDataset,
    QualityIssue,
    RawRow,
    RawTables,
    SourceManifest,
    SourceRecord,
    Student,
)

# ---------------------------------------------------------------------------
# 字段映射：中文列名 -> 程序字段（列名以01文档"原字段映射"为准，兼容合成样例的简写）
# ---------------------------------------------------------------------------

STUDENTS_COLUMNS: dict[str, tuple[str, ...]] = {
    "student_id": ("学生编号", "学号", "学生ID"),
    "class_id": ("模拟班级编号", "班级编号", "班级"),
    "grade": ("年级",),
}
NODES_COLUMNS: dict[str, tuple[str, ...]] = {
    "node_id": ("节点编号",),
}
MATERIALS_COLUMNS: dict[str, tuple[str, ...]] = {
    "material_id": ("材料编号",),
    "indicator_ids": ("指标编号",),
}
REQUIREMENTS_COLUMNS: dict[str, tuple[str, ...]] = {
    "indicator_id": ("指标编号",),
    "topic_id": ("主题编号",),
}
RECORDS_COLUMNS: dict[str, tuple[str, ...]] = {
    "record_id": ("记录编号",),
    "student_id": ("学生编号", "学号"),
    "material_id": ("材料编号",),
    "topic_id": ("主题编号",),
    "node_id": ("节点编号",),
    "event_id": ("作答事件编号",),
    "revision": ("修订版本",),
    "submitted_at": ("模拟提交时间",),
    "grading_status": ("评分状态",),
    "score": ("得分",),
    "max_score": ("满分",),
    "final_answer": ("学生最终作答",),
    "solution_steps": ("解答过程",),
    "rubric_scores": ("评分点得分",),
}
DICTIONARY_COLUMNS: dict[str, tuple[str, ...]] = {
    "field": ("字段",),
    "meaning": ("含义",),
}

_STATUS_GRADED = ("已评分", "graded")
_STATUS_PENDING = ("待评分", "待评", "pending")
_STATUS_UNANSWERED = ("未作答", "unanswered")


class _ParsedRecord:
    """一条作答记录的解析视图；原始行保存在 record 中。"""

    def __init__(self, record: SourceRecord, row: RawRow):
        self.record = record
        self.student_id = _pick(row, RECORDS_COLUMNS["student_id"]) or ""
        self.material_id = _pick(row, RECORDS_COLUMNS["material_id"]) or ""
        self.node_id = _pick(row, RECORDS_COLUMNS["node_id"]) or ""
        self.event_id = _pick(row, RECORDS_COLUMNS["event_id"])
        self.revision = _to_int(_pick(row, RECORDS_COLUMNS["revision"]))
        self.grading_status = _pick(row, RECORDS_COLUMNS["grading_status"])
        self.score = _to_int(_pick(row, RECORDS_COLUMNS["score"]))
        self.max_score = _to_int(_pick(row, RECORDS_COLUMNS["max_score"]))
        self.final_answer = _pick(row, RECORDS_COLUMNS["final_answer"])


def _pick(row: RawRow, aliases: tuple[str, ...]) -> str | None:
    for name in aliases:
        value = row.get(name)
        if value is not None and value != "":
            return value
    return None


def _to_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def parse_node_questions(value: str | None) -> list[str] | None:
    if value is None or not value.strip():
        return None
    return [item for item in re.split(r"[、,，;；\s]+", value.strip()) if item]


def _require_columns(
    table_name: str,
    rows: list[RawRow],
    columns: dict[str, tuple[str, ...]],
    required: tuple[str, ...],
) -> None:
    """结构校验：必需字段的任一别名列名都不存在则拒收整包。"""
    if not rows:
        return
    present = set(rows[0].keys())
    missing: list[dict[str, str]] = []
    for field in required:
        if not any(alias in present for alias in columns[field]):
            missing.append({"table": table_name, "field": field, "expected_columns": "/".join(columns[field])})
    if missing:
        raise DomainError("STRUCTURE_INVALID", f"{table_name} 表缺少必需字段", missing)


def _issue_counter():
    count = 0

    def next_id() -> str:
        nonlocal count
        count += 1
        return f"QI{count:03d}"

    return next_id


def prepare_dataset(tables: RawTables, config: ImportConfig) -> PreparedDataset:
    """六表原始数据 -> 结构校验、去重、版本选择、异常隔离后的数据集。"""
    next_issue_id = _issue_counter()
    issues: list[QualityIssue] = []

    # 1. 结构校验（缺失必需列时整包拒收，由A转成08错误包络）
    _require_columns("students", tables.students, STUDENTS_COLUMNS, ("student_id", "class_id"))
    _require_columns("nodes", tables.nodes, NODES_COLUMNS, ("node_id",))
    _require_columns("materials", tables.materials, MATERIALS_COLUMNS, ("material_id",))
    _require_columns("records", tables.records, RECORDS_COLUMNS, ("record_id", "student_id", "material_id", "node_id"))

    students = _build_students(tables.students)
    nodes_by_id = {}
    for row in tables.nodes:
        node_id = _pick(row, NODES_COLUMNS["node_id"])
        if node_id:
            nodes_by_id.setdefault(node_id, Node(
                node_id=node_id, label=row.get("说明"),
                available_question_ids=parse_node_questions(row.get("可用题目编号")),
            ))
    nodes = list(nodes_by_id.values())

    # 2. 保存全部原始行（含 source_row_number，保证可追溯）
    #    records 表保留原始记录编号（与 selected_record_id/quality_issues 的引用一致）；
    #    其余表无编号列，用 表名:行号 作为 record_id。
    raw_records = []
    for table, rows in (
        ("requirements", tables.requirements),
        ("materials", tables.materials),
        ("students", tables.students),
        ("nodes", tables.nodes),
        ("records", tables.records),
        ("dictionary", tables.dictionary),
    ):
        for index, row in enumerate(rows, start=1):
            if table == "records":
                record_id = _pick(row, RECORDS_COLUMNS["record_id"]) or f"records:{index}"
            else:
                record_id = f"{table}:{index}"
            raw_records.append(
                SourceRecord(
                    record_id=record_id,
                    table_name=table,
                    source_row_number=index,
                    payload=row,
                )
            )

    parsed = _parse_records(tables.records, next_issue_id, issues)
    selected_events = _select_events(parsed, config, next_issue_id, issues)
    question_scope = {node.node_id: set(node.available_question_ids) for node in nodes if node.available_question_ids is not None}
    for event in selected_events:
        if event.node_id in question_scope and event.material_id not in question_scope[event.node_id]:
            issues.append(QualityIssue(
                issue_id=next_issue_id(), code="OUTSIDE_NODE_SCOPE", severity="warning",
                record_ids=[event.selected_record_id] if event.selected_record_id else [],
                event_id=event.event_id,
                message="作答所用题目不在该测验节点的可用清单中。保留原记录，学情分析排除此条。",
                action="retain", rule_version=config.rule_version,
            ))

    manifest = SourceManifest(
        dataset_id=_dataset_id(tables),
        table_row_counts={
            "requirements": len(tables.requirements),
            "materials": len(tables.materials),
            "students": len(tables.students),
            "nodes": len(tables.nodes),
            "records": len(tables.records),
            "dictionary": len(tables.dictionary),
        },
        synthetic=False,
    )
    return PreparedDataset(
        students=students,
        nodes=nodes,
        raw_records=raw_records,
        selected_events=selected_events,
        quality_issues=issues,
        source_manifest=manifest,
    )


def _build_students(rows: list[RawRow]) -> list[Student]:
    seen: set[str] = set()
    students: list[Student] = []
    for row in rows:
        student_id = _pick(row, STUDENTS_COLUMNS["student_id"])
        class_id = _pick(row, STUDENTS_COLUMNS["class_id"]) or ""
        if not student_id or student_id in seen:
            continue
        seen.add(student_id)
        students.append(
            Student(
                student_id=student_id,
                class_id=class_id,
                grade=_pick(row, STUDENTS_COLUMNS["grade"]),
            )
        )
    return students


def _table_ids(rows: list[RawRow], aliases: tuple[str, ...]) -> list[str]:
    seen: set[str] = set()
    ids: list[str] = []
    for row in rows:
        value = _pick(row, aliases)
        if value and value not in seen:
            seen.add(value)
            ids.append(value)
    return ids


def _parse_records(
    rows: list[RawRow], next_issue_id, issues: list[QualityIssue]
) -> list[_ParsedRecord]:
    """构造解析视图，并处理记录编号重复：完全重复去重保留，同号不同内容隔离。"""
    by_record_id: dict[str, list[_ParsedRecord]] = {}
    order: list[str] = []
    for index, row in enumerate(rows, start=1):
        record = SourceRecord(
            record_id=_pick(row, RECORDS_COLUMNS["record_id"]) or f"records:{index}",
            table_name="records",
            source_row_number=index,
            payload=row,
        )
        parsed = _ParsedRecord(record, row)
        if record.record_id not in by_record_id:
            by_record_id[record.record_id] = []
            order.append(record.record_id)
        by_record_id[record.record_id].append(parsed)

    result: list[_ParsedRecord] = []
    for record_id in order:
        group = by_record_id[record_id]
        if len(group) == 1:
            result.append(group[0])
            continue
        if all(_same_payload(g.record.payload, group[0].record.payload) for g in group[1:]):
            issues.append(
                QualityIssue(
                    issue_id=next_issue_id(),
                    code="DUPLICATE_RECORD",
                    severity="info",
                    record_ids=[g.record.record_id for g in group],
                    message=f"记录 {record_id} 完全重复，去重保留首条",
                    action="retain",
                    rule_version="import-rules-v1",
                )
            )
            result.append(group[0])
        else:
            issues.append(
                QualityIssue(
                    issue_id=next_issue_id(),
                    code="DUPLICATE_ID_CONFLICT",
                    severity="error",
                    record_ids=[g.record.record_id for g in group],
                    message=f"记录编号 {record_id} 同号不同内容，隔离全部相关记录",
                    action="quarantine_event",
                    rule_version="import-rules-v1",
                )
            )
    return result


def _select_events(
    parsed: list[_ParsedRecord],
    config: ImportConfig,
    next_issue_id,
    issues: list[QualityIssue],
) -> list[AnswerEvent]:
    """按事件分组 -> 选择最高修订版 -> 判定状态与处置。

    - 同事件最高 revision 唯一时选中；同最高版本内容不同则隔离事件。
    - 最高版本待评分/未作答时保留状态，不回退旧版得分。
    - 得分超过满分不裁剪，标记 exclude_score（受 config.quarantine_invalid_scores 控制）。
    """
    groups: dict[tuple[str, str, str, str], list[_ParsedRecord]] = {}
    for item in parsed:
        event_key = item.event_id or f"auto:{item.student_id}:{item.node_id}:{item.material_id}"
        groups.setdefault((event_key, item.student_id, item.node_id, item.material_id), []).append(item)

    events: list[AnswerEvent] = []
    for seq, ((event_key, student_id, node_id, material_id), group) in enumerate(groups.items(), start=1):
        # 同一事件编号跨学生/题目/节点视为不一致，整组隔离
        if len({(g.student_id, g.node_id, g.material_id) for g in group}) > 1:
            issues.append(
                QualityIssue(
                    issue_id=next_issue_id(),
                    code="EVENT_INCONSISTENT",
                    severity="error",
                    record_ids=[g.record.record_id for g in group],
                    event_id=event_key,
                    message=f"事件 {event_key} 内学生/节点/材料不一致，隔离事件",
                    action="quarantine_event",
                    rule_version="import-rules-v1",
                )
            )
            events.append(_quarantined_event(event_key, seq, group))
            continue

        max_revision = max((g.revision or 1) for g in group)
        top = [g for g in group if (g.revision or 1) == max_revision]
        superseded = [g for g in group if (g.revision or 1) < max_revision]

        if superseded:
            issues.append(
                QualityIssue(
                    issue_id=next_issue_id(),
                    code="SUPERSEDED_REVISION",
                    severity="info",
                    record_ids=[g.record.record_id for g in group],
                    event_id=event_key,
                    message=f"事件 {event_key} 存在旧修订版本，仅保留最高版本 rev={max_revision}",
                    action="retain",
                    rule_version="import-rules-v1",
                )
            )

        if len(top) > 1 and not all(_same_payload(t.record.payload, top[0].record.payload) for t in top[1:]):
            issues.append(
                QualityIssue(
                    issue_id=next_issue_id(),
                    code="CONFLICTING_RECORDS",
                    severity="error",
                    record_ids=[g.record.record_id for g in group],
                    event_id=event_key,
                    message=f"事件 {event_key} 同最高版本 rev={max_revision} 内容冲突，隔离事件，可查看冲突双方",
                    action="quarantine_event",
                    rule_version="import-rules-v1",
                )
            )
            events.append(_quarantined_event(event_key, seq, group))
            continue

        chosen = top[0]
        status = _resolve_status(chosen)
        disposition = "include"

        if status == "graded":
            if chosen.score is not None and chosen.max_score is not None and chosen.score > chosen.max_score:
                issues.append(
                    QualityIssue(
                        issue_id=next_issue_id(),
                        code="SCORE_EXCEEDS_MAX",
                        severity="error",
                        record_ids=[chosen.record.record_id],
                        event_id=event_key,
                        message=f"得分 {chosen.score} 超过满分 {chosen.max_score}，保留原值并排除该分数统计",
                        action="exclude_score",
                        rule_version="import-rules-v1",
                    )
                )
                if config.quarantine_invalid_scores:
                    disposition = "exclude_score"
            elif chosen.max_score is None:
                issues.append(
                    QualityIssue(
                        issue_id=next_issue_id(),
                        code="MISSING_MAX_SCORE",
                        severity="warning",
                        record_ids=[chosen.record.record_id],
                        event_id=event_key,
                        message="已评分但缺少满分，无法计算得分率，排除该分数统计",
                        action="exclude_score",
                        rule_version="import-rules-v1",
                    )
                )
                disposition = "exclude_score"

        events.append(
            AnswerEvent(
                event_id=event_key if not event_key.startswith("auto:") else f"EVT-{seq:03d}",
                student_id=student_id,
                node_id=node_id,
                material_id=material_id,
                selected_record_id=chosen.record.record_id,
                status=status,
                score=chosen.score,
                max_score=chosen.max_score,
                disposition=disposition,
            )
        )
    return events


def _resolve_status(item: _ParsedRecord) -> str:
    """评分状态优先取评分状态列；缺失时按 得分/最终作答 推导。零分是有效已评分。"""
    status_text = (item.grading_status or "").strip()
    if any(token in status_text for token in _STATUS_GRADED):
        return "graded"
    if any(token in status_text for token in _STATUS_PENDING):
        return "pending"
    if any(token in status_text for token in _STATUS_UNANSWERED):
        return "unanswered"
    if item.score is not None:
        return "graded"
    if item.final_answer is not None:
        return "pending"
    return "unanswered"


def _quarantined_event(event_key: str, seq: int, group: list[_ParsedRecord]) -> AnswerEvent:
    first = group[0]
    return AnswerEvent(
        event_id=event_key if not event_key.startswith("auto:") else f"EVT-{seq:03d}",
        student_id=first.student_id,
        node_id=first.node_id,
        material_id=first.material_id,
        selected_record_id=None,
        status="graded" if first.score is not None else "unanswered",
        score=None,
        max_score=None,
        disposition="quarantine_event",
    )


def _same_payload(a: RawRow, b: RawRow) -> bool:
    return {k: v for k, v in a.items() if v not in (None, "")} == {
        k: v for k, v in b.items() if v not in (None, "")
    }


def _dataset_id(tables: RawTables) -> str:
    """内容哈希作为确定性 dataset_id，同一份输入必然得到同一ID，可复现。"""
    payload = json.dumps(
        [tables.requirements, tables.materials, tables.students, tables.nodes, tables.records, tables.dictionary],
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    return "ds-" + hashlib.sha256(payload).hexdigest()[:12]
