"""recommendation/service.py —— C 模块：选材、规则建议、证据校验。

设计对齐：
- 统一实施规范（11）第四节：三个固定签名，函数不读环境变量、不持 HTTP 请求、不写数据库
- 三类学生建议模板 v4（纸盒定稿）：四类判定、递减干预、两级思想迁移
- C 工作指南（05）：证据校验由程序强制执行、空话禁令、不产生 B 没给出的新数字

版本：c-rules-v1（纯规则引擎。1a/1b 细分为启发式判定，接模型适配器后可精判。）
"""

from __future__ import annotations

from study_companion.contracts import (
    Activity,
    AdjustmentCondition,
    Catalog,
    DomainError,
    Finding,
    Material,
    MaterialSearch,
    Recommendation,
    RecommendationBundle,
    RecommendationConfig,
    RunContext,
    StudentResult,
    ValidationIssue,
    ValidationResult,
)

from study_companion.recommendation.leak_check import check_hint

RULE_VERSION = "c-rules-v2"

# 1a 需要解答过程信号：B 的 finding description 引用了过程事实才算"有思路"
PROCESS_SIGNALS: tuple[str, ...] = ("过程", "步骤")
# B 指南约定：缺解答过程时不编造步骤诊断，会写入 limitations
MISSING_PROCESS_MARKERS: tuple[str, ...] = (
    "缺少解答过程",
    "无解答过程",
    "过程缺失",
    "未提供解答过程",
    "解答过程为空",
)
# 空话禁令（05：空泛"多练习"不能验收）
VAGUE_PHRASES: tuple[str, ...] = ("多练习", "加强巩固", "认真听讲")

# 材料偏好：扶用卡片/教材，练与巩固用题目
_SUPPORT_TYPES = ("card", "textbook")
_PRACTICE_TYPE = "question"


# ---------------------------------------------------------------------------
# 分类（启发式 v1）
# ---------------------------------------------------------------------------

def classify_finding(finding: Finding) -> str:
    """把单条 finding 归入剧本四类：1a / 1b / type2 / type3。

    启发式依据（c-rules-v1）：
    - 证据不足或冲突 -> type3（剧本红线：不输出会/不会结论，先观察）
    - 表现较好 -> type2（两级迁移）
    - 需要练习：description 引用了解答过程且未声明过程缺失 -> 1a（倾听式）
    - 需要练习但无过程信号或声明缺失 -> 1b（递减干预扶练）
    - 表现未定但证据充足 -> 保守按 1b（先扶后观察）
    """
    if finding.evidence_status in ("insufficient", "conflicting"):
        return "type3"
    if finding.performance == "observed_strength":
        return "type2"
    if finding.performance == "needs_practice":
        declared_missing = any(m in "".join(finding.limitations) for m in MISSING_PROCESS_MARKERS)
        has_signal = any(s in finding.description for s in PROCESS_SIGNALS)
        if has_signal and not declared_missing:
            return "1a"
        return "1b"
    return "1b"


# ---------------------------------------------------------------------------
# 材料检索
# ---------------------------------------------------------------------------

def search_materials(catalog: Catalog, request: MaterialSearch) -> list[Material]:
    """按指标与类型过滤材料目录，变式多样性优先（不同 variant_tag 交替取用）。

    已知契约缺口（已提请 A 更新）：MaterialSearch 缺 node_id 字段，
    教学范围（节点）过滤暂由调用方 A 服务端负责。
    """
    wanted = set(request.indicator_ids)
    candidates: list[Material] = []
    for material in catalog.materials:
        if wanted and not (wanted & set(material.indicator_ids)):
            continue
        if request.material_types and material.material_type not in request.material_types:
            continue
        candidates.append(material)

    buckets: dict[str, list[Material]] = {}
    for material in candidates:
        buckets.setdefault(material.reviewed_variant_tag or "_untagged", []).append(material)

    picked: list[Material] = []
    while len(picked) < request.limit and any(buckets.values()):
        for key in list(buckets):
            if buckets[key] and len(picked) < request.limit:
                picked.append(buckets[key].pop(0))
    return picked


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

def _context_check(analysis: StudentResult, context: RunContext) -> None:
    mismatches: list[dict[str, str]] = []
    if analysis.run_id != context.run_id:
        mismatches.append({"field": "run_id", "reason": "与分析结果不一致"})
    if analysis.dataset_id != context.dataset_id:
        mismatches.append({"field": "dataset_id", "reason": "与分析结果不一致"})
    if analysis.catalog_version != context.catalog_version:
        mismatches.append({"field": "catalog_version", "reason": "与分析结果不一致"})
    if mismatches:
        raise DomainError("CONTEXT_MISMATCH", "分析结果与运行上下文不一致", mismatches)


