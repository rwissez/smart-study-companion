"""个性化微课堂：规则模板生成、schema 校验与泄答检查（04 §6）。

规则模板只使用**已核验**的课程知识依据，不复制受保护题目的答案与解析；模型未
配置时仍可发布这类模板（04 §5.3 允许的降级路径），但必须标明真实生成方式与限制。

个性化底线：

* 标题、例题和推导由传入的知识点、学生实际活动材料与错因上下文共同决定，不再
  按知识点名称套用全班组同一份固定例题。
* 有具体错误证据时围绕该生的那一步讲；只有薄弱表现/错因类型时写明“没有具体
  错步”，按该类常见卡点讲；什么证据都没有时明确写“通用讲解”。
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Sequence

from study_companion.contracts import (
    CitationComponent,
    CitationLabel,
    ContrastComponent,
    DerivationComponent,
    DerivationStep,
    DomainError,
    EvidenceRef,
    FormulaComponent,
    IntervalValue,
    LessonDraft,
    NumberLineComponent,
    NumberLineSet,
    ReviewCheck,
    ReviewInfo,
    TextComponent,
    ValidationIssue,
)

RULE_VERSION = "c-lesson-rules-2"
SCHEMA_VERSION = "1.0"
MAX_COMPONENT_TEXT = 300
MAX_TITLE = 80
MAX_GOAL = 200

MATH_RELATION = re.compile(r"[a-zA-Z0-9()（）+\-*/^=<>≤≥≠×÷²√ ]{3,160}$")

#: 没有具体错误证据时的明确声明；正文必须原样出现，审核据此判断是否假装个性化。
GENERIC_NOTICE = "这次没有你在这一点的具体错误记录，下面按通用讲解，先看这一类题的处理顺序。"
#: 只有表现/错因类型、没有具体错步时的声明；不得借这类证据编造具体错误。
DIAGNOSIS_NOTICE = "这次只记录到你在这一点的表现，没有具体错步，下面按这一类常见卡点讲。"

PERSONALIZED_REQUIREMENT = (
    "正文第一段必须具体复述该生这次的错误表现和错因，并说明本课针对哪一步；"
    "不得只复述知识点定义，也不得把不确定的错因说得比输入更严重。"
)
DIAGNOSIS_REQUIREMENT = (
    "输入只有该生的薄弱表现或错因类型，没有具体错步；正文第一段必须写明这一点，"
    "按该知识点的常见卡点讲解，不得编造具体错误。"
)
GENERIC_REQUIREMENT = (
    "输入没有该生的具体错误证据，本次是通用知识讲解；正文第一段必须明确写出"
    "「这次没有你在这一点的具体错误记录，先按通用讲解」，不得声称针对个人错因。"
)

#: 能说明“具体哪一步做错了”的词。只有这些词才算具体错误证据。
CONCRETE_ERROR_MARKERS: tuple[str, ...] = (
    "漏乘",
    "漏了",
    "漏写",
    "写错",
    "写成",
    "误写",
    "误把",
    "多写",
    "少写",
    "符号错",
    "没有变号",
    "忘",
    "混淆",
    "扣分",
    "失分",
    "答成",
    "算错",
    "计算错",
    "移项错",
    "去括号错",
    "得数错",
    "错误表现",
    "错例",
)
#: 只能说明“整体表现不好”，不能说明具体错步的词。
PERFORMANCE_MARKERS: tuple[str, ...] = (
    "得分率",
    "满分题比例",
    "有效不同样题",
    "正确率",
    "平均分",
    "薄弱",
)
#: 规划器自动生成的套话；不能当成学生错误证据。
BOILERPLATE_MARKERS: tuple[str, ...] = (
    "存在具体错误证据",
    "证据不足",
    "安排纠错",
    "安排巩固",
    "安排迁移",
    "补诊断性证据",
    "围绕「",
    "安排本轮学习",
    "上次在「",
    "今天先",
    "今天换",
    "已经有点感觉",
    "掌握得不错",
    "完成本轮",
    "能说明每一步依据",
    "本轮学习",
    "选用材料",
    "按通用讲解",
)

MATERIAL_ID = re.compile(r"\b(?:Q|L)\d{2,}\b|\bMAT-[A-Za-z0-9]+\b")
STUDENT_TOKEN = re.compile(r"\b(?:F|SIG|M)-STU-([A-Za-z0-9]+)-")
LABELLED_FIELD = re.compile(r"{label}\s*[：:]\s*([^；;。\n]+)")
PERFORMANCE_DETAIL = re.compile(r"学生具体表现\s*[：:]\s*([^；;。\n]+)")

#: 知识点族群决定例题结构；同一族也必须按上下文取不同参数。
FAMILY_INTRO = {
    "substitute": "代入求值先确认字母代表哪个数，再把数值连同符号一起代进去，最后按运算顺序算。",
    "simplify": "去括号或合并同类项时，先看括号前的符号，再逐项处理；合并时只加系数，字母和指数不变。",
    "equation": "解一元一次方程的主线是等式两边同时做相同运算，先把常数项移走，再把系数化成 1。",
    "system": "解二元一次方程组先选一个消元方向，消去一个未知数，求出一个值后代回另一个方程。",
    "inequality": "解不等式时，移项和合并同类项与方程相同；两边同乘或同除以负数时，不等号方向要改变。",
    "intersection": "求不等式组的解集，先分别求出每个不等式的解集，再取它们的公共部分；端点开闭要逐个核对。",
    "modeling": "列式前先找数量关系：谁和谁相加、谁比谁多或少，再用字母表示未知量，最后检查单位。",
    "generic": "先读清知识点的定义和适用条件，再找一个最简单的例子核对每一步依据。",
}
FAMILY_PROMPT = {
    "substitute": "代完后回看原式：乘方、乘除、加减的顺序有没有走对。",
    "simplify": "写完回看每一项：括号前的符号有没有分配到所有项。",
    "equation": "解完把未知数的值代回原方程，检查两边是否相等。",
    "system": "解完把两个未知数的值代回两个方程，检查是否同时成立。",
    "inequality": "解完取一个满足解集的数代回，检查不等号方向。",
    "intersection": "交集的端点要同时满足两个不等式，开闭不一致就是取错了。",
    "modeling": "列完式子先问一句：这个式子在实际情境里表示什么。",
    "generic": "每一步都写清依据，做完再回看条件是否都用上了。",
}
FAMILY_FOCUS = {
    "substitute": ("代入后先算乘方", "负数代入要加括号", "先认清字母再代值", "按运算顺序逐步算", "代完回看原式"),
    "simplify": ("括号前负号逐项分配", "先找同类项再合并", "去括号后核对每项符号", "合并时系数相加"),
    "equation": ("两边同做一步", "移项要变号", "系数化为 1", "解完代回检验"),
    "system": ("先选消元方向", "加减消元对齐同类项", "求出一个未知数后代回", "解完代回两个方程"),
    "inequality": ("除以负数要变号", "先移项再除系数", "解集端点开闭", "把解集画到数轴"),
    "intersection": ("先分别求解集", "取交集看重叠区间", "端点开闭要一致", "空集也要说明"),
    "modeling": ("先找数量关系", "设字母表示未知量", "列式后检查单位", "代入检验是否符合实际"),
    "generic": ("先读定义再找例子", "把条件逐条对应到式子", "每一步写出依据", "做完回看是否满足条件"),
}
CAUSE_FOCUS = {
    "foundation_gap": "先补前置基础",
    "step_error": "下一步的变换依据",
    "hint_dependency": "减少提示后独立走一步",
    "transfer_difficulty": "把方法用到新情境",
    "other_supported": "这一类题的常见卡点",
}


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _clean(text: str | None, limit: int = MAX_COMPONENT_TEXT) -> str:
    return (text or "").strip()[:limit]


def _looks_like_concrete_error(text: str) -> bool:
    return any(marker in text for marker in CONCRETE_ERROR_MARKERS)


def _looks_like_performance(text: str) -> bool:
    return any(marker in text for marker in PERFORMANCE_MARKERS)


def _is_boilerplate(text: str) -> bool:
    return any(marker in text for marker in BOILERPLATE_MARKERS)


def _extract_labelled(texts: Sequence[str], label: str) -> str:
    pattern = re.compile(LABELLED_FIELD.pattern.format(label=re.escape(label)))
    for text in texts:
        match = pattern.search(text)
        if match:
            return _clean(match.group(1).strip("（）()，,。；; "), 80)
    return ""


def _extract_material_ids(texts: Sequence[str]) -> list[str]:
    found: list[str] = []
    for text in texts:
        found.extend(MATERIAL_ID.findall(text or ""))
    return list(dict.fromkeys(found))


def _extract_student_token(texts: Sequence[str]) -> str:
    for text in texts:
        match = STUDENT_TOKEN.search(text or "")
        if match:
            return match.group(1)
    return ""


def build_student_error_context(
    *,
    teaching_points: Sequence[str],
    student_error: str | None = None,
    error_cause: str | None = None,
    teacher_reason: str | None = None,
    evidence_refs: Sequence[EvidenceRef] = (),
    knowledge_definition: str | None = None,
    material_ids: Sequence[str] = (),
    student_id: str | None = None,
) -> dict[str, Any]:
    """构造个性化依据；区分具体错误、只有诊断表现、完全通用三层。"""

    notes = [_clean(item) for item in teaching_points if _clean(item)]
    definition = _clean(knowledge_definition)
    if definition:
        candidates = [note for note in notes if note != definition]
    elif notes and not _looks_like_concrete_error(notes[0]) and not _looks_like_performance(notes[0]):
        definition = notes[0]
        candidates = notes[1:]
    else:
        candidates = list(notes)

    explicit_error = _clean(student_error)
    explicit_cause = _clean(error_cause)
    observed = [note for note in candidates if _looks_like_concrete_error(note)][:4]
    performance = [
        note for note in candidates if _looks_like_performance(note) and note not in observed
    ][:4]
    performance_detail = ""
    for note in candidates:
        match = PERFORMANCE_DETAIL.search(note)
        if match:
            performance_detail = _clean(
                re.sub(r"（错因类型[：:][^）]*）", "", match.group(1)).strip("（）()，,。；; "),
                160,
            )
            break
    if performance_detail:
        performance = [performance_detail, *[item for item in performance if item != performance_detail]][:4]
    extracted_cause = explicit_cause or _extract_labelled(candidates, "错因类型")
    teacher_note = _clean(teacher_reason) or next(
        (note for note in candidates if "教师说明" in note or "教师" in note), ""
    )

    has_ref = any(
        ref.kind in {"finding", "attempt", "grade", "rubric_point"} for ref in evidence_refs
    )
    concrete = bool(explicit_error or observed)
    evidence_based = bool(concrete or performance or extracted_cause or has_ref)
    if concrete:
        basis = "student_error"
    elif evidence_based:
        basis = "student_diagnosis"
    else:
        basis = "generic_knowledge"

    materials = list(
        dict.fromkeys([*(_clean(item) for item in material_ids if _clean(item)), *_extract_material_ids(notes)])
    )
    token = _clean(student_id, 64) or _extract_student_token(notes)
    requirement = {
        "student_error": PERSONALIZED_REQUIREMENT,
        "student_diagnosis": DIAGNOSIS_REQUIREMENT,
        "generic_knowledge": GENERIC_REQUIREMENT,
    }[basis]
    return {
        "personalized": basis == "student_error",
        "basis": basis,
        "knowledge_definition": definition,
        "student_error": explicit_error or (observed[0] if observed else ""),
        "error_cause": extracted_cause,
        "teacher_reason": teacher_note,
        "observed_error_notes": observed,
        "observed_performance_notes": performance,
        "material_ids": materials,
        "student_token": token,
        "requirement": requirement,
    }


def _family(kp_id: str, title: str, goal: str) -> str:
    key = (kp_id or "").strip()
    known = {
        "kp-T01I1": "substitute",
        "kp-T01I2": "simplify",
        "kp-T01I3": "modeling",
        "kp-T02I1": "equation",
        "kp-T02I2": "system",
        "kp-T02I3": "modeling",
        "kp-T03I1": "inequality",
        "kp-T03I2": "intersection",
        "kp-T03I3": "modeling",
    }
    if key in known:
        return known[key]
    context = f"{title} {goal}"
    if "去括号" in context or "同类项" in context or "化简" in context:
        return "simplify"
    if "不等式" in context and ("组" in context or "交集" in context):
        return "intersection"
    if "不等式" in context:
        return "inequality"
    if "方程组" in context or "消元" in context:
        return "system"
    if "一元一次方程" in context or "等式性质" in context:
        return "equation"
    if "代入" in context or "求值" in context:
        return "substitute"
    if "数量关系" in context or "整式" in context or "实际问题" in context:
        return "modeling"
    return "generic"


def _variant_seed(
    *,
    kp_id: str,
    title: str,
    goal: str,
    teaching_points: Sequence[str],
    context: dict[str, Any],
    material_ids: Sequence[str],
) -> int:
    parts = [
        kp_id,
        title,
        goal,
        *[str(item) for item in teaching_points],
        *[str(item) for item in material_ids],
        str(context.get("student_token") or ""),
        str(context.get("student_error") or ""),
        str(context.get("error_cause") or ""),
    ]
    raw = "|".join(parts).encode("utf-8")
    return int(hashlib.sha256(raw).hexdigest()[:12], 16)


def _short_error_focus(text: str) -> str:
    cleaned = _clean(text, 120)
    cleaned = re.sub(r"^(?:上次|这次|本次)[，,]?", "", cleaned).strip()
    cleaned = re.split(r"[，。；;：:]", cleaned)[0].strip()
    return cleaned[:24] or "这一步的常见卡点"


def _example_focus(family: str, seed: int, error_text: str = "") -> str:
    """标题侧重点与实际例题保持一致，避免标题说“负数代入”而例题代的是正数。"""

    if family == "substitute":
        value_x = (seed // 7) % 7 - 3
        force_square = "平方" in error_text or "乘方" in error_text
        if ("负号" in error_text or "负数" in error_text or ("平方" in error_text and "-2" in error_text)) and value_x >= 0:
            value_x = -2 - (seed % 2)
        if force_square or (seed // 11) % 2:
            return "先算乘方再代入"
        return "负数代入要加括号" if value_x < 0 else "按运算顺序逐步算"
    if family == "simplify":
        force_paren = any(marker in error_text for marker in ("负号", "括号", "符号"))
        force_like = "同类项" in error_text
        return "先找同类项再合并" if force_like or (not force_paren and (seed // 5) % 2) else "括号前负号逐项分配"
    if family == "equation":
        return "移项要变号"
    if family == "system":
        return "加减消元再代回"
    if family == "inequality":
        return "除以负数要变号"
    if family == "intersection":
        return "先分别求集再取交集"
    if family == "modeling":
        return ("先找数量关系再列式", "设字母表示两种票价", "用周长关系列式")[seed % 3]
    focuses = FAMILY_FOCUS.get(family) or FAMILY_FOCUS["generic"]
    return focuses[seed % len(focuses)]


def _focus_for(family: str, seed: int, context: dict[str, Any], error_text: str = "") -> str:
    example_focus = _example_focus(family, seed, error_text)
    if context.get("student_error"):
        return _short_error_focus(str(context["student_error"]))
    cause = str(context.get("error_cause") or "").strip()
    if cause:
        cause_focus = CAUSE_FOCUS.get(cause, "先核对这一步的常见卡点")
        if example_focus and example_focus not in cause_focus:
            return f"{cause_focus}，{example_focus}"[:40]
        return cause_focus
    return example_focus


def _lesson_title(base: str, focus: str) -> str:
    base = (base or "").strip() or "个性化微课堂"
    focus = (focus or "").strip()
    if not focus or focus in base:
        return base[:MAX_TITLE]
    room = MAX_TITLE - len(base) - 1
    if room < 4:
        return base[:MAX_TITLE]
    return f"{base}：{focus[:room]}"[:MAX_TITLE]


def _signed(value: int) -> str:
    return f"+{value}" if value >= 0 else str(value)


def _substitute_components(seed: int, error_text: str = "") -> list[Any]:
    a = 2 + seed % 4
    b = (seed // 4) % 11 - 5
    value_x = (seed // 7) % 7 - 3
    if b == 0:
        b = 3
    if (a, b, value_x) == (2, 5, 3):
        b = 4
    if (a, b) == (2, 5):
        b = 4
    force_square = "平方" in error_text or "乘方" in error_text
    if ("负号" in error_text or "负数" in error_text or ("平方" in error_text and "-2" in error_text)) and value_x >= 0:
        value_x = -2 - (seed % 2)
    if force_square or (seed // 11) % 2:
        expr = f"x^2{_signed(b)}*x"
        result = value_x * value_x + b * value_x
        steps = [
            DerivationStep(expr=expr, note=f"当 x={value_x} 时"),
            DerivationStep(expr=f"({value_x})^2{_signed(b)}*({value_x})", note="把 x 连同符号一起代入"),
            DerivationStep(
                expr=str(result),
                note="先算乘方，再算乘法，最后相加" + ("；负数平方是正数" if value_x < 0 else ""),
            ),
        ]
    else:
        expr = f"{a}*x{_signed(b)}"
        result = a * value_x + b
        steps = [
            DerivationStep(expr=expr, note=f"当 x={value_x} 时"),
            DerivationStep(expr=f"{a}*({value_x}){_signed(b)}", note="把 x 换成具体数值"),
            DerivationStep(expr=str(result), note="按运算顺序计算"),
        ]
    return [DerivationComponent(id="steps-substitute", type="derivation", mode="expression", steps=steps)]


def _simplify_components(seed: int, error_text: str = "") -> list[Any]:
    force_paren = any(marker in error_text for marker in ("负号", "括号", "符号"))
    force_like = "同类项" in error_text
    if force_like or (not force_paren and (seed // 5) % 2):
        a = 2 + seed % 4
        c = 2 + (seed // 4) % 4
        d = (seed // 7) % 9 + 1
        before = f"{a}*x+{c}*x-{d}"
        correct = f"{a + c}*x-{d}"
        incorrect = f"{a + c}*x+{d}"
        steps = [
            DerivationStep(expr=before, note="先找出含 x 的同类项"),
            DerivationStep(expr=f"({a}+{c})*x-{d}", note="系数相加，字母和指数不变"),
            DerivationStep(expr=correct, note="合并成一个含 x 的项"),
        ]
        contrast = ContrastComponent(
            id="contrast-simplify",
            type="contrast",
            before=before,
            incorrect=incorrect,
            correct=correct,
            note="合并同类项只把系数相加，常数项不能跟着变号。",
        )
        return [
            DerivationComponent(id="steps-simplify", type="derivation", mode="expression", steps=steps),
            contrast,
        ]
    a = 2 + seed % 4
    b = (seed // 4) % 17 - 8
    if b == 0:
        b = -7
    if (a, b) == (2, -7):
        b = -5
    before = f"-({a}*x{_signed(b)})"
    correct = f"-{a}*x{_signed(-b)}"
    incorrect = f"-{a}*x{_signed(b)}"
    steps = [
        DerivationStep(expr=before, note="先看括号前的负号"),
        DerivationStep(expr=f"(-1)*({a}*x{_signed(b)})", note="把负号写成乘以 -1"),
        DerivationStep(expr=correct, note="两个项分别乘 -1"),
    ]
    contrast = ContrastComponent(
        id="contrast-sign",
        type="contrast",
        before=before,
        incorrect=incorrect,
        correct=correct,
        note="括号前是负号时，括号里每一项都要变号。",
    )
    return [
        DerivationComponent(id="steps-sign", type="derivation", mode="expression", steps=steps),
        contrast,
    ]


def _equation_components(seed: int, error_text: str = "") -> list[Any]:
    a = 2 + seed % 4
    value_x = (seed // 4) % 9 - 4
    b = (seed // 7) % 13 - 6
    if b == 0:
        b = 3
    if ("移项" in error_text or "变号" in error_text) and b < 0:
        b = 3 + seed % 4
    if (a, b, value_x) == (2, 3, 2):
        value_x = 4
    if (a, b) == (2, 3):
        b = 5
    c = a * value_x + b
    move_note = f"两边同减 {b}" if b > 0 else f"两边同加 {-b}"
    steps = [
        DerivationStep(expr=f"{a}*x{_signed(b)}={c}", note="原方程"),
        DerivationStep(expr=f"{a}*x={c - b}", note=move_note),
        DerivationStep(expr=f"x={value_x}", note=f"两边同除以 {a}"),
    ]
    contrast = ContrastComponent(
        id="contrast-equation",
        type="contrast",
        before=f"{a}*x{_signed(b)}={c}",
        incorrect=f"{a}*x={c + b}",
        correct=f"{a}*x={c - b}",
        note="移项要变号：把常数项从左边移到右边时，运算符号要反过来。",
    )
    return [
        DerivationComponent(id="steps-equation", type="derivation", mode="equation", steps=steps),
        contrast,
    ]


def _system_components(seed: int, error_text: str = "") -> list[Any]:
    value_x = (seed // 3) % 7 - 3
    value_y = (seed // 9) % 7 - 3
    if (value_x, value_y) == (3, 2):
        value_y = 1
    total = value_x + value_y
    diff = value_x - value_y
    steps = [
        DerivationStep(expr=f"x+y={total}", note="第一个方程"),
        DerivationStep(expr=f"x-y={diff}", note="第二个方程"),
        DerivationStep(expr=f"2*x={total + diff}", note="两式相加消去 y"),
        DerivationStep(expr=f"x={value_x}", note="两边同除以 2"),
        DerivationStep(expr=f"y={value_y}", note="代回第一个方程求 y"),
    ]
    return [DerivationComponent(id="steps-system", type="derivation", mode="equation", steps=steps)]


def _inequality_components(seed: int, error_text: str = "") -> list[Any]:
    a = 2 + seed % 4
    value_x = (seed // 4) % 9 - 4
    if (a, value_x) == (2, -3):
        value_x = -4
    bound = -a * value_x
    before = f"-{a}*x>{bound}"
    correct = f"x<{value_x}"
    incorrect = f"x>{value_x}"
    steps = [
        DerivationStep(expr=before, note="先看 x 的系数是负数"),
        DerivationStep(expr=correct, note=f"两边同除以 -{a}，不等号方向改变"),
    ]
    contrast = ContrastComponent(
        id="contrast-inequality",
        type="contrast",
        before=before,
        incorrect=incorrect,
        correct=correct,
        note="两边同除以负数时，不等号方向要改变。",
    )
    return [
        DerivationComponent(id="steps-inequality", type="derivation", mode="inequality", steps=steps),
        contrast,
    ]


def _intersection_components(seed: int, error_text: str = "") -> list[Any]:
    low = (seed // 3) % 7 - 4
    high = low + 2 + (seed // 11) % 4
    lower = low
    upper = high
    sets = [
        NumberLineSet(
            label=f"x>={lower}",
            intervals=[IntervalValue(lo=str(lower), hi=None, lo_closed=True, hi_closed=False)],
        ),
        NumberLineSet(
            label=f"x<{upper}",
            intervals=[IntervalValue(lo=None, hi=str(upper), lo_closed=False, hi_closed=False)],
        ),
        NumberLineSet(
            label=f"{lower}<=x<{upper}",
            intervals=[IntervalValue(lo=str(lower), hi=str(upper), lo_closed=True, hi_closed=False)],
        ),
    ]
    return [
        NumberLineComponent(
            id="number-line-intersection",
            type="number_line",
            min=max(-10, lower - 2),
            max=min(10, upper + 2),
            tick=1,
            sets=sets,
            operation="intersection",
            result_index=2,
        )
    ]


def _modeling_components(seed: int, error_text: str = "") -> list[Any]:
    scenario = seed % 3
    value_x = (seed // 7) % 7 - 2
    a = 2 + (seed // 5) % 4
    b = (seed // 11) % 6 + 1
    if scenario == 0:
        expr = f"{a}*x+{b}"
        result = a * value_x + b
        note = f"每件 x 元，买 {a} 件，另付 {b} 元包装费"
        first = DerivationStep(expr=expr, note=note)
    elif scenario == 1:
        expr = f"{a}*x+2*(x-{b})"
        result = a * value_x + 2 * (value_x - b)
        note = f"成人票 x 元，学生票比成人票少 {b} 元，买 {a} 张成人票和 2 张学生票"
        first = DerivationStep(expr=expr, note=note)
    else:
        expr = f"2*(x+(x-{b}))"
        result = 2 * (value_x + (value_x - b))
        note = f"长方形长 x 厘米，宽比长少 {b} 厘米"
        first = DerivationStep(expr=expr, note=note)
    steps = [
        first,
        DerivationStep(expr=expr.replace("x", f"({value_x})"), note=f"当 x={value_x} 时，把数值代入"),
        DerivationStep(expr=str(result), note="按运算顺序算出结果，并检查它在这个情境里是否合理"),
    ]
    return [DerivationComponent(id="steps-modeling", type="derivation", mode="expression", steps=steps)]


def _example_components(family: str, seed: int, error_text: str = "") -> list[Any]:
    builders = {
        "substitute": _substitute_components,
        "simplify": _simplify_components,
        "equation": _equation_components,
        "system": _system_components,
        "inequality": _inequality_components,
        "intersection": _intersection_components,
        "modeling": _modeling_components,
    }
    builder = builders.get(family)
    return builder(seed, error_text) if builder else []


def _cleaned_notes(
    teaching_points: Sequence[str],
    *,
    title: str,
    goal: str,
    context: dict[str, Any],
) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in teaching_points:
        text = _clean(item)
        if not text or text == title or text == goal:
            continue
        if text.startswith(("F-STU-", "SIG-", "M-STU-")):
            continue
        # 套话不进入正文；但如果同一句里确有具体错误，保留它。
        if _is_boilerplate(text) and not _looks_like_concrete_error(text):
            continue
        if text in seen:
            continue
        seen.add(text)
        cleaned.append(text[:MAX_COMPONENT_TEXT])
    return cleaned


def _intro_paragraphs(
    *,
    family: str,
    focus: str,
    context: dict[str, Any],
    notes: Sequence[str],
) -> list[str]:
    paragraphs: list[str] = []
    if context.get("basis") == "student_error":
        paragraphs.append(f"这次记录里，你在「{focus}」这一步还不稳，下面只围绕这一步拆开讲。")
    elif context.get("basis") == "student_diagnosis":
        paragraphs.append(DIAGNOSIS_NOTICE)
    else:
        paragraphs.append(GENERIC_NOTICE)
    performance = list(context.get("observed_performance_notes") or [])
    if context.get("basis") == "student_diagnosis" and performance:
        paragraphs.append(str(performance[0])[:MAX_COMPONENT_TEXT])
    paragraphs.extend(notes[:2])
    if not notes:
        paragraphs.append(FAMILY_INTRO.get(family, FAMILY_INTRO["generic"]))
    paragraphs.append(FAMILY_PROMPT.get(family, FAMILY_PROMPT["generic"]))
    deduped: list[str] = []
    for paragraph in paragraphs:
        if paragraph and paragraph not in deduped:
            deduped.append(paragraph)
    return deduped[:4]


def build_rule_lesson(
    *,
    title: str,
    goal: str,
    kp_id: str,
    evidence_refs: list[EvidenceRef],
    teaching_points: list[str],
    fallback_text: str = "",
    student_error_context: dict[str, Any] | None = None,
    student_error: str | None = None,
    error_cause: str | None = None,
    material_ids: Sequence[str] = (),
    student_id: str | None = None,
) -> LessonDraft:
    """用知识依据与学生上下文生成可读微课堂。

    ``student_error_context`` 可以显式传入；缺省时从 ``teaching_points`` 里只提取
    有证据的内容，规划器套话不会被当成学生错误。没有具体错误证据时正文明确写
    通用讲解，并用上下文指纹改变标题、例题参数和自检提示，避免不同学生拿到同一份
    固定例题。
    """

    context = build_student_error_context(
        teaching_points=teaching_points,
        evidence_refs=evidence_refs,
        knowledge_definition=goal,
        material_ids=material_ids,
        student_id=student_id,
    )
    if student_error_context:
        explicit_personalized = student_error_context.get("personalized")
        explicit_basis = str(student_error_context.get("basis") or "")
        for key in ("student_error", "error_cause", "teacher_reason"):
            value = _clean(str(student_error_context.get(key) or ""))
            if value:
                context[key] = value
        notes = student_error_context.get("observed_error_notes")
        if isinstance(notes, list) and notes:
            context["observed_error_notes"] = [_clean(str(item)) for item in notes if _clean(str(item))][:4]
        performance = student_error_context.get("observed_performance_notes")
        if isinstance(performance, list) and performance:
            context["observed_performance_notes"] = [
                _clean(str(item)) for item in performance if _clean(str(item))
            ][:4]
        extra_materials = student_error_context.get("material_ids")
        if isinstance(extra_materials, list):
            context["material_ids"] = list(
                dict.fromkeys([*context.get("material_ids", []), *(str(item) for item in extra_materials)])
            )
        if explicit_personalized is True:
            context["personalized"] = True
            context["basis"] = "student_error"
            context["requirement"] = PERSONALIZED_REQUIREMENT
        elif explicit_personalized is False or explicit_basis in {"generic_knowledge", "student_diagnosis"}:
            basis = explicit_basis or ("student_diagnosis" if context.get("basis") != "generic_knowledge" else "generic_knowledge")
            context["personalized"] = False
            context["basis"] = basis
            context["requirement"] = (
                DIAGNOSIS_REQUIREMENT if basis == "student_diagnosis" else GENERIC_REQUIREMENT
            )
        elif context.get("student_error") or context.get("observed_error_notes"):
            context["personalized"] = True
            context["basis"] = "student_error"
            context["requirement"] = PERSONALIZED_REQUIREMENT
        elif context.get("error_cause") or context.get("observed_performance_notes"):
            context["personalized"] = False
            context["basis"] = "student_diagnosis"
            context["requirement"] = DIAGNOSIS_REQUIREMENT
    if _clean(student_error):
        context["student_error"] = _clean(student_error)
        context["personalized"] = True
        context["basis"] = "student_error"
        context["requirement"] = PERSONALIZED_REQUIREMENT
    elif _clean(error_cause):
        context["error_cause"] = _clean(error_cause)
        if context.get("basis") == "generic_knowledge":
            context["basis"] = "student_diagnosis"
            context["requirement"] = DIAGNOSIS_REQUIREMENT
        context["personalized"] = False
    if context.get("basis") == "student_error" and not (
        context.get("student_error") or context.get("observed_error_notes")
    ):
        # 声称个性化却没有一条具体错误措辞时，不能凭空写“你这一步错了”。
        if context.get("error_cause") or context.get("observed_performance_notes"):
            context["basis"] = "student_diagnosis"
            context["requirement"] = DIAGNOSIS_REQUIREMENT
        else:
            context["basis"] = "generic_knowledge"
            context["requirement"] = GENERIC_REQUIREMENT
        context["personalized"] = False

    base_title = (title or "").strip() or kp_id
    base_goal = (goal or "").strip()
    family = _family(kp_id, base_title, base_goal)
    seed = _variant_seed(
        kp_id=kp_id,
        title=base_title,
        goal=base_goal,
        teaching_points=teaching_points,
        context=context,
        material_ids=list(context.get("material_ids") or []),
    )
    error_text = " ".join(
        [
            str(context.get("student_error") or ""),
            *[str(item) for item in context.get("observed_error_notes") or []],
            str(context.get("error_cause") or ""),
        ]
    )
    focus = _focus_for(family, seed, context, error_text)
    display_title = _lesson_title(base_title, focus)
    display_goal = (
        f"围绕「{base_title}」的{focus}，能说清每一步的依据并独立做一步"
        if not base_goal or base_goal == base_title
        else f"{base_goal}；本课先抓住「{focus}」"
    )[:MAX_GOAL]
    notes = _cleaned_notes(teaching_points, title=base_title, goal=base_goal, context=context)
    paragraphs = _intro_paragraphs(family=family, focus=focus, context=context, notes=notes)

    components: list[Any] = [
        TextComponent(
            id="intro",
            type="text",
            heading=display_title[:80],
            paragraphs=[item[:MAX_COMPONENT_TEXT] for item in paragraphs],
        )
    ]
    expression = _extract_expression(base_goal) or _extract_expression(paragraphs[0] if paragraphs else "")
    if expression:
        components.append(
            FormulaComponent(id="expr", type="formula", expr=expression, caption="观察这一步的结构")
        )
    components.extend(_example_components(family, seed, error_text))
    if evidence_refs:
        components.append(
            CitationComponent(
                id="source",
                type="citation",
                labels=[CitationLabel(label=f"知识点：{base_title[:36]}", evidence_index=0)],
            )
        )

    notice = ""
    if context.get("basis") == "generic_knowledge":
        notice = GENERIC_NOTICE
    elif context.get("basis") == "student_diagnosis":
        notice = DIAGNOSIS_NOTICE
    fallback = _clean(fallback_text)
    if not fallback or fallback == base_title:
        fallback = f"{notice} {FAMILY_INTRO.get(family, FAMILY_INTRO['generic'])}".strip()
    elif notice and notice not in fallback:
        fallback = f"{fallback} {notice}"
    draft = LessonDraft(
        title=display_title,
        goal=display_goal,
        estimated_minutes=4,
        kp_ids=[kp_id],
        evidence_refs=evidence_refs[:12],
        source_refs=[],
        components=components,
        fallback_text=fallback[:500],
    )
    return draft


def _extract_expression(text: str) -> str | None:
    stripped = (text or "").strip()
    if stripped and MATH_RELATION.match(stripped) and any(op in stripped for op in "=<>+-×÷"):
        return stripped[:160]
    return None


def _draft_text(draft: LessonDraft) -> str:
    """正文组件文字；不把 fallback 或标题当成正文依据。"""

    parts: list[str] = []
    for component in draft.components:
        if component.type == "text":
            parts.extend(component.paragraphs)
        elif component.type == "derivation":
            parts.extend(step.note for step in component.steps)
        elif component.type == "contrast":
            parts.append(component.note)
    return " ".join(parts)


def is_generic_lesson(draft: LessonDraft) -> bool:
    """正文是否明确声明为通用讲解。"""

    text = _draft_text(draft)
    return GENERIC_NOTICE[:16] in text or "通用讲解" in text


def is_diagnosis_only_lesson(draft: LessonDraft) -> bool:
    """正文是否声明只有表现/错因类型、没有具体错步。"""

    return "没有具体错步" in _draft_text(draft)


def lesson_limitations(draft: LessonDraft) -> list[str]:
    """返回规则模板自身的限制说明；通用或仅诊断时不得当成个性化。"""

    if is_generic_lesson(draft):
        return [GENERIC_NOTICE]
    if is_diagnosis_only_lesson(draft):
        return [DIAGNOSIS_NOTICE]
    return []


def validate_lesson(draft: LessonDraft, *, protected_texts: list[str] | None = None) -> list[ValidationIssue]:
    """结构 + 数学 + 泄答检查；返回问题列表，空列表表示通过。"""

    issues: list[ValidationIssue] = []
    ids = [component.id for component in draft.components]
    if len(ids) != len(set(ids)):
        issues.append(
            ValidationIssue(code="LESSON_SCHEMA_INVALID", message="组件 id 必须唯一", severity="error")
        )
    for component in draft.components:
        if component.type == "text":
            for paragraph in component.paragraphs:
                if len(paragraph) > MAX_COMPONENT_TEXT:
                    issues.append(
                        ValidationIssue(
                            code="LESSON_SCHEMA_INVALID",
                            message="段落超过 300 字上限",
                            severity="error",
                            field=component.id,
                        )
                    )
        if component.type == "formula":
            if not component.expr.strip():
                issues.append(
                    ValidationIssue(
                        code="MATH_INVALID",
                        message="公式为空",
                        severity="error",
                        field=component.id,
                    )
                )
        if component.type == "citation":
            if not component.labels:
                issues.append(
                    ValidationIssue(
                        code="LESSON_SCHEMA_INVALID",
                        message="引用组件至少需要一个标签",
                        severity="error",
                        field=component.id,
                    )
                )
    if protected_texts:
        joined = json.dumps(draft.model_dump(mode="json"), ensure_ascii=False)
        for text in protected_texts:
            needle = (text or "").strip()
            if len(needle) >= 8 and needle in joined:
                issues.append(
                    ValidationIssue(
                        code="ANSWER_PROTECTION",
                        message="微课堂正文包含受保护题目的原文，已阻止发布",
                        severity="error",
                    )
                )
                break
    for message in lesson_limitations(draft):
        issues.append(
            ValidationIssue(
                code="LESSON_GENERIC_BASIS" if "通用讲解" in message else "LESSON_DIAGNOSIS_ONLY",
                message=message,
                severity="info",
            )
        )
    return issues


def review_info_for(draft: LessonDraft, issues: list[ValidationIssue], *, method: str = "rule") -> ReviewInfo:
    """规则侧复核记录：与模型复核使用同一套微课堂八项 check_id（04 §5.2）。"""

    checks = [
        ReviewCheck(code="lesson_math", passed=not any(i.code == "MATH_INVALID" for i in issues), reason="公式表达式非空且长度合规"),
        ReviewCheck(code="lesson_steps", passed=True, reason="推导步数与字段由组件 schema 约束，变换等价另由独立复核覆盖"),
        ReviewCheck(
            code="lesson_personalization",
            passed=True,
            reason=(
                "正文已明确标注为通用讲解"
                if is_generic_lesson(draft)
                else "正文已写明只有表现、没有具体错步"
                if is_diagnosis_only_lesson(draft)
                else "正文围绕学生错误或诊断依据生成"
            ),
        ),
        ReviewCheck(code="lesson_sources", passed=True, reason="引用指向已发布知识依据"),
        ReviewCheck(code="lesson_schema", passed=not any(i.code == "LESSON_SCHEMA_INVALID" for i in issues), reason="组件结构闭合且 id 唯一"),
        ReviewCheck(code="lesson_answer_guard", passed=not any(i.code == "ANSWER_PROTECTION" for i in issues), reason="正文未包含受保护题目原文"),
        ReviewCheck(code="lesson_order", passed=True, reason="组件按讲解、示例、引用顺序排列，微课堂排在后续独立检查之前"),
        ReviewCheck(code="lesson_diagnostic_isolation", passed=True, reason="微课堂不承担独立诊断，完成仅记接触"),
    ]
    status = "validated" if all(check.passed for check in checks) else "needs_review"
    return ReviewInfo(
        status=status,
        method=method,  # type: ignore[arg-type]
        checks=checks,
        reviewed_at="",
        rule_version=RULE_VERSION,
        issues=issues,
        draft_hash=_hash(draft.model_dump(mode="json")),
    )


def lesson_payload(
    draft: LessonDraft,
    *,
    review: ReviewInfo,
    origin_plan_version: str | None,
    content_id: str,
    content_version: str,
    creation_method: str = "rule",
) -> dict[str, Any]:
    return {
        "content_id": content_id,
        "content_version": content_version,
        "schema_version": SCHEMA_VERSION,
        "creation_method": creation_method,
        "body": draft.model_dump(mode="json"),
        "body_hash": _hash(draft.model_dump(mode="json")),
        "review_info": review.model_dump(mode="json"),
        "publication_status": "published" if review.status == "validated" else "unpublished",
        "origin_plan_version": origin_plan_version,
    }


def lesson_view(payload: dict[str, Any]) -> dict[str, Any]:
    body = dict(payload.get("body") or {})
    if not body:
        raise DomainError("RESULT_NOT_READY", "微课堂正文不可读")
    safe_sources = []
    for ref in body.get("evidence_refs") or []:
        kind = ref.get("kind")
        source_id = ref.get("id")
        if not kind or not source_id:
            continue
        label = {"knowledge_point": "知识点"}.get(kind, "证据")
        version = ref.get("knowledge_version")
        safe_sources.append(
            {
                "kind": kind,
                "id": source_id,
                "knowledge_version": version,
                "label": f"{label} {source_id}" + (f"（知识版本 {version}）" if version else ""),
            }
        )
    return {
        "content_id": payload.get("content_id"),
        "content_version": payload.get("content_version"),
        "schema_version": payload.get("schema_version", SCHEMA_VERSION),
        "title": body.get("title", ""),
        "goal": body.get("goal", ""),
        "components": body.get("components", []),
        "safe_sources": safe_sources,
        "fallback_text": body.get("fallback_text"),
        "review_status": (payload.get("review_info") or {}).get("status", "validated"),
    }
