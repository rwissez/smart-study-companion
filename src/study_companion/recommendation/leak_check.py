"""泄答校验（开发手册 15 号文档 §六 提示不泄答的程序校验）。

AI 学伴给学生的分步提示不得直接给出参考答案或完整解题步骤；
靠提示词约束模型不可靠，必须在保存前用代码拦截。

实现为两段式：

1) 归一化（normalize）：对参考答案与提示文本同时做——去空白、
   全角转半角、×→* ÷→/ ≤→<= ≥→>=、去句首「解：/答：/∴/∵」前缀、
   去句末标点、数字去前导零。归一化只为比较，不改写存储内容。

2) 判定（check_hint）：按参考答案归一化长度分流：
   - 长度 >= 6 的表达式或文本：子串包含，或字符 3-gram 重合率 >= 0.8 → 泄漏
   - 长度 < 6 的短答案（如 9、x=9、是、否、无解、0）：不用子串包含，
     仅当答案出现在「答案位」才判泄漏——= 之后、答/得/即 之后
     （允许隔一个冒号或逗号）、所以 之后、或整段末尾。
     含 = 的短答案同时检查其值部分（如 x=9 的 9）。
   - 边界：答案两侧紧邻 ASCII 字母数字时视为嵌在更长标识符中
     （如 19 中的 9），不算独立出现；汉字不作为边界阻挡
     （中文语境「得x=9」「当x<=3时」是正常答案位表述）。
   短答案不用子串包含的理由：若用子串，「先判断不等式是否成立」
   会命中「是」、「这里无解释必要」会命中「无解」，误判会让所有
   提示被拒，比漏判更致命。

命中即拒绝：validate_recommendations 返回
ValidationIssue(code="HINT_LEAKS_ANSWER", ...)，该条建议应置
review_status="needs_review"，不得以 validated 状态保存；
模型生成的提示命中时重试一次，仍命中则回落规则提示
（保存路径由 A 侧强制调用本模块规则）。
"""

from __future__ import annotations

import re

_SENTENCE_END_PUNCT = "。！？；，,.!?;、"
_LEADING_PREFIXES = ("解：", "答：", "解:", "答:", "∴", "∵")
_SLOT_MARKERS = ("答", "得", "即")
_SLOT_SPACERS = "：:，,；;"
_GRAM_SIZE = 3
_GRAM_OVERLAP_THRESHOLD = 0.8


def normalize(text: str) -> str:
    """归一化文本，仅供比较使用；不改写存储内容。"""

    def to_halfwidth(s: str) -> str:
        out = []
        for ch in s:
            code = ord(ch)
            if code == 0x3000:
                out.append(" ")
            elif 0xFF01 <= code <= 0xFF5E:
                out.append(chr(code - 0xFEE0))
            else:
                out.append(ch)
        return "".join(out)

    s = to_halfwidth(text)
    for src, dst in (
        ("×", "*"),
        ("÷", "/"),
        ("≤", "<="),
        ("≥", ">="),
        ("∴", ""),
        ("∵", ""),
    ):
        s = s.replace(src, dst)
    # 去句首前缀（可连续出现，如「解：答：」）
    changed = True
    while changed:
        changed = False
        for prefix in _LEADING_PREFIXES:
            if s.startswith(prefix):
                s = s[len(prefix) :]
                changed = True
    s = s.rstrip(_SENTENCE_END_PUNCT)
    # 数字去前导零：09 → 9；10、0.5 不受影响
    s = re.sub(r"(?<!\d)0+(\d)", r"\1", s)
    s = re.sub(r"\s+", "", s)
    return s


def _ngrams(text: str, n: int = _GRAM_SIZE) -> set[str]:
    return {text[i : i + n] for i in range(len(text) - n + 1)}


def _is_word_char(ch: str) -> bool:
    """ASCII 字母数字视为标识符字符；汉字不作为边界阻挡。"""
    return ch.isascii() and ch.isalnum()


def _at_answer_slot(hint: str, start: int) -> bool:
    """答案出现位置的左侧是否处于「答案位」引导词或等号之后。"""
    k = start - 1
    if k >= 0 and hint[k] in _SLOT_SPACERS:
        k -= 1
    if k >= 0:
        if hint[k] == "=":
            return True
        if hint[k] in _SLOT_MARKERS:
            return True
        if k >= 1 and hint[k - 1 : k + 1] == "所以":
            return True
    return False


def _short_answer_leaks(hint: str, answer: str) -> bool:
    candidates = [answer]
    if "=" in answer:
        value = answer.rsplit("=", 1)[1]
        if value:
            candidates.append(value)
    for cand in candidates:
        for m in re.finditer(re.escape(cand), hint):
            s, e = m.start(), m.end()
            if s > 0 and _is_word_char(hint[s - 1]):
                continue  # 嵌在更长标识符里，如答案 9 出现在 19 中
            if e < len(hint) and _is_word_char(hint[e]):
                continue
            if e == len(hint) or _at_answer_slot(hint, s):
                return True
    return False


def _leaks(hint: str, reference: str) -> bool:
    norm_hint = normalize(hint)
    norm_ans = normalize(reference)
    if not norm_ans:
        return False
    if len(norm_ans) >= 6:
        if norm_ans in norm_hint:
            return True
        grams_ans = _ngrams(norm_ans)
        if not grams_ans:
            return False
        overlap = len(grams_ans & _ngrams(norm_hint)) / len(grams_ans)
        return overlap >= _GRAM_OVERLAP_THRESHOLD
    return _short_answer_leaks(norm_hint, norm_ans)


def check_hint(
    reference_answer: str | None,
    reference_explanation: str | None,
    hint_text: str,
) -> str | None:
    """检查一条提示是否泄漏参考答案或完整解析。

    返回泄漏原因描述；未泄漏返回 None。
    参考答案与参考解析任一存在即参与检查，两者都缺失则不判。
    """
    for label, reference in (
        ("参考答案", reference_answer),
        ("参考解析", reference_explanation),
    ):
        if reference and _leaks(hint_text, reference):
            return f"泄露{label}"
    return None
