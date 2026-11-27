"""回答事实绑定（05 §10）。

本模块在 Agent 最终文本返回前做一次确定性的「结构化回答草稿」：

1. 把回答拆成句子级草稿段，抽取其中的数字、指标和证据断言；
2. 数字只在本轮工具结果（``ToolTrace.result``）里找得到时保留；
3. 引用的证据编号只在本轮授权证据集合里找得到时保留；
4. 找不到出处的句子整句删除，并在末尾追加限制说明，不把它当确定事实输出。

只做确定性核对，不判断解释是否正确、不做语义改写。百分数按
``83% == 0.83`` 的确定性规则换算后比较。序号类数字（``第2步``、``第3页``、
列表编号）不属于事实断言，不参与绑定。

程序无法核对时由调用方降级为安全文本，本模块不抛业务异常。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Literal

#: 数字 token；前面紧邻 ASCII 字母/数字/下划线时视为标识符的一部分（如 R01、M_S01）。
NUMBER_PATTERN = re.compile(r"(?<![0-9A-Za-z_])(-?\d+(?:\.\d+)?)(%?)")

#: 「依据记录 R01」「指标 M_S01」「依据 **R01**」这类显式证据断言。
CITATION_PATTERN = re.compile(
    r"(?:依据|证据|引用|来源|记录|指标)\s*[:：]?\s*\**([A-Za-z]{1,8}[-_]?\d{1,6})\**"
)

#: 只在整体没有授权证据时才拦截的「数据结论」措辞。
DATA_CLAIM_PATTERN = re.compile(r"数据显示|从数据看|指标显示|统计显示|数据表明|根据数据|依据数据")

_SENTENCE_END = re.compile(r"(?<=[。！？!?；;])")
_LIST_MARKER = re.compile(r"^\s*[（(]?(\d{1,2})[)）.、]\s*")
#: 「第2步」「第3页」这类编号不是事实断言。
_REFERENCE_PREFIX = "第"
_REFERENCE_SUFFIXES = ("页", "章", "节", "单元", "步", "号", "选项")
_APPROX_EPSILON = 1e-9

SegmentKind = Literal["fact", "explanation", "limitation"]


@dataclass(frozen=True)
class AnswerSegment:
    """草稿里的一段；保留数字与证据引用，便于审计。"""

    kind: SegmentKind
    text: str
    numeric_tokens: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    grounded: bool = True


@dataclass(frozen=True)
class AnswerBinding:
    """绑定结果：``text`` 是允许返回的文本，``draft`` 是结构化草稿。"""

    text: str
    draft: tuple[AnswerSegment, ...]
    limitations: tuple[str, ...]
    removed_numbers: tuple[str, ...]
    removed_evidence: tuple[str, ...]
    changed: bool
    status: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "changed": self.changed,
            "removed_numbers": list(self.removed_numbers),
            "removed_evidence": list(self.removed_evidence),
            "limitations": list(self.limitations),
            "segments": [segment.text for segment in self.draft],
        }


# --------------------------------------------------------------------------- #
# 本轮事实数字
# --------------------------------------------------------------------------- #


def _add_numeric_text(value: str, bucket: set[float]) -> None:
    for match in NUMBER_PATTERN.finditer(value):
        token, percent = match.group(1), match.group(2)
        try:
            number = float(token)
        except ValueError:  # pragma: no cover - 正则已排除
            continue
        bucket.add(number)
        if percent:
            bucket.add(number / 100)


def _collect_fact_numbers(value: Any, bucket: set[float]) -> None:
    """递归收集本轮工具结果里出现的全部数字（含字符串里的数字与百分数）。"""

    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, (int, float)):
        bucket.add(float(value))
        return
    if isinstance(value, str):
        _add_numeric_text(value, bucket)
        # 「4/5」形式的分数：分子分母单独登记，答案里出现 4 和 5 时都可核对。
        for match in re.finditer(r"(?<![0-9A-Za-z_])(\d+)\s*/\s*(\d+)(?![0-9A-Za-z_])", value):
            bucket.add(float(match.group(1)))
            bucket.add(float(match.group(2)))
        return
    if isinstance(value, dict):
        for item in value.values():
            _collect_fact_numbers(item, bucket)
        return
    if isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            _collect_fact_numbers(item, bucket)


def fact_numbers(tool_results: Iterable[Any] | None) -> frozenset[float]:
    """公开入口：本轮工具结果里的数字集合（测试和审计用）。"""

    bucket: set[float] = set()
    for result in tool_results or []:
        _collect_fact_numbers(result, bucket)
    return frozenset(bucket)


def _collect_evidence_ids(value: Any, bucket: set[str]) -> None:
    if isinstance(value, dict):
        if isinstance(value.get("id"), str) and value.get("kind"):
            bucket.add(value["id"])
        for item in value.values():
            _collect_evidence_ids(item, bucket)
        return
    if isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            _collect_evidence_ids(item, bucket)


def evidence_ids_from_results(tool_results: Iterable[Any] | None) -> frozenset[str]:
    """本轮工具结果里出现过的证据编号（含嵌套在 findings 里的引用）。"""

    bucket: set[str] = set()
    for result in tool_results or []:
        _collect_evidence_ids(result, bucket)
    return frozenset(bucket)


def _is_grounded(token: str, percent: str, facts: frozenset[float]) -> bool:
    try:
        number = float(token)
    except ValueError:  # pragma: no cover - 正则已排除
        return False
    candidates = {number}
    if percent:
        candidates.add(number / 100)
    return any(abs(candidate - known) <= _APPROX_EPSILON for candidate in candidates for known in facts)


# --------------------------------------------------------------------------- #
# 数字与证据抽取
# --------------------------------------------------------------------------- #


def _is_reference_number(
    *, token: str, percent: str, sentence: str, start: int, line: str, first_sentence: bool
) -> bool:
    """``第2步``、``第3页``、列表编号这类数字不是事实断言。"""

    if percent or "." in token:
        return False
    try:
        value = int(token)
    except ValueError:  # pragma: no cover
        return False
    if start > 0:
        left = start - 1
        while left >= 0 and sentence[left] == " ":
            left -= 1
        if left >= 0 and sentence[left] == _REFERENCE_PREFIX:
            return True
    end = start + len(token)
    while end < len(sentence) and sentence[end] == " ":
        end += 1
    rest = sentence[end : end + 2]
    if rest.startswith(_REFERENCE_SUFFIXES) and abs(value) <= 99:
        return True
    if first_sentence:
        marker = _LIST_MARKER.match(line)
        if marker is not None and marker.group(1) == token:
            return True
    return False


def _numeric_mentions(sentence: str) -> list[tuple[str, str, int]]:
    return [
        (match.group(1), match.group(2), match.start(1))
        for match in NUMBER_PATTERN.finditer(sentence)
    ]


def _cited_ids(sentence: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(match.group(1) for match in CITATION_PATTERN.finditer(sentence)))


def _unknown_citations(sentence: str, allowed: set[str]) -> tuple[str, ...]:
    if not allowed:
        return ()
    return tuple(item for item in _cited_ids(sentence) if item not in allowed)


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #


def bind_answer(
    *,
    text: str,
    tool_results: Iterable[Any] | None = None,
    evidence_refs: Iterable[dict[str, Any]] | None = None,
    allowed_evidence_ids: Iterable[str] | None = None,
) -> AnswerBinding:
    """把回答绑定到本轮事实；无法绑定的句子删除并转成限制说明。"""

    raw = text or ""
    facts = fact_numbers(tool_results)
    allowed = {str(item) for item in (allowed_evidence_ids or ()) if item}
    if not allowed:
        # 与 fact_check.check_answer 一致：没有授权集合时不做编号比对。
        allowed = {
            str(ref.get("id"))
            for ref in (evidence_refs or ())
            if isinstance(ref, dict) and ref.get("id")
        }

    kept: list[AnswerSegment] = []
    removed_numbers: list[str] = []
    removed_evidence: list[str] = []
    limitations: list[str] = []
    data_claim_without_evidence = False
    lines: list[str] = []

    for line in raw.split("\n"):
        sentences = [item for item in _SENTENCE_END.split(line) if item]
        if not sentences:
            lines.append("")
            continue
        kept_sentences: list[str] = []
        for index, sentence in enumerate(sentences):
            mentions = _numeric_mentions(sentence)
            unbindable = [
                token
                for token, percent, start in mentions
                if not _is_grounded(token, percent, facts)
                and not _is_reference_number(
                    token=token,
                    percent=percent,
                    sentence=sentence,
                    start=start,
                    line=line,
                    first_sentence=index == 0,
                )
            ]
            unknown = _unknown_citations(sentence, allowed)
            claim_without_evidence = (
                bool(DATA_CLAIM_PATTERN.search(sentence))
                and not _cited_ids(sentence)
                and not allowed
            )
            if unbindable or unknown or claim_without_evidence:
                removed_numbers.extend(unbindable)
                removed_evidence.extend(unknown)
                if claim_without_evidence:
                    data_claim_without_evidence = True
                continue
            kept_sentences.append(sentence)
            numeric_tokens = tuple(token for token, _percent, _start in mentions)
            kept.append(
                AnswerSegment(
                    kind="fact" if (numeric_tokens or _cited_ids(sentence)) else "explanation",
                    text=sentence,
                    numeric_tokens=numeric_tokens,
                    evidence_ids=_cited_ids(sentence),
                )
            )
        lines.append("".join(kept_sentences))

    body = "\n".join(lines).strip("\n")
    body = re.sub(r"\n{3,}", "\n\n", body).rstrip()

    if removed_numbers:
        unique_numbers = tuple(dict.fromkeys(removed_numbers))
        # 不在可见文本里重复这些数字：重复一次仍会把未核对数字留在最终回答里。
        limitations.append("回答里有一部分数字没有在本轮工具结果中找到出处，已删去")
    else:
        unique_numbers = ()
    if removed_evidence:
        unique_evidence = tuple(dict.fromkeys(removed_evidence))
        limitations.append("回答里有一些证据编号不在本轮授权结果中，相关句子已删去")
    else:
        unique_evidence = ()
    if data_claim_without_evidence:
        limitations.append("本轮没有可引用的正式数据，数据结论已删去")

    changed = bool(limitations)
    if changed:
        limitation_text = "限制：" + "；".join(limitations) + "。"
        body = f"{body}\n\n{limitation_text}".strip() if body else limitation_text
        kept.append(AnswerSegment(kind="limitation", text=limitation_text, grounded=False))
    return AnswerBinding(
        text=body,
        draft=tuple(kept),
        limitations=tuple(limitations),
        removed_numbers=tuple(unique_numbers),
        removed_evidence=tuple(unique_evidence),
        changed=changed,
        status="sanitized" if changed else "passed",
    )


__all__ = [
    "AnswerBinding",
    "AnswerSegment",
    "bind_answer",
    "evidence_ids_from_results",
    "fact_numbers",
]
