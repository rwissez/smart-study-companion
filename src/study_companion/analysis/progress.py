"""学习进展比较：可比性判定与四分类（03 §10、01 §5.1 ProgressComparison）。

设计约束（全部来自正式文档，不新增文档之外的业务口径）：

- 可比条件按 03 §10.1：同学生、同 node/观察范围、同 indicator（scope 对齐）、
  同课程知识版本（knowledge/catalog）、同 rule_version 与 config_hash、
  口径与分母有效、新证据的 event_cutoff 不倒退。
- 改善必须有基线之后的新事件证据（03 §10.3 第 5 条）；只有数值变化不构成改善。
- 证据充分性独立于表现高低（03 §5.5）：当前证据不足或冲突时，即使数值上升也不
  宣布改善，按 03 §10.2 记为 ``insufficient``（"不可比或新证据不足"）。
- 四分类的语义取自 03 §10.2；唯一使用的数值界限是 03 §5.4 / §20 已有的掌握判定线
  ``mastered_min_ratio=0.80``，本模块没有引入文档之外的提升幅度阈值。
- 不可比必须 ``classification="insufficient"`` 且 ``comparable=False``；
  ``ProgressComparison`` 自带该校验，本模块不绕过。
"""

from __future__ import annotations

from study_companion.contracts import (
    AnalysisInputRef,
    EvidenceRef,
    Metric,
    MetricScope,
    ProgressComparison,
    StudentResult,
)

#: 掌握判定线（03 §5.4 掌握状态四级分类、§20 建议初始值 ``mastered_min_ratio=0.80``）。
#: 工程初始值，待 03 按真实样本校准；调整必须同步 rule_version 与验收证据。
MASTERED_MIN_RATIO = 0.80

#: 比率比较的数值容差，只用于消除浮点噪声，不是业务阈值。
_EPS = 1e-9

#: 结果级不可比原因之外、逐指标比较需要的稳定指标名（与 analysis/service.py 同名）。
_ITEM_COVERAGE_METRIC = "item_coverage"

_METRIC_LABELS = {
    "valid_score_ratio": "得分率",
    "full_score_ratio": "满分题比例",
    "observed_response_ratio": "观测作答比例",
    _ITEM_COVERAGE_METRIC: "题库覆盖率",
    "student_observation_rate": "学生观测率",
}


# --------------------------------------------------------------------------- #
# 可比性判定
# --------------------------------------------------------------------------- #


def is_comparable(
    baseline: StudentResult,
    current: StudentResult,
    indicator_id: str | None = None,
) -> tuple[bool, list[str]]:
    """判断两次学生结果能否直接比较，返回 ``(comparable, 原因列表)``。

    ``indicator_id`` 给定时额外核对该指标在两侧都有统计结果且分母有效；
    省略时只核对结果级条件（学生/node/版本/口径/事件高水位）。
    """

    reasons = _scope_mismatch_reasons(baseline, current)
    if indicator_id is not None:
        reasons.extend(_indicator_mismatch_reasons(baseline, current, indicator_id))
    return (not reasons, reasons)


def _scope_mismatch_reasons(baseline: StudentResult, current: StudentResult) -> list[str]:
    """结果级可比条件（03 §10.1、§10.3 第 1 条）。"""

    reasons: list[str] = []

    if baseline.student_id != current.student_id:
        reasons.append(f"学生不同（基线{baseline.student_id}，当前{current.student_id}），不是同一个人的进步")
    if baseline.node_id != current.node_id:
        reasons.append(f"观测节点不同（基线{baseline.node_id}，当前{current.node_id}），教学范围不可等同")
    if baseline.scope_kind != current.scope_kind:
        reasons.append(
            f"结果范围类型不同（基线{baseline.scope_kind}，当前{current.scope_kind}），"
            "测验原始成绩与当前综合画像不能直接比较"
        )
    if baseline.catalog_version != current.catalog_version:
        reasons.append(
            f"题库目录版本不同（基线{baseline.catalog_version}，当前{current.catalog_version}），"
            "题目集合与评分口径不可比"
        )
    if (
        baseline.dataset_family_id
        and current.dataset_family_id
        and baseline.dataset_family_id != current.dataset_family_id
    ):
        reasons.append(
            f"数据系列不同（基线{baseline.dataset_family_id}，当前{current.dataset_family_id}），"
            "不同来源的测验不能直接比较"
        )

    if baseline.analysis_version and baseline.analysis_version == current.analysis_version:
        reasons.append(f"基线与当前是同一次分析版本（{current.analysis_version}），不存在两次结果可比")

    baseline_input = baseline.analysis_input
    current_input = current.analysis_input
    if baseline_input is None or current_input is None:
        reasons.append("缺少 analysis_input（规则版本/配置哈希/事件高水位），无法核对比较口径")
        return reasons

    reasons.extend(_version_field_reasons(baseline_input, current_input))
    return reasons


