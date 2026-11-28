"""学生求助的答案保护薄适配器（05 §8、04 §6.1）。

正式链路应由 C 的 ``validate_tutoring_reply`` 用服务端重装的保护集合调用
``leak_check.check_exposure(candidate, protected_set)`` 并做独立语义复核。
当前仓库还没有 ``check_exposure``，Agent 侧也拿不到受保护的参考答案，因此本
模块只做两件事：

1. 有保护引用时，调用 C 已有的确定性提示泄答规则
   ``leak_check.check_hint(reference_answer, reference_explanation, hint_text)``；
2. 拿不到保护引用、或检查本身不可用时，不猜测、不验证，直接返回安全原理
   模板，明确告诉学生只能给思路。

限制：不做语义审查，不做数学判等，不替代 C 的独立复核；保护集合的装载与
累计披露仍必须由 C 服务端完成。接口只在调用方显式传入保护引用时才会判定
「已核对未泄答」。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from study_companion.recommendation.leak_check import check_hint

#: 「怎么做 / 答案 / 提示」类求助；命中后必须先过答案保护再发送。
HELP_PATTERNS: tuple[str, ...] = (
    "怎么做", "怎么算", "怎么写", "怎么求", "怎么列", "如何做", "如何解", "如何算",
    "帮我做", "帮我算", "替我算", "直接告诉", "告诉我结果", "告诉我答案",
    "答案", "结果是多少", "等于几", "得多少", "算出来", "求出来",
    "提示", "给点思路", "解题思路", "下一步", "第一步", "从哪开始", "怎么想",
    "卡住", "不会做", "不会写", "做不出", "没思路",
)

#: 无法核对时的安全原理提示；不含数字、不展开完整步骤。
SAFE_PRINCIPLE_REPLY = (
    "这道题我先不直接给结果，也不展开完整步骤。"
    "你可以先读一遍题目，圈出已知条件，再想它要你求什么；"
    "把第一步写下来，我帮你看看方向对不对。你卡在哪一步？"
)

VERIFIED_LIMITATION = "已按 C 现有提示泄答规则核对参考答案，未发现直接泄答。"
UNVERIFIED_LIMITATION = "本轮没有可核对的标准答案，只能给思路提示；具体结果请自己算完再核对。"
CHECK_FAILED_LIMITATION = "答案保护检查不可用，已退回安全原理提示。"


@dataclass(frozen=True)
class TutoringDecision:
    """``action``：allow（原样放行）/ replaced（换成安全原理提示）。"""

    text: str
    action: str
    reason: str
    verified: bool
    leaked_against: str | None = None
    limitations: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        return self.action == "replaced"

    def as_dict(self) -> dict[str, object]:
        return {
            "action": self.action,
            "reason": self.reason,
            "verified": self.verified,
            "leaked_against": self.leaked_against,
            "limitations": list(self.limitations),
        }


def is_tutoring_request(text: str | None) -> bool:
    """是否为需要答案保护的学生求助；纯知识、计划和学情查询不算。"""

    if not text:
        return False
    normalized = "".join(text.casefold().split())
    return any(pattern in normalized for pattern in HELP_PATTERNS)


def _replaced(reason: str, *, verified: bool, limitation: str, leaked_against: str | None = None) -> TutoringDecision:
    return TutoringDecision(
        text=SAFE_PRINCIPLE_REPLY,
        action="replaced",
        reason=reason,
        verified=verified,
        leaked_against=leaked_against,
        limitations=(limitation,),
    )


def guard_tutoring_reply(
    *,
    text: str,
    request_text: str | None = None,
    protected_answers: Sequence[str] = (),
    protected_explanations: Sequence[str] = (),
    can_show_solution: bool | None = None,
) -> TutoringDecision:
    """在发送前检查学生求助回复；无法验证时返回安全原理提示。

    ``can_show_solution=True`` 表示 C 已确认当前活动允许展示完整解析；只有
    调用方显式传入时才放行。``request_text`` 不作为解题依据，只用于判断是否
    属于求助；``None`` 表示调用方已确认这是求助。
    """

    raw = text or ""
    if can_show_solution is True:
        return TutoringDecision(
            text=raw,
            action="allow",
            reason="当前活动已允许展示解析",
            verified=True,
            limitations=(),
        )
    if request_text is not None and not is_tutoring_request(request_text):
        return TutoringDecision(
            text=raw,
            action="allow",
            reason="非求助请求，不走答案保护",
            verified=False,
            limitations=(),
        )

    answers = [item for item in protected_answers if item]
    explanations = [item for item in protected_explanations if item]
    if not answers and not explanations:
        return _replaced(
            "没有可核对的标准答案或解析",
            verified=False,
            limitation=UNVERIFIED_LIMITATION,
        )

    try:
        for label, references in (("参考答案", answers), ("参考解析", explanations)):
            for reference in references:
                leak = check_hint(reference, None, raw)
                if leak:
                    return _replaced(
                        f"C 提示泄答规则判定：{leak}",
                        verified=True,
                        limitation=VERIFIED_LIMITATION,
                        leaked_against=label,
                    )
    except Exception:  # 保护检查不可用时只能降级，不能放行未验证内容
        return _replaced(
            "答案保护检查不可用",
            verified=False,
            limitation=CHECK_FAILED_LIMITATION,
        )

    return TutoringDecision(
        text=raw,
        action="allow",
        reason="C 提示泄答规则未发现泄答",
        verified=True,
        limitations=(VERIFIED_LIMITATION,),
    )


__all__ = [
    "HELP_PATTERNS",
    "SAFE_PRINCIPLE_REPLY",
    "TutoringDecision",
    "guard_tutoring_reply",
    "is_tutoring_request",
]
