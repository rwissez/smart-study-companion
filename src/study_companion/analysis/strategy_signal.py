"""StrategySignal：错误阶段/错误类型的内部判定模型（供A和C读取）。

定位（下一阶段任务书第1条）：
- 不修改已冻结的公开契约；本模块是 analysis 内部的适配格式。
- 输入：评分点得分、解答过程、学生最终作答、得分/满分、材料表评分点定义。
- 输出：每个作答事件的 StrategySignal（error_stage / error_type /
  rubric_focus / evidence_status + 记录引用），每个判断可追溯到记录或评分点。
- 判定为确定性规则（可解释可复核），不推断动机，不生成无依据标签。

真实数据摸底结论（806行实测，规则依据）：
- 解答过程仅解答题有（269/269），填空/选择题天然无过程 → 题型影响证据充足性。
- 评分点得分覆盖 98%；材料表有评分点定义（"Q006P2 正确列出周长算式 1分"）。
- 0分解答题 62 条全部有过程（无"过程空但记0分"的歧义记录）。
- 未作答 5 条：作答/过程/评分点全空。
- 待评分 6 条：有作答、评分点全空（正等待评分，非不会）。
- 失分解答题 176 次中"首点丢"65 / "非首点丢"111 → 可区分起步失败与中段步骤失败。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from study_companion.contracts import AnswerEvent, PreparedDataset, RawRow

# error_stage：错误发生在解题的哪个阶段（任务书枚举）
STAGE_CANNOT_START = "cannot_start"        # 不会开始
STAGE_MODELING = "modeling"                # 建模（列式/表达数量关系）
STAGE_CONCEPT = "concept_understanding"    # 概念理解
STAGE_KEY_STEP = "key_step"                # 关键步骤
STAGE_CALCULATION = "calculation"          # 计算
STAGE_VERIFICATION = "verification"        # 检验
STAGE_NONE = "none"                        # 无错误（满分或未失分）
STAGE_UNDETERMINED = "undetermined"        # 证据不足无法判定

# error_type：错误的性质（任务书枚举）
TYPE_CONCEPT = "concept_error"             # 概念错误
TYPE_STEP = "step_error"                   # 步骤错误
TYPE_CALC = "calculation_error"            # 计算错误
TYPE_NO_VERIFICATION = "missing_verification"  # 缺少检验
TYPE_INSUFFICIENT = "insufficient_evidence"    # 证据不足（非错误，是判定受限）
TYPE_NONE = "none"

# rubric_point 语义类别：从材料表评分点定义文本识别（关键词规则，可追溯）。
# 词表经真实数据90条评分点定义校准（29条unknown逐一归类后全量覆盖）。
_VERIFICATION_WORDS = ("检验", "检查", "验算", "回代", "核对", "核验")
_CONCEPT_WORDS = (
    "概念", "定义", "性质", "判断", "识别", "表示",
    "设未知数", "数量关系", "周长关系", "解释", "取整数", "整数",
    "开闭端点", "端点与方向", "反向", "上界", "下界",
)
_STEP_WORDS = (
    "列出", "算式", "化简", "移项", "合并", "步骤", "解方程", "变形",
    "去括号", "括号", "去分母", "消元", "解不等式", "解出", "求出", "交集",
    "解集", "求值", "求两种数量", "求解",
)
_CALC_WORDS = ("计算", "运算", "求解结果", "结果正确", "答案正确", "写出正确结果", "写出结果")


@dataclass
class RubricPointResult:
    """单个评分点的判定结果：丢分点 + 语义类别 + 依据。"""

    point_id: str
    description: str
    awarded: int | None
    max_score: int
    category: str  # concept / step / verification / answer / unknown


@dataclass
class StrategySignal:
    """一个作答事件的错误阶段/类型信号（内部模型，供A和C读取）。

    每个判断都通过 record_id / event_id / rubric_detail 可追溯。
    """

    student_id: str
    node_id: str
    material_id: str
    event_id: str
    record_id: str | None
    question_type: str | None          # 题型（解答题/填空题/选择题，来自材料表）
    error_stage: str
    error_type: str
    rubric_focus: list[str] = field(default_factory=list)   # 具体失分评分点ID
    rubric_detail: list[RubricPointResult] = field(default_factory=list)
    evidence_status: str = "sufficient"   # sufficient / insufficient / conflicting
    evidence_refs: list[dict] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    reason: str = ""                      # 人可读的判定依据说明


# ---------------------------------------------------------------------------
# 评分点解析
# ---------------------------------------------------------------------------

def parse_rubric_scores(raw: str | None) -> dict[str, int]:
    """解析作答记录的评分点得分为 {point_id: score}。

    兼容真实格式 "Q015P1=1；Q015P2=0" 与分隔变体；无法解析返回空。
    """
    if not raw:
        return {}
    result: dict[str, int] = {}
    for pair in re.split(r"[;；,，]", raw):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        pid, value = pair.split("=", 1)
        pid, value = pid.strip(), value.strip()
        try:
            result[pid] = int(value)
        except ValueError:
            continue
    return result


def parse_rubric_definitions(raw: str | None) -> dict[str, str]:
    """解析材料表评分点定义为 {point_id: 描述文本}。

    真实格式按行或分号分隔："Q006P1 写出周长关系 1分\\nQ006P2 正确列出周长算式 1分"。
    """
    if not raw:
        return {}
    result: dict[str, str] = {}
    for line in re.split(r"[\n;；]", raw):
        line = line.strip()
        m = re.match(r"([A-Za-z0-9]+P\d+)\s*(.+?)(?:\s+\d+\s*分)?$", line)
        if m:
            result[m.group(1)] = m.group(2).strip()
    return result


def classify_point(description: str) -> str:
    """按描述文本给评分点分类（关键词规则；未匹配归 unknown，不强行猜）。"""
    text = description or ""
    if any(w in text for w in _VERIFICATION_WORDS):
        return "verification"
    if any(w in text for w in _CONCEPT_WORDS):
        return "concept"
    if any(w in text for w in _STEP_WORDS):
        return "step"
    if any(w in text for w in _CALC_WORDS):
        return "calculation"
    if "答案" in text or "结果" in text:
        return "answer"
    return "unknown"


# ---------------------------------------------------------------------------
# 判定主逻辑
# ---------------------------------------------------------------------------

def build_signal(
    event: AnswerEvent,
    record_payload: RawRow | None,
    material: RawRow | None,
) -> StrategySignal:
    """对单个作答事件生成 StrategySignal。

    判定规则（全部可在 reason 中追溯）：
    1. 非已评分 / 分数被排除 → 证据不足（不作错误判定）。
    2. 未作答（作答空）→ cannot_start / insufficient_evidence。
    3. 满分 → none。
    4. 解答题：有过程时按评分点模式判 stage/type；过程缺失去 insufficient。
    5. 填空/选择：无过程可看，仅有答案对错 → 答案对=none，错=答案类信息不足
       （除非单点即答案点，可判 concept/calc 不可区分 → insufficient 细化说明）。
    6. 评分点矛盾（RUBRIC_SUM_MISMATCH 相关事件）→ conflicting。
    """
    question_type = (material or {}).get("题型") or None
    final_answer = (record_payload or {}).get("学生最终作答") or ""
    solution_steps = (record_payload or {}).get("解答过程") or ""
    rubric_raw = (record_payload or {}).get("评分点得分") or ""
    rubric_defs = parse_rubric_definitions((material or {}).get("评分点"))

    signal = StrategySignal(
        student_id=event.student_id,
        node_id=event.node_id,
        material_id=event.material_id,
        event_id=event.event_id,
        record_id=event.selected_record_id,
        question_type=question_type,
        error_stage=STAGE_UNDETERMINED,
        error_type=TYPE_INSUFFICIENT,
        evidence_refs=[
            {"kind": "record", "id": event.selected_record_id}
            for _ in [1] if event.selected_record_id
        ],
    )

    # 1. 未作答（状态级）：不会开始
    if event.status == "unanswered":
        signal.error_stage = STAGE_CANNOT_START
        signal.error_type = TYPE_INSUFFICIENT
        signal.evidence_status = "insufficient"
        signal.reason = "记录状态为未作答，无作答内容与过程，判定为不会开始"
        return signal

    # 2. 待评分或分数被排除：证据不足，不判错误
    if event.status != "graded" or event.disposition != "include":
        signal.evidence_status = "insufficient"
        signal.reason = (
            "待评分，尚无官方分数" if event.status == "pending"
            else "分数被排除（超满分/缺满分/冲突），不进入错误判定"
        )
        return signal

    # 3. 记录级未作答：作答与过程全空但记0分
    if not final_answer.strip() and not solution_steps.strip():
        signal.error_stage = STAGE_CANNOT_START
        signal.error_type = TYPE_INSUFFICIENT
        signal.evidence_status = "insufficient"
        signal.reason = "无作答内容且无过程，判定为不会开始；无进一步证据"
        return signal

    # 评分点明细
    scores = parse_rubric_scores(rubric_raw)
    detail: list[RubricPointResult] = []
    for pid, value in scores.items():
        desc = rubric_defs.get(pid, "")
        detail.append(
            RubricPointResult(
                point_id=pid,
                description=desc,
                awarded=value,
                max_score=1,  # 真实数据每点1分；非1分在 sum 校验层处理
                category=classify_point(desc) if desc else "unknown",
            )
        )
    signal.rubric_detail = detail
    lost = [p for p in detail if p.awarded == 0]
    signal.rubric_focus = [p.point_id for p in lost]
    signal.evidence_refs.extend(
        {"kind": "rubric", "id": p.point_id} for p in lost
    )

    # 4. 评分点与得分矛盾 → conflicting（先于满分判定：满分也可能矛盾）
    rubric_total = sum(p.awarded or 0 for p in detail)
    if event.score is not None and detail and rubric_total != event.score:
        signal.evidence_status = "conflicting"
        signal.error_stage = STAGE_UNDETERMINED
        signal.error_type = TYPE_INSUFFICIENT
        signal.reason = (
            f"评分点之和 {rubric_total} 与官方得分 {event.score} 不符，"
            "证据冲突，标记待复核不判定错误类型"
        )
        return signal

    # 5. 满分
    if event.score is not None and event.max_score is not None and event.score == event.max_score:
        signal.error_stage = STAGE_NONE
        signal.error_type = TYPE_NONE
        signal.evidence_status = "sufficient"
        signal.reason = f"满分 {event.score}/{event.max_score}，无失分"
        return signal

    # 4. 解答题
    if question_type == "解答题":
        return _classify_solution_question(signal, event, final_answer, solution_steps, lost)

    # 5. 填空/选择题：无过程
    return _classify_objective_question(signal, event, final_answer, lost)


def _classify_solution_question(
    signal: StrategySignal,
    event: AnswerEvent,
    final_answer: str,
    solution_steps: str,
    lost: list[RubricPointResult],
) -> StrategySignal:
    """解答题判定：按失分评分点的位置与语义类别推断错误阶段。"""
    if not solution_steps.strip():
        # 有作答无过程（真实数据中解答题此情形极少但存在防御路径）
        signal.evidence_status = "insufficient"
        signal.error_stage = STAGE_UNDETERMINED
        signal.reason = "解答题缺少解答过程，无法区分概念/步骤/计算错误"
        return signal

    if not lost:
        signal.error_stage = STAGE_NONE
        signal.error_type = TYPE_NONE
        signal.reason = "有失分但无丢失评分点（部分得分来自加权），不判错误"
        return signal

    point_ids = [p.point_id for p in lost]
    first_lost = all(not p.point_id.endswith("P1") for p in lost) is False
    # 首点（P1）丢 → 起步/建模失败；中后段点丢 → 关键步骤/计算
    p1_lost = any(p.point_id.endswith("P1") for p in lost)
    verification_lost = [p for p in lost if p.category == "verification"]
    concept_lost = [p for p in lost if p.category == "concept"]
    step_lost = [p for p in lost if p.category == "step"]
    calc_lost = [p for p in lost if p.category == "calculation"]

    # 末位检验点单独丢 → 缺少检验
    if verification_lost and not p1_lost and len(lost) == len(verification_lost):
        signal.error_stage = STAGE_VERIFICATION
        signal.error_type = TYPE_NO_VERIFICATION
        signal.reason = f"仅检验类评分点丢失（{','.join(point_ids)}），此前步骤已得分"
        return signal

    # 前段步骤对、仅计算/结果类点丢 → 计算错误
    if calc_lost and not p1_lost and not concept_lost and len(lost) == len(calc_lost):
        signal.error_stage = STAGE_CALCULATION
        signal.error_type = TYPE_CALC
        signal.reason = f"起步与步骤点已得分，仅计算结果类点丢失（{','.join(point_ids)}）"
        return signal

    if p1_lost:
        # 起步点丢失：建模或概念
        if concept_lost:
            signal.error_stage = STAGE_CONCEPT if concept_lost[0].point_id.endswith("P1") else STAGE_MODELING
            signal.error_type = TYPE_CONCEPT
            signal.reason = f"首评分点（概念/建模类）丢失：{','.join(point_ids)}，起步即失分"
        else:
            signal.error_stage = STAGE_MODELING
            signal.error_type = TYPE_STEP
            signal.reason = f"首评分点丢失且无过程得分：{','.join(point_ids)}，建模或起步步骤失败"
        return signal

    if concept_lost:
        signal.error_stage = STAGE_CONCEPT
        signal.error_type = TYPE_CONCEPT
        signal.reason = f"概念类评分点丢失：{','.join(point_ids)}，但起步点已得分"
        return signal

    if step_lost:
        signal.error_stage = STAGE_KEY_STEP
        signal.error_type = TYPE_STEP
        signal.reason = f"中段步骤评分点丢失：{','.join(point_ids)}，起步与结果正常"
        return signal

    # 无语义信息（定义缺失）→ 按位置保守判定为步骤类
    signal.error_stage = STAGE_KEY_STEP if not first_lost else STAGE_MODELING
    signal.error_type = TYPE_STEP
    signal.reason = f"评分点丢失但材料表无该点定义，按位置判定：{','.join(point_ids)}"
    signal.limitations.append("材料表缺评分点定义，类别判定基于点位置而非语义")
    return signal


def _classify_objective_question(
    signal: StrategySignal,
    event: AnswerEvent,
    final_answer: str,
    lost: list[RubricPointResult],
) -> StrategySignal:
    """填空/选择判定：无过程可看，答案错即丢分，错误类型证据不足。

    真实数据中此类题评分点即"答案正确"——只知结果不知原因，
    按任务书要求标证据不足，不强行区分概念/计算。
    """
    if not lost:
        signal.error_stage = STAGE_NONE
        signal.error_type = TYPE_NONE
        signal.reason = "有失分但无丢失评分点，不判错误"
        return signal

    signal.evidence_status = "insufficient"
    signal.error_stage = STAGE_UNDETERMINED
    signal.error_type = TYPE_INSUFFICIENT
    signal.reason = (
        f"客观题答案错误（丢失点：{','.join(p.point_id for p in lost)}），"
        "无解答过程，无法区分概念错误与计算错误"
    )
    signal.limitations.append("客观题无过程证据，建议作为观察候选而非直接定论")
    return signal


# ---------------------------------------------------------------------------
# 批量入口
# ---------------------------------------------------------------------------

def aggregate_signals_by_topic(
    signals: list[StrategySignal],
    indicator_to_topic: dict[str, str],
    material_to_indicators: dict[str, list[str]],
    min_solution_questions_for_error_typing: int = 2,
) -> list[dict]:
    """按 学生×节点×主题 聚合信号，生成主题层结论（18号核对表校准规则）。

    规则（真实数据校准，非经验值）：
    - 错误类型进入结论的条件：在 >= min_solution_questions_for_error_typing 道
      解答题中重复出现（单题模式可能是笔误）。
    - 客观题失分不计入错误类型计数（0% 可判型），聚合为 observed_objective_misses。
    - 主题层证据充足的条件：有效题 >=3 且解答题 >=1；否则标 insufficient。

    返回字典列表（内部适配格式，供C的 select_strategy 读取）。
    """
    groups: dict[tuple[str, str, str], dict] = {}
    for s in signals:
        iids = material_to_indicators.get(s.material_id, [])
        topic = next((indicator_to_topic[i] for i in iids if i in indicator_to_topic), None)
        if topic is None:
            continue
        key = (s.student_id, s.node_id, topic)
        g = groups.setdefault(
            key,
            {
                "student_id": s.student_id,
                "node_id": s.node_id,
                "topic_id": topic,
                "distinct_solution_materials": set(),
                "distinct_valid_materials": set(),
                "error_type_counts": {},  # error_type -> 重复出现的材料数
                "error_materials": {},    # error_type -> [material_id]
                "rubric_focus": [],
                "insufficient_reasons": 0,
                "evidence_status": "insufficient",
            },
        )
        is_valid = s.error_type not in ("insufficient_evidence",) or s.error_stage == "cannot_start"
        if s.question_type == "解答题":
            if s.error_type == "none" or s.error_stage == "cannot_start" or is_valid:
                g["distinct_solution_materials"].add(s.material_id)
            if s.error_type in ("concept_error", "step_error", "calculation_error",
                                "missing_verification"):
                mats_seen = g["error_materials"].setdefault(s.error_type, [])
                if s.material_id not in mats_seen:
                    mats_seen.append(s.material_id)
        if s.error_stage != "undetermined" or s.error_type == "none":
            g["distinct_valid_materials"].add(s.material_id)
        if s.evidence_status == "insufficient":
            g["insufficient_reasons"] += 1
        g["rubric_focus"].extend(s.rubric_focus)

    results: list[dict] = []
    for key, g in groups.items():
        sol_count = len(g["distinct_solution_materials"])
        valid_count = len(g["distinct_valid_materials"])
        # 错误类型结论：足够题数重复出现
        confirmed = {
            et: mats
            for et, mats in g["error_materials"].items()
            if len(mats) >= min_solution_questions_for_error_typing
        }
        # 证据充足：有效题>=3 且解答题>=1
        if valid_count >= 3 and sol_count >= 1:
            g["evidence_status"] = "sufficient"
        results.append(
            {
                "student_id": g["student_id"],
                "node_id": g["node_id"],
                "topic_id": g["topic_id"],
                "valid_material_count": valid_count,
                "solution_material_count": sol_count,
                "confirmed_error_types": {et: list(mats) for et, mats in confirmed.items()},
                "unconfirmed_error_types": {
                    et: mats for et, mats in g["error_materials"].items() if et not in confirmed
                },
                "rubric_focus": sorted(set(g["rubric_focus"])),
                "evidence_status": g["evidence_status"],
                "insufficient_signal_count": g["insufficient_reasons"],
            }
        )
    return results

def build_strategy_signals(
    dataset: PreparedDataset, materials_rows: list[RawRow]
) -> list[StrategySignal]:
    """对全部选中事件生成 StrategySignal 列表。

    materials_rows：材料表原始行（含 题型/评分点/参考答案），由调用方从
    PreparedDataset.raw_records 中筛出 table_name=="materials" 传入。
    """
    materials_by_id: dict[str, RawRow] = {}
    for row in materials_rows:
        mid = row.get("材料编号") or row.get("材料ID") or ""
        if mid:
            materials_by_id[mid] = row

    records_by_id = {
        r.record_id: r.payload
        for r in dataset.raw_records
        if r.table_name == "records"
    }

    signals: list[StrategySignal] = []
    for event in dataset.selected_events:
        record_payload = (
            records_by_id.get(event.selected_record_id)
            if event.selected_record_id
            else None
        )
        signals.append(
            build_signal(event, record_payload, materials_by_id.get(event.material_id))
        )
    return signals
