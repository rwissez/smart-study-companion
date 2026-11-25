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
from study_companion.recommendation.strategy import choose_strategy

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
    exclude: frozenset[str] | None = None,
) -> list[Material]:
    """按指标选材；exclude 为本建议包已使用的材料（A 任务单 C-4：同一材料不重复出现）。"""
    # 过滤顺序（A 合并前反馈修复）：先全量取 → 排除本包已用 → 再截 limit；
    # 旧顺序先截后排会让已用材料挤占名额，可用题白白变少
    found = search_materials(
        catalog,
        MaterialSearch(indicator_ids=[indicator_id], material_types=list(types), limit=200),
    )
    if exclude:
        found = [m for m in found if m.material_id not in exclude]
    return found[:limit]


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


def _excerpt(material, limit: int = 24) -> str:
    """材料题干摘要（织入文案，治模板死板）：超长截断。"""
    text = (material.content or "").strip().replace("\n", " ")
    return text[:limit] + ("…" if len(text) > limit else "")


_FRUSTRATION_SIGNALS = ("均无有效", "多次", "连续", "仍未", "反复")


def _is_frustrated(finding: Finding) -> bool:
    """学生挫败信号的近似判定：基于诊断描述的文本特征。

    B 的 StrategySignal 到位后替换为结构化字段；教师备注类观察事件
    也可作为补充信号源。
    """
    return any(s in finding.description for s in _FRUSTRATION_SIGNALS)


# ---------------------------------------------------------------------------
# 四类建议生成（剧本 v4）
# ---------------------------------------------------------------------------

