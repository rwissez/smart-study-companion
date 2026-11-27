"""回答事实校验规则（05 §10）。"""

from __future__ import annotations

from study_companion.agent.fact_check import check_answer


def test_answer_with_unknown_evidence_is_failed() -> None:
    result = check_answer(
        text="这个问题主要错在去括号。",
        evidence_refs=[{"kind": "record", "id": "R99"}],
        allowed_evidence_ids={"R01", "R02"},
    )
    assert result["status"] == "failed"
    assert any(item["code"] == "EVIDENCE_UNKNOWN" for item in result["issues"])


def test_numeric_claim_outside_facts_is_warning() -> None:
    result = check_answer(
        text="得分率是 0.83，比上次高一点。",
        evidence_refs=[{"kind": "metric", "id": "m1"}],
        allowed_evidence_ids={"m1"},
        tool_results=[{"value": 0.8, "numerator": 4, "denominator": 5}],
    )
    assert result["status"] == "warning"
    assert any(item["code"] == "NUMBER_UNVERIFIED" for item in result["issues"])


def test_matched_numbers_pass() -> None:
    result = check_answer(
        text="得分率是 0.8，4/5 道题做对。",
        evidence_refs=[{"kind": "metric", "id": "m1"}],
        allowed_evidence_ids={"m1"},
        tool_results=[{"value": 0.8, "numerator": 4, "denominator": 5}],
    )
    assert result["status"] == "passed"
    assert result["issues"] == []


def test_claim_without_citation_is_warning() -> None:
    result = check_answer(
        text="数据显示这个班整体掌握较好。",
        evidence_refs=[],
        allowed_evidence_ids=set(),
        tool_results=[{"value": 0.9}],
    )
    assert result["status"] == "warning"
    assert any(item["code"] == "NO_EVIDENCE_CITED" for item in result["issues"])


def test_no_evidence_baseline_does_not_flag_numbers() -> None:
    result = check_answer(
        text="先练去括号，再做两道整式题。",
        evidence_refs=[],
        allowed_evidence_ids=set(),
        tool_results=[],
    )
    assert result["status"] == "passed"