def _version_field_reasons(baseline_input: AnalysisInputRef, current_input: AnalysisInputRef) -> list[str]:
    """版本字段核对：两侧都已登记且不同则不可比；任一侧未登记只作为限制说明。"""

    reasons = []
    if baseline_input.rule_version != current_input.rule_version:
        reasons.append(
            f"规则版本不同（基线{baseline_input.rule_version}，当前{current_input.rule_version}），"
            "统计与判定口径已变化"
        )
    elif not baseline_input.rule_version:
        reasons.append("两个版本都没有登记规则版本，无法确认判定口径一致")
    if baseline_input.config_hash != current_input.config_hash:
        reasons.append(
            f"配置哈希不同（基线{baseline_input.config_hash}，当前{current_input.config_hash}），"
            "阈值与权重配置已变化"
        )
    elif not baseline_input.config_hash:
        reasons.append("两个版本都没有登记配置哈希，无法确认阈值配置一致")
    baseline_knowledge = baseline_input.knowledge_version
    current_knowledge = current_input.knowledge_version
    if baseline_knowledge and current_knowledge and baseline_knowledge != current_knowledge:
        reasons.append(
            f"课程知识版本不同（基线{baseline_knowledge}，当前{current_knowledge}），指标范围不可比"
        )
    if current_input.event_cutoff < baseline_input.event_cutoff:
        reasons.append(
            f"当前事件高水位（{current_input.event_cutoff}）低于基线（{baseline_input.event_cutoff}），"
            "新旧结果顺序颠倒，不能用旧结果覆盖新结果"
        )
    return reasons


def _indicator_mismatch_reasons(
    baseline: StudentResult, current: StudentResult, indicator_id: str
) -> list[str]:
    baseline_metrics = _metrics_of(baseline, indicator_id)
    current_metrics = _metrics_of(current, indicator_id)
    reasons: list[str] = []
    if not baseline_metrics:
        reasons.append(f"指标{indicator_id}在基线版本中没有统计结果")
    if not current_metrics:
        reasons.append(f"指标{indicator_id}在当前版本中没有统计结果")
    if baseline_metrics and current_metrics:
        if not any(metric.value is not None for metric in baseline_metrics):
            reasons.append(f"指标{indicator_id}的基线分母为0，比值未定义")
        if not any(metric.value is not None for metric in current_metrics):
            reasons.append(f"指标{indicator_id}的当前分母为0，比值未定义")
    return reasons


# --------------------------------------------------------------------------- #
# 逐指标比较
# --------------------------------------------------------------------------- #


def compare_progress(baseline: StudentResult, current: StudentResult) -> list[ProgressComparison]:
    """逐指标比较两次学生结果，返回每个指标一项 :class:`ProgressComparison`。

    指标按 ``(indicator_id, metric_name)`` 配对；只在一侧出现的指标也生成一条
    ``insufficient`` 记录并说明原因，不做隐式补零或猜测。
    """

    scope_reasons = _scope_mismatch_reasons(baseline, current)
    plan = _metric_plan(baseline, current)
    if not plan:
        return [_scope_level_comparison(baseline, current, scope_reasons)]

    comparisons: list[ProgressComparison] = []
    for indicator_id, metric_name in plan:
        baseline_metric = _metric_of(baseline, indicator_id, metric_name)
        current_metric = _metric_of(current, indicator_id, metric_name)
        comparisons.append(
            _compare_metric(
                baseline,
                current,
                indicator_id,
                metric_name,
                baseline_metric,
                current_metric,
                scope_reasons,
            )
        )
    return comparisons