def _materials_for(
    catalog: Catalog,
    indicator_id: str,
    types: tuple[str, ...],
    limit: int,
) -> list[Material]:
    return search_materials(
        catalog,
        MaterialSearch(indicator_ids=[indicator_id], material_types=list(types), limit=limit),
    )


def _activity(
    material: Material,
    order: int,
    purpose: str,
    hints: tuple[str, ...],
    points: tuple[str, ...],
) -> Activity:
    return Activity(
        order=order,
        material_id=material.material_id,
        purpose=purpose,
        hint_levels=list(hints),
        observation_points=list(points),
        previously_attempted=False,
    )


# ---------------------------------------------------------------------------
# 四类建议生成（剧本 v4）
# ---------------------------------------------------------------------------

def _practice_cue(requirement: str) -> str:
    if "交集" in requirement:
        return "先分别求出两个不等式的解集，再在数轴上找同时满足的部分。"
    if "实际" in requirement and ("列" in requirement or "解释" in requirement):
        return "先说明未知量表示什么、单位是什么，再列出数量关系，最后用原题条件检查结果。"
    if "不等式" in requirement:
        return "逐行写出变形；两边同乘或同除时，先看这个数是正数还是负数。"
    if "消元" in requirement:
        return "先比较两个方程中同一个未知数的系数，选一个较容易消去的未知数。"
    if "同类项" in requirement or "括号" in requirement:
        return "先用笔标出括号前的符号，再把字母和指数都相同的项划到一起。"
    if "代入" in requirement or "数量关系" in requirement:
        return "先写出字母代表的量和它们之间的关系，再把给定值代进去分步计算。"
    if "等式" in requirement or "方程" in requirement:
        return "每变形一次，写清等式两边做了什么；求得结果后代回原式检查。"
    return "先圈出题目给出的条件和要求，再写出第一步。"


def _practice(material: Material, order: int, purpose: str, cue: str) -> Activity:
    return _activity(
        material, order, purpose,
        (cue,
         "做完后对照题目条件和每一步的计算，检查有没有漏写或抄错。"),
        ("查看学生能否独立写出第一步，并说明每一步用了哪个条件。",),
    )


def _quantity_exercises(questions: list[Material]) -> tuple[Material, Material] | None:
    expression = next((item for item in questions if "表示" in item.content and "值是多少" not in item.content), None)
    evaluation = next((
        item for item in questions if item != expression
        and any(word in item.content for word in ("的值", "求值", "代入"))
    ), None)
    return (expression, evaluation) if expression and evaluation else None


