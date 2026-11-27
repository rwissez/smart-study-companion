"""三级提示的规则模板（04 §6.1）。

设计只允许三个层级：**知识回忆 → 方法方向 → 关键未完成步骤**，并且三层加起来
仍不得重建答案。这里用确定性模板生成，再用 ``leak_check`` 逐条自检；一旦命中
泄答风险，就降到更保守的安全文本并记录限制，绝不把有风险的提示发给学生。
"""

from __future__ import annotations

from study_companion.contracts import Material
from study_companion.recommendation.leak_check import check_hint

GENERIC_LEVELS = (
    "先回忆这道题用到的知识点，把题目里的条件逐条读清楚。",
    "想一想第一步该做什么：把已知条件和要问的量分开写出来。",
    "把已经写出的那一步再核对一遍，确认它朝目标前进了一小步。",
)

STRATEGY_FOCUS = {
    "collect_evidence": "先按自己的思路写一步，不用急着求完",
    "prerequisite_support": "先回到更基础的那一步，把基础规则用熟",
    "error_correction": "重点检查上一次出错的那一步，尤其是符号与运算顺序",
    "consolidation": "把同一类题的结构看清楚，再换一种问法试一次",
    "transfer": "先找出这道题和之前练过的题哪里不一样",
    "teacher_intervention": "先写出你卡住的具体位置，教师会据此安排",
}


def build_hints(
    material: Material,
    *,
    kp_name: str,
    strategy: str,
    limitations: list[str] | None = None,
) -> list[str]:
    """生成三级提示，并逐条做泄答自检。"""

    focus = STRATEGY_FOCUS.get(strategy, STRATEGY_FOCUS["consolidation"])
    knowledge = f"这道题考查「{kp_name}」，先回忆它的定义和适用条件。"
    approach = f"{focus}。先明确要处理的对象，再决定用哪一步运算。"
    step = "把过程拆成三步：写出已知、写出要用的关系、再逐步化简，每一步都回头核对一次。"
    candidates = [knowledge, approach, step]

    safe: list[str] = []
    for level, hint in enumerate(candidates, start=1):
        leak = check_hint(material.reference_answer, material.reference_explanation, hint)
        if leak:
            if limitations is not None:
                limitations.append(f"第 {level} 级提示模板命中泄答检查，已替换为安全文本：{leak}")
            safe.append(GENERIC_LEVELS[level - 1])
        else:
            safe.append(hint)
    return safe


def hints_are_safe(material: Material, hints: list[str]) -> list[str]:
    """返回有泄答风险的层级说明（空列表表示全部通过）。"""

    problems: list[str] = []
    for level, hint in enumerate(hints, start=1):
        leak = check_hint(material.reference_answer, material.reference_explanation, hint)
        if leak:
            problems.append(f"第 {level} 级：{leak}")
    return problems