def _metrics_of(result: StudentResult, indicator_id: str) -> list[Metric]:
    return [metric for metric in result.metrics if metric.scope.indicator_id == indicator_id]


def _metric_of(result: StudentResult, indicator_id: str, metric_name: str) -> Metric | None:
    for metric in result.metrics:
        if metric.scope.indicator_id == indicator_id and metric.name == metric_name:
            return metric
    return None


def _metric_plan(baseline: StudentResult, current: StudentResult) -> list[tuple[str, str]]:
    """稳定配对顺序：先基线出现顺序，再补当前新增指标。"""

    plan: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for result in (baseline, current):
        for metric in result.metrics:
            indicator_id = metric.scope.indicator_id
            if indicator_id is None:
                continue
            key = (indicator_id, metric.name)
            if key not in seen:
                seen.add(key)
                plan.append(key)
    return plan


def _compare_metric(
    baseline: StudentResult,
    current: StudentResult,
    indicator_id: str,
    metric_name: str,
    baseline_metric: Metric | None,
    current_metric: Metric | None,
    scope_reasons: list[str],
) -> ProgressComparison:
    reasons = list(scope_reasons)
    reasons.extend(_metric_mismatch_reasons(metric_name, baseline_metric, current_metric))

    baseline_value = baseline_metric.value if baseline_metric else None
    current_value = current_metric.value if current_metric else None

    if reasons or baseline_value is None or current_value is None:
        classification = "insufficient"
        limitations = reasons or ["该指标的基线或当前值缺失，无法比较"]
        description = f"{_label(metric_name)}：不生成变化结论。{('；'.join(limitations))}。"
        comparable = False
    else:
        new_evidence, evidence_note = _new_evidence_state(baseline, current)
        classification, limitations = _classify(
            baseline=baseline,
            current=current,
            indicator_id=indicator_id,
            delta=current_value - baseline_value,
            baseline_value=baseline_value,
            current_value=current_value,
            new_evidence=new_evidence,
            evidence_note=evidence_note,
        )
        comparable = True
        description = _describe(
            metric_name, classification, baseline_value, current_value, limitations
        )

    return ProgressComparison(
        comparison_id=_comparison_id(baseline, current, indicator_id, metric_name),
        student_id=current.student_id,
        baseline_analysis_version=baseline.analysis_version,
        current_analysis_version=current.analysis_version,
        scope=_scope_of(current, indicator_id, current_metric or baseline_metric),
        classification=classification,
        comparable=comparable,
        metric_name=metric_name,
        baseline_value=baseline_value,
        current_value=current_value,
        evidence_refs=_merge_refs(baseline, current, baseline_metric, current_metric),
        limitations=limitations,
        description=description,
    )


def _metric_mismatch_reasons(
    metric_name: str, baseline_metric: Metric | None, current_metric: Metric | None
) -> list[str]:
    """指标级可比条件：两侧存在、单位一致、粒度一致、分母有效、覆盖范围一致。"""

    if baseline_metric is None:
        return [f"该指标只在当前版本存在，基线没有同名统计（{metric_name}）"]
    if current_metric is None:
        return [f"该指标只在基线版本存在，当前版本没有同名统计（{metric_name}）"]

    reasons: list[str] = []
    if baseline_metric.unit != current_metric.unit:
        reasons.append(f"指标单位不同（基线{baseline_metric.unit}，当前{current_metric.unit}）")
    if baseline_metric.scope.kp_id != current_metric.scope.kp_id:
        reasons.append(
            f"指标粒度不同（基线知识点{baseline_metric.scope.kp_id or '未细分'}，"
            f"当前知识点{current_metric.scope.kp_id or '未细分'}）"
        )
    for side, metric in (("基线", baseline_metric), ("当前", current_metric)):
        if metric.denominator is None:
            reasons.append(f"{side}该指标缺少分母，口径不完整")
        elif metric.denominator == 0:
            reasons.append(f"{side}该指标分母为0，比值未定义")
        elif metric.value is None:
            reasons.append(f"{side}该指标比值为空，无法比较")
    if (
        metric_name == _ITEM_COVERAGE_METRIC
        and baseline_metric.denominator
        and current_metric.denominator
        and baseline_metric.denominator != current_metric.denominator
    ):
        reasons.append(
            f"题库覆盖范围不同（基线{baseline_metric.denominator}道题，"
            f"当前{current_metric.denominator}道题），覆盖类指标不直接比较"
        )
    return reasons


