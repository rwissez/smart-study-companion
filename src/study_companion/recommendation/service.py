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

RULE_VERSION = "c-rules-v1"

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
_MIN_REASON_LEN = 12

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


def _require_materials(catalog: Catalog, indicator_id: str, materials: list[Material]) -> None:
    if not materials:
        raise DomainError(
            "MATERIAL_NOT_FOUND",
            f"指标 {indicator_id} 在材料目录中无适配材料，无法生成建议",
            [{"field": "indicator_id", "reason": "目录中无适配材料"}],
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

def _build_type3(finding: Finding, catalog: Catalog, rec_id: str) -> Recommendation:
    """证据不足：观察用题组，不下结论（剧本：先观察再说话）。"""
    indicator_id = finding.indicator_id
    materials = _materials_for(catalog, indicator_id, (_PRACTICE_TYPE,), 3)
    _require_materials(catalog, indicator_id, materials)

    activities = [
        _activity(
            m,
            i,
            purpose=f"观察用题组第 {i} 题：收集证据，不判定会或不会",
            hints=("这几道小题帮我们看看你的思路，不着急，做完可以自己检查",),
            points=(
                "完成度与过程完整度",
                f"错误是否集中在同一位置（对照诊断 {finding.finding_id}）",
            ),
        )
        for i, m in enumerate(materials, start=1)
    ]
    return Recommendation(
        recommendation_id=rec_id,
        indicator_id=indicator_id,
        finding_ids=[finding.finding_id],
        goal=f"针对指标 {indicator_id} 补齐观察证据，再定学习安排",
        reason=(
            f"诊断 {finding.finding_id} 判定证据不足（{finding.evidence_status}）。"
            "按剧本红线不输出会/不会结论，先安排覆盖不同变式的观察题组收集证据。"
        ),
        activities=activities,
        adjustment_conditions=[
            AdjustmentCondition(
                observable_condition="题组证据齐备",
                next_action="按证据归类到 1a/1b/类型二并重新生成建议",
            ),
            AdjustmentCondition(
                observable_condition="学生未完成观察题组",
                next_action="记录 limitation，不强行归因，教师端如实呈现该生未参与本轮观察",
            ),
        ],
        review_status="draft",
        generation_method="rule",
    )


def _build_type2(finding: Finding, catalog: Catalog, rec_id: str) -> Recommendation:
    """表现较好：两级迁移。先知识迁移（变式），再思想迁移（总结性小任务，不刷题）。"""
    indicator_id = finding.indicator_id
    variants = _materials_for(catalog, indicator_id, (_PRACTICE_TYPE,), 2)
    _require_materials(catalog, indicator_id, variants)

    activities = [
        _activity(
            variants[0],
            1,
            purpose="知识迁移：变式或综合题，检验换问法后是否仍能识别",
            hints=("这道题和你之前做的比，有什么不一样的地方",),
            points=(
                "是否主动识别变化点",
                "每步依据哪条性质说得清",
            ),
        )
    ]
    if len(variants) > 1:
        activities.append(
            _activity(
                variants[1],
                2,
                purpose="思想迁移：总结性小任务，从做过的题里长出数学思想（数形结合/分类讨论/化归）",
                hints=(
                    "你解过的这些题最后都变成了什么样子？解不等式是在往什么方向变？",
                ),
                points=(
                    "能否说出思想名称及适用时机",
                    "能否举出自己经历过的例子",
                ),
            )
        )
    return Recommendation(
        recommendation_id=rec_id,
        indicator_id=indicator_id,
        finding_ids=[finding.finding_id],
        goal=f"在指标 {indicator_id} 上完成知识与思想两级迁移，说清每步依据",
        reason=(
            f"诊断 {finding.finding_id} 判定表现较好。按剧本不追加刷题量："
            "先用变式确认知识迁移，再用总结性小任务引导思想迁移。"
        ),
        activities=activities,
        adjustment_conditions=[
            AdjustmentCondition(
                observable_condition="能说出思想并举出自己的例子",
                next_action="本指标收尾，记录为亮点证据（教师端展示）",
            ),
            AdjustmentCondition(
                observable_condition="会做但说不出思想",
                next_action="教师端标注建议面谈追问，学生端不硬灌名词",
            ),
            AdjustmentCondition(
                observable_condition="变式题出错",
                next_action="回落第一级补一道，思想任务顺延",
            ),
        ],
        review_status="draft",
        generation_method="rule",
    )


def _build_1a(finding: Finding, catalog: Catalog, rec_id: str) -> Recommendation:
    """有思路但走不通：倾听式引导 + 巩固，不追加新课。"""
    indicator_id = finding.indicator_id
    consolidations = _materials_for(catalog, indicator_id, (_PRACTICE_TYPE,), 2)
    _require_materials(catalog, indicator_id, consolidations)

    activities = [
        _activity(
            m,
            i,
            purpose="按修正后思路独立完成同类题，并用代入检验自查（倾听式引导后的巩固）",
            hints=("先独立完成，再把答案代回原式检查一遍",),
            points=(
                "修正点（如方向处理）是否稳定",
                "检验是否主动发生",
            ),
        )
        for i, m in enumerate(consolidations, start=1)
    ]
    return Recommendation(
        recommendation_id=rec_id,
        indicator_id=indicator_id,
        finding_ids=[finding.finding_id],
        goal=f"按修正后思路独立完成指标 {indicator_id} 的同类题，并保持代入检验习惯",
        reason=(
            f"诊断 {finding.finding_id} 显示思路完整、失败点集中。"
            "按剧本 1a 采用倾听式引导（复述确认→定位分岔→引导自撞→修正重走，"
            "见 prompts/diagnosis.md 流程），建议只安排巩固，不追加新课。"
        ),
        activities=activities,
        adjustment_conditions=[
            AdjustmentCondition(
                observable_condition="巩固题全对",
                next_action="记录修正证据，进入类型二两级迁移",
            ),
            AdjustmentCondition(
                observable_condition="同类位置再次出错",
                next_action="降级到 1b 递减干预，从对应性质补台阶",
            ),
        ],
        review_status="draft",
        generation_method="rule",
    )


def _build_1b(finding: Finding, catalog: Catalog, rec_id: str, config: RecommendationConfig) -> Recommendation:
    """纯粹不懂：递减干预扶练。接住即放手，回头题受限在 reason 中如实声明。"""
    indicator_id = finding.indicator_id
    support = _materials_for(catalog, indicator_id, _SUPPORT_TYPES, 1)
    practices = _materials_for(catalog, indicator_id, (_PRACTICE_TYPE,), 2)
    _require_materials(catalog, indicator_id, practices)

    activities: list[Activity] = []
    order = 1
    if support:
        activities.append(
            _activity(
                support[0],
                order,
                purpose="递减干预式扶助：从 L1 方向层开始，每层设探针，接住即放手",
                hints=(
                    "先回想一下，不等号两边同时加一个数，方向会不会变？",
                    "学习卡热身题做完对答案卡片，卡哪道只追问那道",
                    "完整讲解版例题（兜底层，用完即回探针）",
                ),
                points=(
                    "最终接住的层级（L1/L2/L3）：层级越浅，后续练习提示上限越低",
                ),
            )
        )
        order += 1
    for i, m in enumerate(practices, start=order):
        activities.append(
            _activity(
                m,
                i,
                purpose="独立走完整流程，提示上限随扶助接住层级递减",
                hints=(
                    "解完以后，用自己的办法检查一遍",
                    "把你的答案代回原式，看不等式还成立吗",
                ),
                points=(
                    "失分位置是否修复",
                    "需要的提示层是否比扶助阶段更浅",
                ),
            )
        )
    activities = activities[: config.max_activities]

    return Recommendation(
        recommendation_id=rec_id,
        indicator_id=indicator_id,
        finding_ids=[finding.finding_id],
        goal=f"在指标 {indicator_id} 上补齐基础台阶，独立完成求解并自查",
        reason=(
            f"诊断 {finding.finding_id} 判定需练习且无可用思路信号。"
            "按剧本 1b 采用递减干预扶练：扶的目的是尽早放手，接住即进入独立练习。"
            "重做原错题（回头）需要原题材料定位，建议生成时以诊断引用记录为准由服务端"
            "补充记录与材料的关联（已知契约缺口），教师可先从记录列表指定。"
        ),
        activities=activities,
        adjustment_conditions=[
            AdjustmentCondition(
                observable_condition="回头重做原错题全对",
                next_action="进入类型二两级迁移",
            ),
            AdjustmentCondition(
                observable_condition="仍错",
                next_action="记录新证据并提示教师介入",
            ),
        ],
        review_status="draft",
        generation_method="rule",
    )


_BUILDERS = {
    "type3": lambda f, c, rid, cfg: _build_type3(f, c, rid),
    "type2": lambda f, c, rid, cfg: _build_type2(f, c, rid),
    "1a": lambda f, c, rid, cfg: _build_1a(f, c, rid),
    "1b": _build_1b,
}


# ---------------------------------------------------------------------------
# 固定签名：规则建议
# ---------------------------------------------------------------------------

def recommend_rules(
    analysis: StudentResult,
    catalog: Catalog,
    context: RunContext,
    config: RecommendationConfig,
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
        kind = classify_finding(finding)
        rec_id = f"rec-{analysis.student_id}-{analysis.node_id}-{finding.indicator_id}-{seq:02d}"
        recommendations.append(_BUILDERS[kind](finding, catalog, rec_id, config))

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
    - 空话禁令：理由必须落到材料编号或诊断编号
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
    if bundle.student_id != analysis.student_id:
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
                        message=f"建议 {rid} 引用的诊断 {fid} 不属于当前学生本次分析",
                        recommendation_id=rid,
                    )
                )

        if rec.indicator_id not in known_indicators:
            issues.append(
                ValidationIssue(
                    code="INDICATOR_REF_INVALID",
                    message=f"建议 {rid} 的指标 {rec.indicator_id} 不在材料目录中",
                    recommendation_id=rid,
                )
            )

        if not rec.reason or len(rec.reason.strip()) < _MIN_REASON_LEN:
            issues.append(
                ValidationIssue(
                    code="VAGUE_REASON",
                    message=f"建议 {rid} 缺少具体理由（05 空话禁令）",
                    recommendation_id=rid,
                )
            )
        else:
            concrete_tokens = list(known_findings) + list(materials_by_id.keys())
            has_concrete_ref = any(token in rec.reason for token in concrete_tokens)
            vague_only = any(p in rec.reason for p in VAGUE_PHRASES)
            if not has_concrete_ref and vague_only:
                issues.append(
                    ValidationIssue(
                        code="VAGUE_REASON",
                        message=f"建议 {rid} 理由空泛：需落到材料编号或诊断编号",
                        recommendation_id=rid,
                    )
                )

        expected_order = 1
        for act in rec.activities:
            if act.order != expected_order:
                issues.append(
                    ValidationIssue(
                        code="ACTIVITY_ORDER_INVALID",
                        message=f"建议 {rid} 活动顺序必须从 1 连续递增",
                        recommendation_id=rid,
                    )
                )
            expected_order = act.order + 1

            material = materials_by_id.get(act.material_id)
            if material is None:
                issues.append(
                    ValidationIssue(
                        code="MATERIAL_REF_INVALID",
                        message=f"建议 {rid} 活动 {act.order} 引用的材料 {act.material_id} 不存在",
                        recommendation_id=rid,
                    )
                )
            elif rec.indicator_id not in material.indicator_ids:
                issues.append(
                    ValidationIssue(
                        code="MATERIAL_SCOPE_INVALID",
                        message=f"建议 {rid} 活动 {act.order} 的材料不适配指标 {rec.indicator_id}",
                        recommendation_id=rid,
                    )
                )

            if not act.hint_levels:
                issues.append(
                    ValidationIssue(
                        code="HINT_MISSING",
                        message=f"建议 {rid} 活动 {act.order} 缺少分步提示",
                        recommendation_id=rid,
                    )
                )
            if not act.observation_points:
                issues.append(
                    ValidationIssue(
                        code="OBSERVATION_MISSING",
                        message=f"建议 {rid} 活动 {act.order} 缺少观察点",
                        recommendation_id=rid,
                    )
                )

        if not rec.adjustment_conditions:
            issues.append(
                ValidationIssue(
                    code="ADJUSTMENT_MISSING",
                    message=f"建议 {rid} 缺少调整条件（剧本：出现什么证据时升级/继续/转观察）",
                    recommendation_id=rid,
                )
            )

    return ValidationResult(valid=not issues, issues=issues)