def _build_recommendation(
    finding: Finding,
    catalog: Catalog,
    rec_id: str,
    config: RecommendationConfig,
    attempted: set[str],
) -> Recommendation:
    indicator_id = finding.indicator_id
    indicator = next((item.description for item in catalog.indicators if item.indicator_id == indicator_id), "这项学习要求")
    kind = classify_finding(finding)
    questions = _materials_for(catalog, indicator_id, (_PRACTICE_TYPE,), len(catalog.materials))
    questions.sort(key=lambda material: (
        not any(point.description == finding.rubric_focus for point in material.rubric_points),
        material.material_id in attempted,
    ))
    support = _materials_for(catalog, indicator_id, _SUPPORT_TYPES, 1)
    activities: list[Activity] = []

    if kind == "type3":
        goal = f"用短练习了解「{indicator}」的解题过程"
        reason = "本次相关作答还不足以判断表现。先补做少量题，保留解题步骤，便于老师确认从哪里开始讲。"
        actions = [("能独立完成并说清步骤", "老师结合新作答重新判断；如仍卡住，先讨论写不下去的那一步。")]
        purposes = ["先独立完成，保留草稿；写不下去就在那一步停下并画圈。"] * 3
    elif kind == "type2":
        goal = f"在不同题目中运用「{indicator}」"
        reason = "这项学习要求在本次测验中表现较好。用少量练习检查换一道题时是否仍能用对方法。"
        actions = [("能独立完成并说清方法", "结束这组练习；若条件一变就出错，和老师对照两道题的区别。")]
        purposes = ["独立完成这道题，并写下一条解题依据。", "完成后和上一题比较，说出两题共用的方法和各自不同的条件。"]
    else:
        goal = f"巩固「{indicator}」，独立完成并检查"
        reason = "本次测验中，这项学习要求的得分偏低。先弄清怎么开始，再独立做题，看看是否还会在同一步卡住。"
        actions = [("两道练习都能独立完成", "尝试换一道题，复述解题方法。"),
                   ("仍需提示才能开始，或同一步再次卡住", "把草稿交给老师，一起找出需要补讲的步骤。")]
        purposes = ["独立做题，逐行写出依据；卡住时先标记卡住的那一步。",
                    "先不看提示，再做一道相关题；做完后检查结果是否符合原题条件。"]
        if kind == "1a":
            reason = "这次作答留下了解题过程。先回看已有步骤，找出自己说不清的一步，再独立完成同类题。"
        if kind == "1b" and support:
            activities.append(_activity(
                support[0], 1, "先读这份学习材料，找出与下面练习有关的一条方法，用自己的话说一遍。",
                ("先想一想，下面这道题的第一步能用到材料中的哪条方法？",),
                ("能否合上材料，自己写出并解释第一步。",),
            ))
        if finding.rubric_focus:
            reason = f"本次相关作答在「{finding.rubric_focus}」这一评分点失分。练习时把这一步单独写清，再检查整道题。"

    paired = _quantity_exercises(questions) if kind in {"1a", "1b"} and "数量关系" in indicator and "代入" in indicator else None
    if paired:
        expression, evaluation = paired
        activities.append(_practice(
            expression, len(activities) + 1, "先写每个字母表示什么，再用代数式表示题目里的数量关系。",
            "把题目里的各部分数量分别写出来，再想它们要相加、相减还是相乘。",
        ))
        activities.append(_practice(
            evaluation, len(activities) + 1, "把题目给的数代进去，写出代入后的式子再计算，最后检查符号。",
            "如果代入的是负数，先给它加括号，再按运算顺序计算。",
        ))
    else:
        cue = _practice_cue(indicator)
        for material, purpose in zip(questions, purposes):
            activities.append(_practice(material, len(activities) + 1, purpose, cue))
    if not activities:
        activities.extend(
            _activity(material, 1, "阅读相关例题并写下解法，与老师核对。",
                      ("任选一个例子，先说清题目条件和要完成的任务。",),
                      ("能否用自己的话复述例题第一步。",))
            for material in support
        )
    if not activities:
        reason += " 当前目录还没有匹配的材料，请老师先准备一道对应练习。"
    for activity in activities:
        activity.previously_attempted = activity.material_id in attempted

    return Recommendation(
        recommendation_id=rec_id, indicator_id=indicator_id, finding_ids=[finding.finding_id],
        goal=goal, reason=reason, activities=activities[:config.max_activities],
        adjustment_conditions=[
            AdjustmentCondition(observable_condition=when, next_action=then) for when, then in actions
        ],
        review_status="draft", generation_method="rule",
    )


# ---------------------------------------------------------------------------
# 固定签名：规则建议
# ---------------------------------------------------------------------------

def recommend_rules(
    analysis: StudentResult,
    catalog: Catalog,
    context: RunContext,
    config: RecommendationConfig,
    *,
    attempted_material_ids: set[str] | None = None,
) -> RecommendationBundle:
    """基于已验证分析结果与材料目录，生成规则建议（剧本 v4 四类）。

    红线：不重算统计；所有数字只转述 analysis 现有内容；
    每条建议挂材料编号与诊断编号；上下文不一致直接拒绝。
    """
    _context_check(analysis, context)
    if catalog.version != context.catalog_version:
        raise DomainError(
            "CONTEXT_MISMATCH",
            "材料目录版本与运行上下文不一致",
            [{"field": "catalog_version", "reason": f"目录为 {catalog.version}，上下文为 {context.catalog_version}"}],
        )

    recommendations: list[Recommendation] = []
    for seq, finding in enumerate(analysis.findings, start=1):
        rec_id = f"rec-{analysis.student_id}-{analysis.node_id}-{finding.indicator_id}-{seq:02d}"
        recommendations.append(_build_recommendation(finding, catalog, rec_id, config, attempted_material_ids or set()))

    return RecommendationBundle(
        run_id=context.run_id,
        dataset_id=context.dataset_id,
        catalog_version=context.catalog_version,
        student_id=analysis.student_id,
        node_id=analysis.node_id,
        recommendations=recommendations,
        validation_issues=[],
    )


# ---------------------------------------------------------------------------
# 固定签名：证据校验（程序强制执行，不可跳过）
# ---------------------------------------------------------------------------