def _classify(
    *,
    baseline: StudentResult,
    current: StudentResult,
    indicator_id: str,
    delta: float,
    baseline_value: float,
    current_value: float,
    new_evidence: bool,
    evidence_note: str | None,
) -> tuple[str, list[str]]:
    """四分类（03 §10.2）：证据门槛优先，再看方向，再看新证据，最后分明显/部分改善。"""

    current_status = _evidence_status(current, indicator_id)
    baseline_status = _evidence_status(baseline, indicator_id)
    limitations: list[str] = []

    if current_status == "conflicting":
        return "insufficient", [
            "当前版本该指标存在记录冲突，按 03 §5.4 优先判证据不足，不宣布变化"
        ]

    if delta <= _EPS:
        if current_status != "sufficient":
            limitations.append(
                f"当前指标证据状态为{current_status or '未提供'}，"
                "本结论只反映数值方向，不足以支撑掌握判断"
            )
        return "unchanged_or_declined", limitations

    if not new_evidence:
        return "insufficient", [evidence_note or "两次分析之间没有新事件证据，不生成改善结论"]

    if current_status != "sufficient":
        return "insufficient", [
            f"当前指标证据状态为{current_status or '未提供'}，按 03 §10.2 属新证据不足，暂不判定改善"
        ]

    limitations.append(
        f"改善判定依据为掌握判定线 {MASTERED_MIN_RATIO:.2f}（03 §5.4 工程初始值）的跨越；"
        "本模块未引入文档之外的提升幅度阈值"
    )
    if current_value >= MASTERED_MIN_RATIO > baseline_value and baseline_status == "sufficient":
        return "improved", limitations
    if current_value >= MASTERED_MIN_RATIO > baseline_value:
        limitations.append(
            f"基线证据状态为{baseline_status or '未提供'}，改善方向成立但起点不稳定，"
            "按 03 §10.2 记为有改善迹象而非明显改善"
        )
        return "partially_improved", limitations
    return "partially_improved", limitations


def _evidence_status(result: StudentResult, indicator_id: str) -> str | None:
    for finding in result.findings:
        if finding.indicator_id == indicator_id:
            return finding.evidence_status
    return None


# --------------------------------------------------------------------------- #
# 新证据与描述
# --------------------------------------------------------------------------- #


def _new_evidence_state(baseline: StudentResult, current: StudentResult) -> tuple[bool, str | None]:
    """按两版事件清单判断该学生基线之后是否有新作答（03 §10.3 第 5 条）。

    ``event_cutoff`` 是全局账本的高水位，其他学生的新事件也会推高它，
    因此这里只用本对象的 ``included_event_ids`` 差集判断，不用高水位代替。
    """

    baseline_input = baseline.analysis_input
    current_input = current.analysis_input
    if baseline_input is None or current_input is None:
        return False, "缺少 analysis_input，无法确认基线之后的新事件"

    baseline_ids = set(baseline_input.included_event_ids)
    current_ids = set(current_input.included_event_ids)
    if not baseline_ids or not current_ids:
        return False, "两版未提供完整事件清单（included_event_ids），无法确认该学生的新事件"
    if current_ids - baseline_ids:
        return True, None
    return False, "当前版本未包含基线之后的新事件，按 03 §10.3 不生成改善结论"


def _label(metric_name: str) -> str:
    return _METRIC_LABELS.get(metric_name, f"指标{metric_name}")


