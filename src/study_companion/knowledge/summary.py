"""A 质量报告的口径计算（02 §4—§12）。

这里只做三件事，保证 ``review_summary`` 里的比率可核对：

* 题目、学习卡（讲义）、教材三类材料分开统计，分子分母都只取本类材料；
* 不修补超过 100% 的比率：分子大于分母说明口径不一致，直接返回 ``null`` 并写原因；
* 来源能否解析只看已核验且带页码的定位；未核验、缺页码、没有定位都如实返回 ``false``。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from study_companion.contracts import ReviewCheck, ReviewInfo, SourceLocator

#: 材料类别与展示名的唯一对照；新增类别时三处统计一起生效。
MATERIAL_POPULATIONS: dict[str, str] = {
    "question": "题目",
    "card": "学习卡（讲义）",
    "textbook": "教材",
}

SourceState = Literal["verified", "unverified", "missing"]


@dataclass(frozen=True)
class SourceResolution:
    """单条来源的解析结论；``reason`` 直接写进 ``ReviewCheck.reason``。"""

    state: SourceState
    passed: bool
    reason: str


@dataclass(frozen=True)
class MaterialMapping:
    """按材料类别拆开的关联统计，分子分母由同一批目录材料得出。"""

    totals: dict[str, int]
    mapped: dict[str, int]
    mapped_material_ids: set[str]
    unclassified_material_ids: list[str]


def _page_anchor(locator: SourceLocator) -> str:
    """返回可复核的页码定位；没有页码时为空串。"""

    if locator.pdf_page is not None and locator.pdf_page >= 1:
        return f"PDF 第 {locator.pdf_page} 页"
    printed = (locator.printed_page or "").strip()
    if printed:
        return f"印刷页 {printed}"
    return ""


def source_resolvability(locators: list[SourceLocator]) -> SourceResolution:
    """判断来源是否真的可定位。

    只有 ``verification_status=verified`` 且带 PDF 页码或印刷页码的定位才算通过；
    未核验、已核验但无页码、完全没有定位，都返回 ``passed=False`` 并说明原因。
    """

    if not locators:
        return SourceResolution("missing", False, "没有登记任何来源定位，无法解析出处")
    verified = [item for item in locators if item.verification_status == "verified"]
    if not verified:
        has_page = any(_page_anchor(item) for item in locators)
        if has_page:
            reason = "来源有页码但未核验（verification_status=unverified），不能当作已解析出处"
        else:
            reason = (
                "来源只有登记信息、未核验，也没有页码"
                "（verification_status=unverified），不能当作已解析出处"
            )
        return SourceResolution("unverified", False, reason)
    anchored = [(item, _page_anchor(item)) for item in verified]
    anchored = [(item, anchor) for item, anchor in anchored if anchor]
    if not anchored:
        return SourceResolution(
            "unverified",
            False,
            "来源标记为 verified 但缺少 PDF 页码或印刷页码，无法定位到页",
        )
    locator, anchor = anchored[0]
    return SourceResolution(
        "verified", True, f"来源已核验并可定位：{locator.source_file} {anchor}"
    )


def point_source_states(points: list[Any]) -> dict[str, list[str]]:
    """按来源状态给知识点分组；``state`` 与 :class:`SourceResolution` 一致。"""

    states: dict[str, list[str]] = {"verified": [], "unverified": [], "missing": []}
    for point in points:
        states[source_resolvability(list(point.source_locators)).state].append(point.kp_id)
    return states


def material_mapping(
    materials: list[Any],
    annotations: list[Any],
    point_ids: set[str],
) -> MaterialMapping:
    """把材料按目录类别计数，只把指向现存知识点的关联算作已关联。

    关联材料不在目录内时无法归类，单独记入 ``unclassified_material_ids``；
    调用方应把它写成 ``unknown_count``，不能硬算进任何一个分子。
    """

    totals = {kind: 0 for kind in MATERIAL_POPULATIONS}
    kinds: dict[str, str] = {}
    for material in materials:
        kind = material.material_type
        if kind in totals:
            totals[kind] += 1
            kinds[material.material_id] = kind

    mapped_ids: set[str] = set()
    for annotation in annotations:
        targets = set(annotation.target_ids or ())
        if targets & point_ids:
            mapped_ids.add(annotation.source_id)

    mapped = {kind: 0 for kind in totals}
    unclassified: list[str] = []
    for material_id in mapped_ids:
        kind = kinds.get(material_id)
        if kind is None:
            unclassified.append(material_id)
            continue
        mapped[kind] += 1
    return MaterialMapping(
        totals=totals,
        mapped=mapped,
        mapped_material_ids=mapped_ids,
        unclassified_material_ids=sorted(unclassified),
    )


def coverage_item(
    *,
    key: str,
    label: str,
    population: str,
    numerator: int,
    denominator: int,
    numerator_label: str,
    denominator_label: str,
    scope_description: str,
    partial_count: int = 0,
    not_applicable_count: int = 0,
    unknown_count: int = 0,
    reason_notes: list[str] | None = None,
) -> dict[str, Any]:
    """生成一条覆盖率记录；字段名直接写明分子、分母的统计对象。

    分母为 0 或分子大于分母时不产出比率，``value=null``、``status="unknown"``，
    原因写入 ``reason``，不在展示层补一个假百分比。
    """

    notes = [note for note in (reason_notes or []) if note]
    if denominator <= 0:
        value: float | None = None
        status = "unknown"
        reason = "；".join([f"{denominator_label}为 0，无法计算比率", *notes])
    elif numerator > denominator:
        value = None
        status = "unknown"
        reason = "；".join(
            [
                f"分子（{numerator}）大于分母（{denominator}），分子分母不是同一口径，已拒绝给出比率",
                *notes,
            ]
        )
    else:
        value = numerator / denominator
        status = "computed"
        reason = "；".join(notes)
    return {
        "key": key,
        "label": label,
        "population": population,
        "numerator": numerator,
        "denominator": denominator,
        "value": value,
        "status": status,
        "reason": reason,
        "numerator_label": numerator_label,
        "denominator_label": denominator_label,
        "scope_description": scope_description,
        "partial_count": partial_count,
        "not_applicable_count": not_applicable_count,
        "unknown_count": unknown_count,
        "detail_refs": [],
    }


def review_with_source_check(
    review_info: ReviewInfo | None, resolution: SourceResolution
) -> ReviewInfo | None:
    """用最新的来源结论重算 ``SOURCE_RESOLVABLE``，其余检查原样保留。"""

    if review_info is None:
        return None
    checks = [
        ReviewCheck(code="SOURCE_RESOLVABLE", passed=resolution.passed, reason=resolution.reason)
    ]
    checks.extend(check for check in review_info.checks if check.code != "SOURCE_RESOLVABLE")
    return review_info.model_copy(
        update={
            "checks": checks,
            "status": "validated" if all(check.passed for check in checks) else "needs_review",
        }
    )
