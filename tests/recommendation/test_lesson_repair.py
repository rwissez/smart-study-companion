"""C 微课堂：一次最小修复与八项逐项复核（04 §5）。

覆盖四件事：

1. 独立复核返回 ``repair`` 时，按 issues 做一次最小修复，修复后规则硬检与复审都通过；
2. 修复失败、修复越界或组件 schema 被改动时退回规则模板，且只使用一次修复额度；
3. 八项复核必须与程序下发的 check_id/object_key 一一对应，缺项、重复、换对象或
   伪造依据都判失败；
4. 原有六项 check_id 保持兼容，``review_info_for`` 也投影同一套八项。
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from pydantic import ValidationError

from study_companion.contracts import EvidenceRef
from study_companion.recommendation.lesson import build_rule_lesson, review_info_for, validate_lesson
from study_companion.recommendation.lesson_model import (
    REQUIRED_REVIEW_CHECKS,
    LessonModelOut,
    LessonRepairOut,
    LessonReplacementOut,
    LessonReviewCheckOut,
    LessonReviewIssueOut,
    LessonReviewOut,
    generate_with_model,
)

OBJECT_KEY = "L-1"
ORIGINAL_SIX = (
    "lesson_math",
    "lesson_steps",
    "lesson_personalization",
    "lesson_sources",
    "lesson_schema",
    "lesson_answer_guard",
)


@dataclass
class Result:
    succeeded: bool
    value: object = None
    error: dict | None = None


class ScriptedService:
    """按 task_key 取预置结果；记录请求顺序，便于断言调用次数与输入负载。"""

    def __init__(self, *, write=None, reviews=(), repairs=()) -> None:
        self.write_queue = list(write if isinstance(write, list) else [write])
        self.review_queue = list(reviews)
        self.repair_queue = list(repairs)
        self.requests: list[object] = []

    def _resolve(self, item):  # noqa: ANN001
        if isinstance(item, Exception):
            raise item
        if isinstance(item, Result):
            return item
        if item is None:
            return Result(False, None, {"code": "MODEL_TIMEOUT"})
        return Result(True, item)

    def generate_structured(self, request, schema):  # noqa: ANN001
        self.requests.append(request)
        key = request.task_key
        if key.endswith("write_content.v1"):
            return self._resolve(self.write_queue.pop(0))
        if key.endswith("review_lesson.v1"):
            return self._resolve(self.review_queue.pop(0))
        if key.endswith("repair_lesson.v1"):
            return self._resolve(self.repair_queue.pop(0))
        raise AssertionError(f"unexpected task {key}")

    @property
    def task_keys(self) -> list[str]:
        return [request.task_key for request in self.requests]

    def payload(self, task_suffix: str) -> dict:
        request = next(item for item in self.requests if item.task_key.endswith(task_suffix))
        return dict(request.input_payload)


def _refs() -> list[EvidenceRef]:
    return [EvidenceRef(kind="knowledge_point", id="kp-1", knowledge_version="kn-1")]


def _write_draft(*, correct: str = "-2*x-7") -> LessonModelOut:
    return LessonModelOut.model_validate(
        {
            "title": "去括号时的符号",
            "goal": "能把括号前的负号分配到每一项",
            "estimated_minutes": 4,
            "components": [
                {
                    "id": "t",
                    "type": "text",
                    "heading": "先看符号",
                    "paragraphs": ["上次你去括号时漏乘了括号里的第二项，这次只练这一步。"],
                },
                {
                    "id": "c",
                    "type": "contrast",
                    "before": "-(2*x-7)",
                    "incorrect": "-2*x-7",
                    "correct": correct,
                    "note": "负号要乘到每一项",
                },
            ],
            "fallback_text": "负号要乘到括号里的每一项。",
        }
    )


def _checks(
    *,
    result: str = "pass",
    fail: tuple[str, ...] = (),
    object_key: str = OBJECT_KEY,
    extra: list[LessonReviewCheckOut] | None = None,
) -> list[LessonReviewCheckOut]:
    checks = [
        LessonReviewCheckOut(
            check_id=check_id,
            object_key=object_key,
            result="fail" if check_id in fail else result,
            reason="变换不等价" if check_id in fail else "",
        )
        for check_id in REQUIRED_REVIEW_CHECKS
    ]
    if extra:
        checks.extend(extra)
    return checks


def _pass_review() -> LessonReviewOut:
    return LessonReviewOut(decision="pass", checks=_checks())


def _repair_review() -> LessonReviewOut:
    return LessonReviewOut(
        decision="repair",
        checks=_checks(fail=("lesson_steps",)),
        issues=[
            LessonReviewIssueOut(
                code="STEP_NOT_EQUIVALENT",
                object_key="c",
                field="component",
                expected="正误对照的 correct 应与 before 等价",
            )
        ],
    )


def _repaired_contrast() -> LessonRepairOut:
    return LessonRepairOut(
        status="ok",
        replacements=[
            LessonReplacementOut(
                object_key="c",
                field="component",
                value={
                    "id": "c",
                    "type": "contrast",
                    "before": "-(2*x-7)",
                    "incorrect": "-2*x-7",
                    "correct": "-2*x+7",
                    "note": "负号要乘到每一项",
                },
            )
        ],
    )


ERROR_POINTS = [
    "负号分配到括号内每一项的知识点定义",
    "上次去括号时漏乘了第二项，符号也写错了",
]


def _generate(service: ScriptedService, **overrides):  # noqa: ANN001
    kwargs = {
        "service": service,
        "title": "去括号的符号",
        "goal": "会变号",
        "kp_id": "kp-1",
        "evidence_refs": _refs(),
        "teaching_points": ERROR_POINTS,
        "protected_texts": [],
        "object_key": OBJECT_KEY,
    }
    kwargs.update(overrides)
    return generate_with_model(**kwargs)  # type: ignore[arg-type]


def _contrast(draft) -> object:  # noqa: ANN001
    return next(component for component in draft.components if component.id == "c")


# --------------------------------------------------------------------------- #
# repair 成功与失败
# --------------------------------------------------------------------------- #


def test_repair_fixes_draft_and_passes_full_recheck() -> None:
    service = ScriptedService(
        write=_write_draft(correct="-2*x-7"),
        reviews=[_repair_review(), _pass_review()],
        repairs=[_repaired_contrast()],
    )
    draft, limitations = _generate(service)
    assert draft is not None
    assert _contrast(draft).correct == "-2*x+7"
    assert service.task_keys == [
        "c.write_content.v1",
        "c.review_lesson.v1",
        "c.repair_lesson.v1",
        "c.review_lesson.v1",
    ]
    assert any("最小修复" in item for item in limitations)
    assert draft.kp_ids == ["kp-1"]
    assert draft.evidence_refs == _refs()
    # 程序下发的八项检查必须逐项带对象键，第二次复审基于修复后的草稿。
    required = service.payload("review_lesson.v1")["required_checks"]
    assert len(required) == len(REQUIRED_REVIEW_CHECKS)
    assert {item["check_id"] for item in required} == set(REQUIRED_REVIEW_CHECKS)
    assert all(item["object_key"] == OBJECT_KEY for item in required)
    second_review_payloads = [
        request.input_payload
        for request in service.requests
        if request.task_key.endswith("review_lesson.v1")
    ]
    assert second_review_payloads[-1]["draft"]["components"][1]["correct"] == "-2*x+7"


def test_repair_failure_falls_back_to_rule_and_repairs_only_once() -> None:
    still_bad = LessonReviewOut(
        decision="repair",
        checks=_checks(fail=("lesson_steps",)),
        issues=[
            LessonReviewIssueOut(
                code="STEP_NOT_EQUIVALENT",
                object_key="c",
                field="component",
                expected="仍然不等价",
            )
        ],
    )
    service = ScriptedService(
        write=_write_draft(),
        reviews=[_repair_review(), still_bad],
        repairs=[_repaired_contrast()],
    )
    draft, limitations = _generate(service)
    assert draft is None
    assert limitations and "修复后复审仍未通过" in limitations[0]
    assert "规则模板" in limitations[0]
    assert service.task_keys.count("c.repair_lesson.v1") == 1
    assert service.task_keys.count("c.review_lesson.v1") == 2


def test_repair_uncertain_falls_back() -> None:
    service = ScriptedService(
        write=_write_draft(),
        reviews=[_repair_review()],
        repairs=[LessonRepairOut(status="uncertain", replacements=[])],
    )
    draft, limitations = _generate(service)
    assert draft is None
    assert limitations and "未能给出可用的最小修复" in limitations[0]
    assert service.task_keys.count("c.review_lesson.v1") == 1


def test_reject_is_not_repaired() -> None:
    service = ScriptedService(
        write=_write_draft(),
        reviews=[LessonReviewOut(decision="reject", checks=_checks(), reasons=["答案泄漏"])],
    )
    draft, limitations = _generate(service)
    assert draft is None
    assert limitations and "复核未通过" in limitations[0]
    assert "c.repair_lesson.v1" not in service.task_keys


# --------------------------------------------------------------------------- #
# repair 越界与 schema 冻结
# --------------------------------------------------------------------------- #


def test_repair_out_of_scope_component_is_rejected() -> None:
    out_of_scope = LessonRepairOut(
        status="ok",
        replacements=[
            LessonReplacementOut(
                object_key="t",
                field="component",
                value={
                    "id": "t",
                    "type": "text",
                    "heading": "换个说法",
                    "paragraphs": ["不该改的组件被改了。"],
                },
            )
        ],
    )
    service = ScriptedService(
        write=_write_draft(),
        reviews=[_repair_review()],
        repairs=[out_of_scope],
    )
    draft, limitations = _generate(service)
    assert draft is None
    assert limitations and "越界" in limitations[0]
    assert service.task_keys.count("c.review_lesson.v1") == 1


def test_repair_cannot_change_component_type_or_id() -> None:
    type_change = LessonRepairOut(
        status="ok",
        replacements=[
            LessonReplacementOut(
                object_key="c",
                field="component",
                value={
                    "id": "c",
                    "type": "text",
                    "heading": "换成文本",
                    "paragraphs": ["把对照组件换成文本，绕过数学检查。"],
                },
            )
        ],
    )
    service = ScriptedService(
        write=_write_draft(),
        reviews=[_repair_review()],
        repairs=[type_change],
    )
    draft, limitations = _generate(service)
    assert draft is None
    assert limitations and "组件类型" in limitations[0]


def test_repair_without_located_issue_falls_back() -> None:
    review = LessonReviewOut(
        decision="repair",
        checks=_checks(fail=("lesson_steps",)),
        reasons=["推导第 2 步不等价"],
    )
    service = ScriptedService(
        write=_write_draft(),
        reviews=[review],
        repairs=[_repaired_contrast()],
    )
    draft, limitations = _generate(service)
    assert draft is None
    assert limitations and "没有给出可定位的字段或组件" in limitations[0]
    assert "c.repair_lesson.v1" not in service.task_keys


def test_repair_payload_trying_to_change_kp_ids_is_rejected() -> None:
    # 模拟模型绕开 schema 返回 kp_ids 替换：字段枚举先拒绝，程序边界再只认 LessonRepairOut。
    raw = {
        "status": "ok",
        "replacements": [
            {"object_key": OBJECT_KEY, "field": "kp_ids", "value": ["kp-2"]}
        ],
        "issues": [],
    }
    with pytest.raises(ValidationError):
        LessonRepairOut.model_validate(raw)
    service = ScriptedService(
        write=_write_draft(),
        reviews=[_repair_review()],
        repairs=[raw],
    )
    draft, limitations = _generate(service)
    assert draft is None
    assert limitations and "修复未完成" in limitations[0]
    assert service.task_keys.count("c.review_lesson.v1") == 1


def test_repair_introducing_protected_text_fails_full_recheck() -> None:
    protected = "受保护题 Q1 的原文答案，这一句是题目内容"
    review = LessonReviewOut(
        decision="repair",
        checks=_checks(fail=("lesson_answer_guard",)),
        issues=[
            LessonReviewIssueOut(
                code="ANSWER_LEAK",
                object_key="t",
                field="component",
                expected="删掉受保护题目内容",
            )
        ],
    )
    leak = LessonRepairOut(
        status="ok",
        replacements=[
            LessonReplacementOut(
                object_key="t",
                field="component",
                value={
                    "id": "t",
                    "type": "text",
                    "heading": "换一段话",
                    "paragraphs": [protected],
                },
            )
        ],
    )
    service = ScriptedService(
        write=_write_draft(),
        reviews=[review],
        repairs=[leak],
    )
    draft, limitations = _generate(service, protected_texts=[protected])
    assert draft is None
    assert limitations and "修复后规则硬检未通过" in limitations[0]
    assert "ANSWER_PROTECTION" in limitations[0]
    assert service.task_keys.count("c.review_lesson.v1") == 1


# --------------------------------------------------------------------------- #
# 八项逐项复核：缺项、重复、换对象、伪造依据
# --------------------------------------------------------------------------- #


def test_missing_new_check_is_rejected_without_repair() -> None:
    checks = [
        check for check in _checks() if check.check_id != "lesson_order"
    ]
    service = ScriptedService(
        write=_write_draft(),
        reviews=[LessonReviewOut(decision="pass", checks=checks)],
    )
    draft, limitations = _generate(service)
    assert draft is None
    assert limitations and "不完整" in limitations[0]
    assert "lesson_order" in limitations[0]
    assert "c.repair_lesson.v1" not in service.task_keys


def test_duplicate_check_is_rejected() -> None:
    duplicate = LessonReviewCheckOut(
        check_id="lesson_math",
        object_key=OBJECT_KEY,
        result="pass",
        reason="",
    )
    service = ScriptedService(
        write=_write_draft(),
        reviews=[LessonReviewOut(decision="pass", checks=_checks(extra=[duplicate]))],
    )
    draft, limitations = _generate(service)
    assert draft is None
    assert limitations and "重复" in limitations[0]


def test_forged_object_key_is_rejected() -> None:
    service = ScriptedService(
        write=_write_draft(),
        reviews=[LessonReviewOut(decision="pass", checks=_checks(object_key="OTHER"))],
    )
    draft, limitations = _generate(service)
    assert draft is None
    assert limitations and "与下发对象不匹配" in limitations[0]


def test_forged_evidence_ref_is_rejected() -> None:
    checks = _checks()
    checks[0].evidence_refs = [
        EvidenceRef(
            kind="finding",
            id="F-FAKE",
            run_id="run-1",
            analysis_version="B-1",
        )
    ]
    service = ScriptedService(
        write=_write_draft(),
        reviews=[LessonReviewOut(decision="pass", checks=checks)],
    )
    draft, limitations = _generate(service)
    assert draft is None
    assert limitations and "未下发的依据" in limitations[0]


def test_overall_pass_with_empty_checks_is_rejected() -> None:
    service = ScriptedService(
        write=_write_draft(),
        reviews=[LessonReviewOut(decision="pass", checks=[])],
    )
    draft, limitations = _generate(service)
    assert draft is None
    assert limitations and "不完整" in limitations[0]


# --------------------------------------------------------------------------- #
# 原有六项兼容
# --------------------------------------------------------------------------- #


def test_original_six_checks_keep_working_with_eight_item_review() -> None:
    assert set(ORIGINAL_SIX) <= set(REQUIRED_REVIEW_CHECKS)
    assert len(REQUIRED_REVIEW_CHECKS) == 8
    service = ScriptedService(
        write=_write_draft(),
        reviews=[_pass_review()],
    )
    draft, limitations = _generate(service)
    assert draft is not None
    assert limitations == []


def test_review_info_for_exposes_same_eight_checks() -> None:
    draft = build_rule_lesson(
        title="表示数量关系并代入求值",
        goal="表示数量关系并代入求值",
        kp_id="kp-T01I1",
        evidence_refs=_refs(),
        teaching_points=ERROR_POINTS,
    )
    review = review_info_for(draft, validate_lesson(draft))
    assert {check.code for check in review.checks} == set(REQUIRED_REVIEW_CHECKS)
    assert all(check.passed for check in review.checks)