def _describe(
    metric_name: str,
    classification: str,
    baseline_value: float | None,
    current_value: float | None,
    limitations: list[str],
) -> str:
    label = _label(metric_name)
    if baseline_value is None or current_value is None:
        return f"{label}：缺少可比数值，不生成变化结论。"
    delta = current_value - baseline_value
    if classification == "improved":
        return (
            f"{label}由{baseline_value:.2f}提升到{current_value:.2f}（+{delta:.2f}），"
            f"跨越掌握判定线{MASTERED_MIN_RATIO:.2f}，且有基线之后的新作答证据，判定为明显改善；"
            "可进入巩固或迁移练习。"
        )
    if classification == "partially_improved":
        if current_value >= MASTERED_MIN_RATIO:
            return (
                f"{label}由{baseline_value:.2f}提升到{current_value:.2f}（+{delta:.2f}），"
                f"已跨过掌握判定线{MASTERED_MIN_RATIO:.2f}，但基线证据不稳定，"
                "判定为有所改善仍需继续验证；建议观察不同题目的独立表现。"
            )
        return (
            f"{label}由{baseline_value:.2f}提升到{current_value:.2f}（+{delta:.2f}），"
            "有改善迹象但尚未达到掌握判定线，判定为有所改善仍需巩固；"
            "建议继续观察不同题目的独立表现。"
        )
    if classification == "unchanged_or_declined":
        if delta < -_EPS:
            return (
                f"{label}由{baseline_value:.2f}下降到{current_value:.2f}（{delta:.2f}），"
                "没有观察到改善，建议复查错误原因后再调整练习。"
            )
        return f"{label}保持在{current_value:.2f}，与基线相比没有观察到变化。"
    reason = "；".join(limitations) if limitations else "证据不足"
    return f"{label}：暂不判定改善或退步。{reason}。"


def _scope_level_comparison(
    baseline: StudentResult, current: StudentResult, reasons: list[str]
) -> ProgressComparison:
    """两个版本都没有指标时，仍返回一条覆盖范围的可比性说明。"""

    limitations = reasons or ["两个版本都没有指标统计结果，没有可比较对象"]
    return ProgressComparison(
        comparison_id=_comparison_id(baseline, current, None, "scope"),
        student_id=current.student_id,
        baseline_analysis_version=baseline.analysis_version,
        current_analysis_version=current.analysis_version,
        scope=_scope_of(current, None, None),
        classification="insufficient",
        comparable=False,
        evidence_refs=[],
        limitations=limitations,
        description=f"暂不判定进展。{('；'.join(limitations))}。",
    )


# --------------------------------------------------------------------------- #
# 引用与标识
# --------------------------------------------------------------------------- #


def _version_of(result: StudentResult) -> str:
    return result.analysis_version or result.run_id


def _comparison_id(
    baseline: StudentResult,
    current: StudentResult,
    indicator_id: str | None,
    metric_name: str,
) -> str:
    return (
        f"PC-{_version_of(baseline)}-{_version_of(current)}"
        f"-{indicator_id or 'ALL'}-{metric_name}"
    )


def _scope_of(result: StudentResult, indicator_id: str | None, metric: Metric | None) -> MetricScope:
    """比较范围沿用当前版本的主体与节点；知识点粒度取两侧对齐后的值。"""

    return MetricScope(
        student_id=result.student_id,
        node_id=result.node_id,
        indicator_id=indicator_id,
        kp_id=metric.scope.kp_id if metric else None,
    )


def _merge_refs(
    baseline: StudentResult,
    current: StudentResult,
    baseline_metric: Metric | None,
    current_metric: Metric | None,
) -> list[EvidenceRef]:
    """合并两版各自的记录/指标引用，按 kind+id+版本去重并保留首次出现顺序。"""

    groups: list[list[EvidenceRef]] = []
    for result, metric in ((baseline, baseline_metric), (current, current_metric)):
        if metric is None:
            continue
        groups.append(
            [EvidenceRef(kind="metric", id=metric.metric_id, run_id=result.run_id)]
        )
        groups.append(list(metric.evidence_refs))

    seen: set[str] = set()
    merged: list[EvidenceRef] = []
    for group in groups:
        for ref in group:
            key = ref.key()
            if key in seen:
                continue
            seen.add(key)
            merged.append(ref)
    return merged
