"""C 的题目难度评估（04 §4.1）。

难度是**有序的工程判断**，不是教学真相：

* 先用确定性特征抽取（步骤数、主知识点数、是否建模、是否表示切换、是否分类讨论），
  再按 04 §4.1 的复杂度公式分档；
* 特征抽取不到（题干被截断、公式无法解析）时不猜，返回 ``None`` 并让候选留在
  "难度未知"，由 C 的准入规则决定能不能用；
* 群体作答只作为复核触发，不直接改写数学难度等级。

公式与阈值来自 04（属该文档标注的 E 类启发式），模块常量集中在此，便于校准与追溯。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from study_companion.contracts import DifficultyAssessment, Material

RULE_VERSION = "c-difficulty-rules-1"

#: 04 §4.1 复杂度公式的档位边界（0—2 基本，3—5 中等，6 以上进阶）。
BASIC_MAX = 2
INTERMEDIATE_MAX = 5

MODELING_MARKERS = ("应用", "实际", "情景", "情境", "方案", "购买", "行程", "工程问题")
REPRESENTATION_MARKERS = ("数轴", "图像", "图象", "表格", "坐标", "示意图")
CASE_SPLIT_MARKERS = ("分类", "两种情况", "分情况", "讨论", "取值范围")

OPERATOR_PATTERN = re.compile(r"[+\-*/^×÷]|(?<![<>=])=(?!=)|≤|≥|<|>")
RELATION_PATTERN = re.compile(r"[<>=≤≥]")


@dataclass(frozen=True)
class DifficultyFeatures:
    """04 §4.1 的 DifficultyProfile.features（严格闭合，值域 1..8）。"""

    steps: int
    primary_kp_count: int
    has_modeling: bool
    representation_switch: bool
    case_split: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "steps": self.steps,
            "primary_kp_count": self.primary_kp_count,
            "has_modeling": self.has_modeling,
            "representation_switch": self.representation_switch,
            "case_split": self.case_split,
        }


def _clamp(value: int, low: int = 1, high: int = 8) -> int:
    return max(low, min(high, value))


def extract_features(
    material: Material, *, primary_kp_count: int | None = None
) -> DifficultyFeatures | None:
    """从题干与评分点抽取结构特征；无法解析时返回 None。"""

    content = (material.content or "").strip()
    if not content:
        return None
    # 步骤数：数学关键操作数（等号、运算符、评分点各自算一次），跨步重复书写只计一次。
    operators = len(OPERATOR_PATTERN.findall(content))
    equations = len(RELATION_PATTERN.findall(content))
    rubric_points = len(material.rubric_points)
    raw_steps = max(operators, equations) + rubric_points
    if material.question_type == "solution" and raw_steps < 2:
        raw_steps = 2
    steps = _clamp(max(1, raw_steps))

    kp_count = primary_kp_count if primary_kp_count is not None else max(1, len(material.indicator_ids))
    kp_count = _clamp(kp_count)

    return DifficultyFeatures(
        steps=steps,
        primary_kp_count=kp_count,
        has_modeling=any(marker in content for marker in MODELING_MARKERS),
        representation_switch=any(marker in content for marker in REPRESENTATION_MARKERS),
        case_split=any(marker in content for marker in CASE_SPLIT_MARKERS),
    )


def complexity(features: DifficultyFeatures) -> int:
    """04 §4.1 公式：min(4,steps-1)+min(2,primary_kp_count-1)+2*建模+表示切换+分类讨论。"""

    return (
        min(4, features.steps - 1)
        + min(2, features.primary_kp_count - 1)
        + 2 * int(features.has_modeling)
        + int(features.representation_switch)
        + int(features.case_split)
    )


def level_for(score: int) -> str:
    if score <= BASIC_MAX:
        return "basic"
    if score <= INTERMEDIATE_MAX:
        return "intermediate"
    return "advanced"


def assess(
    material: Material, *, primary_kp_count: int | None = None
) -> DifficultyAssessment | None:
    """规则难度评估。特征不足时不产出结论，返回 None（C 不得当作中等）。"""

    features = extract_features(material, primary_kp_count=primary_kp_count)
    if features is None:
        return None
    score = complexity(features)
    level = level_for(score)
    return DifficultyAssessment(
        level=level,  # type: ignore[arg-type]
        method="rule",
        status="verified",
        reason=(
            f"复杂度 {score}（步骤 {features.steps}、主知识点 {features.primary_kp_count}、"
            f"建模 {int(features.has_modeling)}、表示切换 {int(features.representation_switch)}、"
            f"分类讨论 {int(features.case_split)}）→ {level}"
        ),
        evidence_refs=[],
        rule_version=RULE_VERSION,
    )


def needs_recheck(
    assessment: DifficultyAssessment | None,
    *,
    valid_sample: int,
    correct_rate: float | None,
) -> bool:
    """群体作答复核触发（04 §4.1）：样本足够且正确率异常时才提示复核，不改等级。"""

    if assessment is None or correct_rate is None:
        return False
    if valid_sample < 20:
        return False
    if assessment.level == "basic" and correct_rate < 0.30:
        return True
    if assessment.level == "advanced" and correct_rate > 0.85:
        return True
    return False


def estimated_minutes(assessment: DifficultyAssessment | None, *, solution: bool) -> int:
    """04 §4.3：基本/中等/进阶 = 2/4/6 分钟，未知取 6；解答题需完整书写 +2。"""

    base = {"basic": 2, "intermediate": 4, "advanced": 6}.get(
        assessment.level if assessment else "", 6
    )
    if solution:
        return min(12, base + 2)
    return base