def validate_recommendations(
    bundle: RecommendationBundle,
    analysis: StudentResult,
    catalog: Catalog,
) -> ValidationResult:
    """校验建议包（05 验收）：

    - 运行身份一致（run/dataset/catalog/student 与分析结果对齐）
    - finding_ids 必须属于当前学生本次分析
    - indicator_id 必须存在于目录
    - 材料必须存在且适配该指标
    - activities 顺序从 1 连续递增，提示与观察点非空
    - 调整条件非空
    - 空话禁令：理由应具体，诊断通过结构化引用关联
    - 提示泄答拦截（15 号文档 §六）：HINT_LEAKS_ANSWER，命中不得以 validated 状态保存
    """
    issues: list[ValidationIssue] = []

    if (
        bundle.run_id != analysis.run_id
        or bundle.dataset_id != analysis.dataset_id
        or bundle.catalog_version != analysis.catalog_version
    ):
        issues.append(
            ValidationIssue(code="CONTEXT_MISMATCH", message="建议包与分析结果的运行身份不一致")
        )
    if bundle.student_id != analysis.student_id or bundle.node_id != analysis.node_id:
        issues.append(
            ValidationIssue(code="STUDENT_MISMATCH", message="建议包学生与分析结果学生不一致")
        )

    known_findings = {f.finding_id for f in analysis.findings}
    known_indicators = {i.indicator_id for i in catalog.indicators}
    materials_by_id = {m.material_id: m for m in catalog.materials}

    for rec in bundle.recommendations:
        rid = rec.recommendation_id

        for fid in rec.finding_ids:
            if fid not in known_findings:
                issues.append(
                    ValidationIssue(
                        code="FINDING_REF_INVALID",
                        message="建议引用了当前学生本次分析之外的诊断",
                        recommendation_id=rid,
                    )
                )

        if rec.indicator_id not in known_indicators:
            issues.append(
                ValidationIssue(
                    code="INDICATOR_REF_INVALID",
                    message="建议对应的学习要求不在当前课程目录中",
                    recommendation_id=rid,
                )
            )

        if rec.reason.strip().rstrip("。！") in ("", *VAGUE_PHRASES):
            issues.append(
                ValidationIssue(
                    code="VAGUE_REASON",
                    message="请写明推荐这项活动的具体理由",
                    recommendation_id=rid,
                )
            )
        expected_order = 1
        for act in rec.activities:
            if act.order != expected_order:
                issues.append(
                    ValidationIssue(
                        code="ACTIVITY_ORDER_INVALID",
                        message="活动顺序必须从 1 连续递增",
                        recommendation_id=rid,
                    )
                )
            expected_order = act.order + 1

            material = materials_by_id.get(act.material_id)
            if material is None:
                issues.append(
                    ValidationIssue(
                        code="MATERIAL_REF_INVALID",
                        message=f"第 {act.order} 项活动的材料不存在",
                        recommendation_id=rid,
                    )
                )
            elif rec.indicator_id not in material.indicator_ids:
                issues.append(
                    ValidationIssue(
                        code="MATERIAL_SCOPE_INVALID",
                        message=f"第 {act.order} 项活动的材料与这项学习要求不匹配",
                        recommendation_id=rid,
                    )
                )

            if not act.purpose.strip():
                issues.append(ValidationIssue(code="PURPOSE_MISSING", message=f"请填写第 {act.order} 项活动的具体做法", recommendation_id=rid))
            if not act.hint_levels or not all(text.strip() for text in act.hint_levels):
                issues.append(
                    ValidationIssue(
                        code="HINT_MISSING",
                        message=f"第 {act.order} 项活动缺少分步提示",
                        recommendation_id=rid,
                    )
                )
            elif material is not None:
                # 15 号文档 §六：提示不泄答的程序校验，保存前代码拦截
                for hint_idx, hint_text in enumerate(act.hint_levels, start=1):
                    leak = check_hint(
                        material.reference_answer, material.reference_explanation, hint_text
                    )
                    if leak:
                        issues.append(
                            ValidationIssue(
                                code="HINT_LEAKS_ANSWER",
                                message=(
                                    f"第 {act.order} 项活动的第 {hint_idx} 条提示可能直接透露答案，"
                                    "请改成引导学生思考的问题后再发布"
                                ),
                                recommendation_id=rid,
                            )
                        )
            if not act.observation_points or not all(text.strip() for text in act.observation_points):
                issues.append(
                    ValidationIssue(
                        code="OBSERVATION_MISSING",
                        message=f"第 {act.order} 项活动缺少观察点",
                        recommendation_id=rid,
                    )
                )

        if not rec.adjustment_conditions or any(
            not item.observable_condition.strip() or not item.next_action.strip() for item in rec.adjustment_conditions
        ):
            issues.append(
                ValidationIssue(
                    code="ADJUSTMENT_MISSING",
                    message="请至少填写一个后续调整条件",
                    recommendation_id=rid,
                )
            )

    return ValidationResult(valid=not issues, issues=issues)