def _build_type3(finding: Finding, catalog: Catalog, rec_id: str, exclude: frozenset[str] = frozenset()) -> Recommendation:
    """证据不足：观察用题组，不下结论（剧本：先观察再说话）。"""
    indicator_id = finding.indicator_id
    materials = _materials_for(catalog, indicator_id, (_PRACTICE_TYPE,), 3, exclude=exclude)
    _require_materials(catalog, indicator_id, materials)

    activities = [
        _activity(
            m,
            i,
            purpose=f"观察题第 {i} 题（{_excerpt(m)}）：看这一问下思路是否走通，不判定会或不会",
            hints=(
                "这道题不用赶时间，按你的想法往下走；卡住了也没关系，跟我说说卡在哪就行"
                if i == 1
                else "继续按你的思路走，做完这道再看下一道",
            ),
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
        goal=f"用 {len(materials)} 道覆盖不同问法的题，看清学生在指标 {indicator_id} 上的真实掌握情况",
        reason=(
            (
                f"诊断 {finding.finding_id} 的记录之间说法互相冲突。"
                f"现在下结论为时过早，先安排 {len(materials)} 道覆盖不同问法的观察题，让证据自己说话。"
            )
            if finding.evidence_status == "conflicting"
            else (
                f"诊断 {finding.finding_id} 判定证据不足（{finding.evidence_status}）：{finding.description} "
                f"现有信息还撑不起“会或不会”的判断，先安排 {len(materials)} 道覆盖不同问法的观察题补齐证据。"
            )
        ),
        student_intro=(
            "今天先来几道小题。不是考试，就是想听听你的想法：慢慢做，"
            "卡住了也没关系，跟我说说卡在哪就好。"
        ),
        activities=activities,
        adjustment_conditions=[
            AdjustmentCondition(
                observable_condition="题组证据齐备",
                next_action="按证据归类到 1a/1b/类型二并重新生成建议",
                group="advance",
            ),
            AdjustmentCondition(
                observable_condition="学生未完成观察题组",
                next_action="记录 limitation，不强行归因，教师端如实呈现该生未参与本轮观察",
                group="wrap_up",
            ),
        ],
        review_status="draft",
        generation_method="rule",
    )


def _build_type2(finding: Finding, catalog: Catalog, rec_id: str, exclude: frozenset[str] = frozenset()) -> Recommendation:
    """表现较好：两级迁移。先知识迁移（变式），再思想辨识选择（学生选、学伴解释，不刷题）。"""
    indicator_id = finding.indicator_id
    variants = _materials_for(catalog, indicator_id, (_PRACTICE_TYPE,), 2, exclude=exclude)
    _require_materials(catalog, indicator_id, variants)

    activities = [
        _activity(
            variants[0],
            1,
            purpose=f"知识迁移（{_excerpt(variants[0])}）：换了个问法，看还能不能一眼认出来",
            hints=("这道题和你之前做的比，有什么不一样的地方？如果你觉得它变了，是哪一步变了？",),
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
                purpose="思想迁移：思想辨识选择（低频环节：同一思想每周至多出现一次，三个思想可轮换），学生从三个数学思想中选出他认为最核心的一个，学伴解释他的选择",
                hints=(
                    # 题干示例绑定 synthetic-v1 材料目录（M001/M006），目录更换需同步调整
                    "这些题你都解对了。有个有意思的事想跟你确认：解 2x+3<11 时最后"
                    "变成了 x>4，解 ax<b 时要先想 a 是正还是负。你觉得这些做法里"
                    "反复出现的是哪一个？A. 化归：把没见过的题，变成会做的旧题；"
                    "B. 数形结合：用图来想数的问题；C. 分类讨论：情况不止一种时，"
                    "分开想。选一个就行。",
                ),
                points=(
                    "选择结果本身（三个思想均真实存在，无错误选项）",
                    "学伴反馈是否把三个思想都覆盖到",
                ),
            )
        )
    return Recommendation(
        recommendation_id=rec_id,
        indicator_id=indicator_id,
        finding_ids=[finding.finding_id],
        goal=(
            f"在指标 {indicator_id} 上完成知识迁移（说清每步依据）与思想迁移（选并解释一个数学思想）"
            if len(variants) > 1
            else f"在指标 {indicator_id} 上完成知识迁移，说清每步依据"
        ),
        reason=(
            (
                f"诊断 {finding.finding_id} 判定表现较好（{finding.description}）。"
                "不追加刷题量：先用变式确认换问法后是否仍能识别，"
                "再用思想辨识选择（学生选、学伴解释）做思想迁移。"
                "思想辨识是低频环节：同一思想每周至多一次，三个思想可轮换，"
                "学生表现持续稳定的不重复安排。"
            )
            if len(variants) > 1
            else (
                f"诊断 {finding.finding_id} 判定表现较好（{finding.description}）。"
                "不追加刷题量：本轮先用变式确认知识迁移；思想辨识环节需要配套选题，"
                "留到材料齐备时再做，不为凑环节硬安排。"
            )
        ),
        student_intro=(
            "最近这些题你做得挺稳。今天为你准备了一道变式，问法换了个角度，核心没变；"
            "做完有个小选择想听听你的想法。"
            if len(variants) > 1
            else "最近这些题你做得挺稳。今天为你准备了一道变式，问法换了个角度，"
            "核心没变，看看你还能不能认出它。"
        ),
        activities=activities,
        adjustment_conditions=[
            AdjustmentCondition(
                observable_condition="完成选择，反馈已呈现",
                next_action="本指标收尾，选择记录为亮点证据（教师端展示）",
                group="wrap_up",
            ),
            AdjustmentCondition(
                observable_condition="学生对思想有进一步疑问",
                next_action="学伴顺势延伸讨论，不设为必经环节；持续困惑则建议面谈",
                group="escalate_teacher",
            ),
            AdjustmentCondition(
                observable_condition="同一思想本周已出现过",
                next_action="跳过该思想的本轮辨识，可轮换另一思想或直接收尾",
                group="advance",
            ),
            AdjustmentCondition(
                observable_condition="学生表现持续稳定无新变化",
                next_action="不重复安排任务，仅更新亮点证据",
                group="wrap_up",
            ),
            AdjustmentCondition(
                observable_condition="变式题出错",
                next_action="回落第一级补一道，思想任务顺延",
                group="fall_back",
            ),
        ],
        review_status="draft",
        generation_method="rule",
    )


def _build_1a(finding: Finding, catalog: Catalog, rec_id: str, exclude: frozenset[str] = frozenset()) -> Recommendation:
    """有思路但走不通：倾听式引导 + 巩固，不追加新课。"""
    indicator_id = finding.indicator_id
    consolidations = _materials_for(catalog, indicator_id, (_PRACTICE_TYPE,), 2, exclude=exclude)
    _require_materials(catalog, indicator_id, consolidations)

    activities = [
        _activity(
            m,
            i,
            purpose=f"倾听式引导后的巩固（{_excerpt(m)}）：按修正后的思路独立走完，再代入检验",
            hints=(
                (
                    "按你刚才修正的思路从头走一遍，走完把答案代回去，看看式子还成立吗"
                    if i == 1
                    else "同样的路子再来一遍：先解，再把答案代回去验一验，看两头还对得上吗"
                ),
            ),
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
        goal=f"按修正后思路独立完成指标 {indicator_id} 的 {len(consolidations)} 道同类题，并保持代入检验习惯",
        reason=(
            f"诊断 {finding.finding_id}：{finding.description} "
            f"思路完整、失败点集中。学伴先按倾听式四步（复述确认→定位分岔→引导自撞→修正重走）"
            f"听学生讲当时的想法，走通后只安排 {len(consolidations)} 道巩固题，不追加新课。"
        ),
        student_intro=(
            "我看了你的解题过程，思路里有几处值得聊。"
            "我想听你讲讲当时是怎么想的，咱们从一道差不多的题开始。"
        ),
        activities=activities,
        adjustment_conditions=[
            AdjustmentCondition(
                observable_condition="巩固题全对",
                next_action="记录修正证据，进入类型二两级迁移",
                group="advance",
            ),
            AdjustmentCondition(
                observable_condition="同类位置再次出错",
                next_action="降级到 1b 递减干预，从对应性质补台阶",
                group="fall_back",
            ),
        ],
        review_status="draft",
        generation_method="rule",
    )


def _build_1b(finding: Finding, catalog: Catalog, rec_id: str, config: RecommendationConfig, exclude: frozenset[str] = frozenset()) -> Recommendation:
    """纯粹不懂：递减干预扶练。接住即放手，回头题受限在 reason 中如实声明。"""
    indicator_id = finding.indicator_id
    support = _materials_for(catalog, indicator_id, _SUPPORT_TYPES, 1, exclude=exclude)
    practices = _materials_for(catalog, indicator_id, (_PRACTICE_TYPE,), 2, exclude=exclude)
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
                purpose=f"独立走完整流程（{_excerpt(m)}）：提示上限随扶助接住层级递减",
                hints=(
                    "解完以后，用自己的办法检查一遍；拿不准检查什么的话，先想想这题最容易错的是哪一步",
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
        goal=f"在指标 {indicator_id} 上补齐基础台阶，独立完成 {len(practices)} 道同类题并自查",
        reason=(
            (
                f"诊断 {finding.finding_id}：{finding.description} "
                "先用学习卡从方向层补台阶（L1→L2→L3 逐层、接住即停），"
                f"再做 {len(practices)} 道同类题；扶的目的是尽早放手，"
                "接住的层级越浅，练习的提示上限越低。"
                "若想让学生重做自己的原错题，教师可在记录列表中指定那道题。"
            )
            if support
            else (
                f"诊断 {finding.finding_id}：{finding.description} "
                f"本轮目录内暂无配套学习卡，直接安排 {len(practices)} 道同类题观察独立完成度，教师可视情况补讲。"
                "若想让学生重做自己的原错题，教师可在记录列表中指定那道题。"
            )
        ),
        student_intro=(
            (
                "这题不怪你，它的步骤就是容易乱。我们从你最有把握的那小步开始，"
                "走一步算一步；错了也有用，错的位置正是我们要找的。"
                if _is_frustrated(finding)
                else "这道题卡住很正常，它本身就是多步的。咱们把它拆小，"
                "从你能确定的那一步做起，做完一层我看一层。"
            )
            if support
            else (
                "这题确实不好下手，硬啃没有意义。我先给个小台阶：从最基础的那条性质过一遍，"
                "你觉得从哪一步开始没底，我们就从哪儿补。"
                if _is_frustrated(finding)
                else "先别急着完整解题，咱们只做第一步：把题目要求的量圈出来。"
                "后面的我们一层一层来。"
            )
        ),
        activities=activities,
        adjustment_conditions=[
            AdjustmentCondition(
                observable_condition="回头重做原错题全对",
                next_action="进入类型二两级迁移",
                group="advance",
            ),
            AdjustmentCondition(
                observable_condition="仍错",
                next_action="记录新证据并提示教师介入",
                group="escalate_teacher",
            ),
        ],
        review_status="draft",
        generation_method="rule",
    )


_BUILDERS = {
    "type3": lambda f, c, rid, cfg, excl: _build_type3(f, c, rid, excl),
    "type2": lambda f, c, rid, cfg, excl: _build_type2(f, c, rid, excl),
    "1a": lambda f, c, rid, cfg, excl: _build_1a(f, c, rid, excl),
    "1b": lambda f, c, rid, cfg, excl: _build_1b(f, c, rid, cfg, excl),
}


# ---------------------------------------------------------------------------
# 固定签名：规则建议
# ---------------------------------------------------------------------------

def recommend_rules(
    analysis: StudentResult,
    catalog: Catalog,
    context: RunContext,
    config: RecommendationConfig,
    attempted_material_ids: frozenset[str] = frozenset(),
) -> RecommendationBundle:
    """基于已验证分析结果与材料目录，生成规则建议（剧本 v4 四类 + 策略选择器）。

    红线：不重算统计；所有数字只转述 analysis 现有内容；
    每条建议挂材料编号与诊断编号；上下文不一致直接拒绝。

    策略选择器（2026-11-24 团队拍板落地）：
    - 先归并（同指标同类诊断合并、前置链失分归并）再生成，不再逐条机械输出
    - 优先级：证据不足先观察 > 尚未掌握先扶练 > 倾听式纠错 > 思想迁移
    - 负荷上限 max_recommendations，超出暂缓并写进调整条件
    - 前置检查结论写进理由（教师语言，只转述本批 findings，不产生新判断）

    attempted_material_ids：学生已作答过的材料（如 record→material 关联），
    选材时排除，保证变式/扶练/观察题都是没做过的题；排除后无题可用的指标转暂缓。
    """
    _context_check(analysis, context)
    if catalog.version != context.catalog_version:
        raise DomainError(
            "CONTEXT_MISMATCH",
            "材料目录版本与运行上下文不一致",
            [{"field": "catalog_version", "reason": f"目录为 {catalog.version}，上下文为 {context.catalog_version}"}],
        )

    plan = choose_strategy(analysis.findings, catalog, config)
    recommendations: list[Recommendation] = []
    used_materials: set[str] = set()
    exhausted: list[str] = []  # 可用材料耗尽转暂缓的指标（A 任务单 C-4）
    for seq, entry in enumerate(plan.entries, start=1):
        primary = entry.findings[0]
        rec_id = f"rec-{analysis.student_id}-{analysis.node_id}-{primary.indicator_id}-{seq:02d}"
        try:
            rec = _BUILDERS[entry.kind](
                primary, catalog, rec_id, config, frozenset(used_materials) | attempted_material_ids
            )
        except DomainError as err:
            if err.code == "MATERIAL_NOT_FOUND":
                # 同一材料不重复出现；排除已用材料后无题可用 → 该知识点转暂缓，不硬凑重复题
                exhausted.append(primary.indicator_id)
                continue
            raise
        used_materials.update(a.material_id for a in rec.activities)

        updates: dict[str, object] = {}
        merged_ids = [f.finding_id for f in entry.findings[1:]]
        reason = rec.reason
        if merged_ids:
            updates["finding_ids"] = rec.finding_ids + merged_ids
            reason = (
                reason
                + f"（本条合并同指标同类诊断：{'、'.join(merged_ids)}，不重复安排两份练习）"
            )
        if entry.rationale:
            reason = reason + f"策略说明：{entry.rationale}"
        if reason != rec.reason:
            updates["reason"] = reason

        conditions = list(rec.adjustment_conditions)
        for later_id in entry.follow_up_indicators:
            conditions.append(
                AdjustmentCondition(
                    observable_condition=f"指标 {primary.indicator_id} 的补练完成且复评通过",
                    next_action=f"再安排指标 {later_id} 的同类练习（此前暂缓，不与补练叠加）",
                )
            )
        if conditions != rec.adjustment_conditions:
            updates["adjustment_conditions"] = conditions

        if updates:
            rec = rec.model_copy(update=updates)
        recommendations.append(rec)

    # 暂缓汇总（负荷截断 + 材料耗尽），统一写进最后一条保留建议的调整条件
    deferred_all = list(plan.deferred_indicators) + exhausted
    if deferred_all and recommendations:
        last = recommendations[-1]
        note = (
            f"本周学习安排以不超过 {config.max_recommendations} 个知识点为限（减负原则），"
            f"指标 {'、'.join(deferred_all)} 暂不安排，下周跟进"
        )
        if exhausted:
            note += "；其中部分因避免重复使用同一批材料而暂缓，教师可另指定材料"
        recommendations[-1] = last.model_copy(
            update={
                "adjustment_conditions": list(last.adjustment_conditions)
                + [AdjustmentCondition(observable_condition="学生完成本轮全部活动", next_action=note, group="advance")]
            }
        )
    elif deferred_all:
        # 全部指标暂缓（如材料耗尽）：不留空包，生成一条零活动的暂缓说明，让教师看得见原因
        deferred_findings = [f.finding_id for f in analysis.findings]
        primary_ind = plan.entries[0].findings[0].indicator_id if plan.entries else (
            analysis.findings[0].indicator_id if analysis.findings else ""
        )
        recommendations.append(
            Recommendation(
                recommendation_id=f"rec-{analysis.student_id}-{analysis.node_id}-DEFERRED-01",
                indicator_id=primary_ind,
                finding_ids=deferred_findings,
                goal="本周不安排练习：先向教师说明暂缓原因并等待补充材料",
                reason=(
                    f"指标 {'、'.join(deferred_all)} 暂不安排："
                    + ("目录中可用材料均已使用过，为避免重复刷题暂缓（教师可另指定材料）" if exhausted else "负荷上限控制")
                ),
                student_intro="今天没有给你安排新练习，我先看看你最近的学习情况，有想法随时跟我说。",
                activities=[],
                adjustment_conditions=[
                    AdjustmentCondition(
                        observable_condition="教师在材料目录中补充新题目或调整安排",
                        next_action=f"重新为指标 {'、'.join(deferred_all)} 生成学习建议",
                        group="advance",
                    )
                ],
                review_status="draft",
                generation_method="rule",
            )
        )

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
    config: RecommendationConfig | None = None,
) -> ValidationResult:
    """校验建议包（05 验收 + A 任务单 C-5 扩展）：

    - 运行身份一致（run/dataset/catalog/student 与分析结果对齐）
    - finding_ids 必须属于当前学生本次分析
    - indicator_id 必须存在于目录
    - 材料必须存在且适配该指标
    - activities 顺序从 1 连续递增，提示与观察点非空
    - 调整条件非空
    - 空话禁令：理由必须落到材料编号或诊断编号
    - 提示泄答拦截（15 号文档 §六）：HINT_LEAKS_ANSWER，命中不得以 validated 状态保存
    - 发布保护：UNVERIFIED_MATERIAL_PUBLISHED，未核对材料不得发布给学生
    - 重复建议阻断：同一 finding 不得出现在多条建议
    - 重复材料阻断：同一 material 不得出现在多条建议（不让学生刷同一题两遍）
    - 负荷超限阻断：建议条数超过 max_recommendations（需传入 config 启用）
    - 表述去重：同一建议包内目标（goal）完全相同 → DUPLICATE_GOAL（呈现层去重兜底）
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

    # bundle 级：重复建议 / 重复材料 / 负荷超限（A 任务单 C-5）
    seen_finding: dict[str, str] = {}
    seen_material: dict[str, str] = {}
    for rec in bundle.recommendations:
        for fid in rec.finding_ids:
            if fid in seen_finding:
                issues.append(
                    ValidationIssue(
                        code="DUPLICATE_RECOMMENDATION",
                        message=(
                            f"诊断 {fid} 同时出现在建议 {seen_finding[fid]} 与 {rec.recommendation_id}；"
                            "同一诊断不应生成多条建议（重复建议）"
                        ),
                        recommendation_id=rec.recommendation_id,
                    )
                )
            else:
                seen_finding[fid] = rec.recommendation_id
        for act in rec.activities:
            if act.material_id in seen_material:
                issues.append(
                    ValidationIssue(
                        code="DUPLICATE_MATERIAL",
                        message=(
                            f"材料 {act.material_id} 同时出现在建议 {seen_material[act.material_id]} 与 "
                            f"{rec.recommendation_id}；同一材料不应重复安排给学生"
                        ),
                        recommendation_id=rec.recommendation_id,
                    )
                )
            else:
                seen_material[act.material_id] = rec.recommendation_id
    # 表述层去重（呈现去重兜底）：同一包内 goal 完全相同视为重复呈现
    seen_goal: dict[str, str] = {}
    for rec in bundle.recommendations:
        if rec.goal in seen_goal:
            issues.append(
                ValidationIssue(
                    code="DUPLICATE_GOAL",
                    message=(
                        f"建议 {rec.recommendation_id} 与 {seen_goal[rec.goal]} 的目标表述完全相同；"
                        "同一建议包内不应出现重复目标"
                    ),
                    recommendation_id=rec.recommendation_id,
                )
            )
        else:
            seen_goal[rec.goal] = rec.recommendation_id
    if config is not None and len(bundle.recommendations) > config.max_recommendations:
        issues.append(
            ValidationIssue(
                code="LOAD_LIMIT_EXCEEDED",
                message=(
                    f"建议包包含 {len(bundle.recommendations)} 条建议，"
                    f"超出单次负荷上限 {config.max_recommendations}（减负原则）"
                ),
            )
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

            if (
                material is not None
                and material.source_locator.verification_status == "unverified"
            ):
                # 发布保护硬规则（A 建议⑤）：未核对材料不得进入将发布给学生的建议
                issues.append(
                    ValidationIssue(
                        code="UNVERIFIED_MATERIAL_PUBLISHED",
                        message=(
                            f"建议 {rid} 活动 {act.order} 引用的材料 {material.material_id} "
                            "未完成人工核对（verification_status=unverified）；"
                            "未核对材料不得直接发布给学生，请教师核对后将状态更新为 verified"
                        ),
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
                                    f"建议 {rid} 活动 {act.order} 第 {hint_idx} 级提示{leak}；"
                                    "该建议应置 review_status=needs_review，不得以 validated 状态保存"
                                ),
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
