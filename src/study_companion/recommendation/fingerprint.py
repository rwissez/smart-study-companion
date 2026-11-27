"""题目指纹与近变式判定（04 §4.1）。

作用只有一个：**减少同组里的近似重复题**。它不判断教学价值，也不改写题库。

相似度 S = .45×形状相似 + .25×方法标签 Jaccard + .20×知识点 Jaccard + .10×步骤顺序相似。
S ≥ .80 视为近变式；同一任务组默认最多容纳两题同簇。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from study_companion.contracts import Material

NEAR_VARIANT_THRESHOLD = 0.80
MAX_PER_CLUSTER = 2

DIGIT = re.compile(r"\d+(?:\.\d+)?")

METHOD_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("substitute", ("代入", "替换")),
    ("combine", ("合并同类项", "合并")),
    ("distribute", ("去括号", "分配律", "乘进", "逐项乘")),
    ("balance", ("移项", "两边同", "同除以", "同乘", "等式性质")),
    ("eliminate", ("消元", "加减消元", "代入消元")),
    ("reverse_inequality", ("不等号方向", "变号", "翻转")),
    ("interval_intersect", ("交集", "公共部分", "数轴", "解集")),
    ("model", ("设未知数", "列方程", "列式", "建模")),
)

OPERATOR_TOKENS = {
    "+": "add",
    "-": "sub",
    "*": "mul",
    "×": "mul",
    "/": "div",
    "÷": "div",
    "^": "pow",
    "=": "eq",
    "<": "lt",
    ">": "gt",
    "≤": "le",
    "≥": "ge",
}


@dataclass(frozen=True)
class Fingerprint:
    material_id: str
    content_hash: str
    method_tags: tuple[str, ...]
    primary_kp_ids: tuple[str, ...]
    normalized_shape: str
    step_signature: tuple[str, ...]
    string_shape: str

    def as_dict(self) -> dict[str, object]:
        return {
            "material_id": self.material_id,
            "content_hash": self.content_hash,
            "method_tags": list(self.method_tags),
            "primary_kp_ids": list(self.primary_kp_ids),
            "normalized_shape": self.normalized_shape,
            "step_signature": list(self.step_signature),
        }


def mask_digits(text: str) -> str:
    """数字遮罩：把数字换成 #，保留运算符与文字结构，用于发现"只改数字"的克隆。"""

    return DIGIT.sub("#", text or "")


def normalize_shape(text: str) -> str:
    masked = mask_digits(text or "")
    return re.sub(r"\s+", "", masked).lower()


def detect_methods(text: str) -> tuple[str, ...]:
    found = [name for name, keywords in METHOD_KEYWORDS if any(word in (text or "") for word in keywords)]
    return tuple(sorted(set(found)))


def step_signature(text: str) -> tuple[str, ...]:
    tokens: list[str] = []
    for char in text or "":
        token = OPERATOR_TOKENS.get(char)
        if token and (not tokens or tokens[-1] != token):
            tokens.append(token)
    return tuple(tokens)


def build_fingerprint(
    material: Material, *, primary_kp_ids: list[str] | None = None
) -> Fingerprint:
    text = material.content or ""
    kp_ids = tuple(sorted(primary_kp_ids or []))
    return Fingerprint(
        material_id=material.material_id,
        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        method_tags=detect_methods(text),
        primary_kp_ids=kp_ids,
        normalized_shape=normalize_shape(text),
        step_signature=step_signature(text),
        string_shape=text,
    )


def _jaccard(left: tuple[str, ...] | set[str], right: tuple[str, ...] | set[str]) -> float:
    left_set, right_set = set(left), set(right)
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / len(left_set | right_set)


def _ordered_similarity(left: tuple[str, ...], right: tuple[str, ...]) -> float:
    if not left or not right:
        return 0.0
    matcher = SequenceMatcher(a=left, b=right, autojunk=False)
    return matcher.ratio()


def similarity(left: Fingerprint, right: Fingerprint) -> float:
    """04 §4.1 的 S 分数（0—1，六位小数比较）。"""

    shape: float
    if left.normalized_shape and left.normalized_shape == right.normalized_shape:
        shape = 1.0
    else:
        shape = SequenceMatcher(a=left.normalized_shape, b=right.normalized_shape, autojunk=False).ratio()
    score = (
        0.45 * shape
        + 0.25 * _jaccard(left.method_tags, right.method_tags)
        + 0.20 * _jaccard(left.primary_kp_ids, right.primary_kp_ids)
        + 0.10 * _ordered_similarity(left.step_signature, right.step_signature)
    )
    return round(score, 6)


def is_near_variant(left: Fingerprint, right: Fingerprint) -> bool:
    """近变式判定。

    结构完全相同（数字遮罩后形状一致且运算符序列一致）直接判同簇：只改数字、
    换结论数字的克隆必须被挡住，不能因为方法标签缺失就漏过。其余按 S ≥ .80。
    """

    if left.content_hash == right.content_hash:
        return True
    if left.normalized_shape and left.normalized_shape == right.normalized_shape:
        if left.step_signature == right.step_signature:
            return True
    return similarity(left, right) >= NEAR_VARIANT_THRESHOLD


def cluster_of(
    candidate: Fingerprint, selected: list[Fingerprint]
) -> int | None:
    """返回命中的已有簇序号（列表下标），未命中返回 None。"""

    for index, existing in enumerate(selected):
        if is_near_variant(candidate, existing):
            return index
    return None


def cluster_counts(selected: list[Fingerprint]) -> list[int]:
    """把已选题目按近变式聚成簇，返回每簇题量（用于"同簇 ≤2"约束）。"""

    clusters: list[list[Fingerprint]] = []
    for item in selected:
        placed = False
        for cluster in clusters:
            if any(is_near_variant(item, member) for member in cluster):
                cluster.append(item)
                placed = True
                break
        if not placed:
            clusters.append([item])
    return [len(cluster) for cluster in clusters]
