"""回答事实校验（05 §10）。

程序侧只核对三类可验证事实，不做语义判断：

1. 引用的证据编号是否真的来自本轮工具结果；
2. 回答里出现的数字是否能在这轮事实里找到；
3. 声称"根据/依据"时是否至少给了一条证据。

数字对不上只记警告，不阻断回答；证据编号凭空出现记错误。
"""

from __future__ import annotations

import re
from typing import Any

NUMBER_PATTERN = re.compile(r"-?\d+(?:\.\d+)?%?")
CLAIM_PATTERN = re.compile(r"根据|依据|从数据看|数据显示")


def _flatten_numbers(value: Any, bucket: set[str]) -> None:
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, (int, float)):
        bucket.add(str(value).rstrip("0").rstrip(".") if isinstance(value, float) else str(value))
        return
    if isinstance(value, str):
        bucket.update(NUMBER_PATTERN.findall(value))
        return
    if isinstance(value, dict):
        for item in value.values():
            _flatten_numbers(item, bucket)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _flatten_numbers(item, bucket)


def check_answer(
    *,
    text: str,
    evidence_refs: list[dict[str, Any]],
    allowed_evidence_ids: set[str],
    tool_results: list[Any] | None = None,
) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    cited = {
        str(ref.get("id") or "")
        for ref in evidence_refs
        if isinstance(ref, dict) and ref.get("id")
    }
    unknown = sorted(cited - allowed_evidence_ids) if allowed_evidence_ids else []
    for item in unknown:
        issues.append(
            {
                "code": "EVIDENCE_UNKNOWN",
                "message": f"引用 {item} 不在本轮工具结果中",
            }
        )

    numbers_in_text = set(NUMBER_PATTERN.findall(text or ""))
    facts: set[str] = set()
    for result in tool_results or []:
        _flatten_numbers(result, facts)
    unmatched = sorted(numbers_in_text - facts) if facts else []
    if unmatched:
        issues.append(
            {
                "code": "NUMBER_UNVERIFIED",
                "message": "回答中的数字未在本轮事实中找到：" + "、".join(unmatched[:5]),
            }
        )
    if CLAIM_PATTERN.search(text or "") and not cited:
        issues.append(
            {
                "code": "NO_EVIDENCE_CITED",
                "message": "回答使用了数据结论但没有给出证据编号",
            }
        )

    errors = [item for item in issues if item["code"] == "EVIDENCE_UNKNOWN"]
    return {
        "status": "failed" if errors else ("warning" if issues else "passed"),
        "issues": issues,
        "checked_evidence": sorted(cited),
        "checked_numbers": sorted(numbers_in_text),
        "limitations": ["只核对证据编号与数字来源，不判断解释是否正确"],
    }
