"""C 微课堂质量：个性化依据、fail-closed 独立复核、可核验引用（FIND-08/09/10）。

全部为合成输入，不调用真实模型。重点验证三件事：

1. 该生这次的具体错误、错因、教师说明必须进入生成输入，并标明是否个性化；
2. 复核调用失败、结果缺项或非 pass 时，草稿不得返回，只能退回规则模板；
3. citation 标签由输入证据生成，模型自拟页码和外部链接不会进入正文。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from study_companion.contracts import EvidenceRef, LessonDraft
from study_companion.recommendation.lesson_model import (
    REQUIRED_REVIEW_CHECKS,
    LessonModelOut,
    LessonReviewCheckOut,
    LessonReviewOut,
    build_citation_labels,
    build_student_error_context,
    generate_with_model,
)


@dataclass
class Result:
    succeeded: bool
    value: object
    error: dict | None = None


class RecordingService:
    def __init__(
        self,
        *,
        write: LessonModelOut | None = None,
        review: LessonReviewOut | None = None,
        write_error: Exception | None = None,
        review_error: Exception | None = None,
    ) -> None:
        self.write = write
        self.review = review
        self.write_error = write_error
        self.review_error = review_error
        self.requests: list[object] = []

    def generate_structured(self, request, schema):  # noqa: ANN001
        self.requests.append(request)
        if request.task_key.endswith("write_content.v1"):
            if self.write_error is not None:
                raise self.write_error
            if self.write is None:
                return Result(False, None, {"code": "MODEL_TIMEOUT"})
            return Result(True, self.write)
        if self.review_error is not None:
            raise self.review_error
        if self.review is None:
            return Result(False, None, {"code": "MODEL_TIMEOUT"})
        return Result(True, self.review)

    @property
    def write_payload(self) -> dict:
        request = next(item for item in self.requests if item.task_key.endswith("write_content.v1"))
        return dict(request.input_payload)


def _checks(result: str = "pass", *, omit: tuple[str, ...] = ()) -> list[LessonReviewCheckOut]:
    return [
        LessonReviewCheckOut(check_id=check_id, result=result if check_id not in omit else "fail", reason="")
        for check_id in REQUIRED_REVIEW_CHECKS
    ]


def _pass_review() -> LessonReviewOut:
    return LessonReviewOut(decision="pass", checks=_checks())


def _draft(*, with_citation: bool = False) -> LessonModelOut:
    components: list[dict] = [
        {
            "id": "t",
            "type": "text",
            "heading": "先看符号",
            "paragraphs": ["上次你漏乘了括号里的第二项，这次只练这一步。"],
        },
        {
            "id": "c",
            "type": "contrast",
            "before": "-(2x-7)",
            "incorrect": "-2x-7",
            "correct": "-2x+7",
            "note": "负号要乘到每一项",
        },
    ]
    if with_citation:
        components.append(
            {
                "id": "source",
                "type": "citation",
                "labels": [{"label": "课本第 99 页，某某出版社", "evidence_index": 0}],
            }
        )
    return LessonModelOut.model_validate(
        {
            "title": "去括号时的符号",
            "goal": "能把括号前的负号分配到每一项",
            "estimated_minutes": 4,
            "components": components,
            "fallback_text": "负号要乘到括号里的每一项。",
        }
    )


def _generic_draft() -> LessonModelOut:
    return LessonModelOut.model_validate(
        {
            "title": "去括号的符号：先看括号前",
            "goal": "能按括号前的符号逐项处理",
            "estimated_minutes": 4,
            "components": [
                {
                    "id": "t",
                    "type": "text",
                    "heading": "先看括号前",
                    "paragraphs": [
                        "这次没有你在这一点的具体错误记录，下面按通用讲解，先看括号前符号怎么分配。"
                    ],
                },
                {
                    "id": "c",
                    "type": "contrast",
                    "before": "-(3x+5)",
                    "incorrect": "-3x+5",
                    "correct": "-3x-5",
                    "note": "负号要乘到每一项",
                },
            ],
            "fallback_text": "这次没有具体错误记录，按通用讲解处理括号前的负号。",
        }
    )


def _refs(*extra: EvidenceRef) -> list[EvidenceRef]:
    return [
        EvidenceRef(kind="knowledge_point", id="kp-1", knowledge_version="kn-1"),
        *extra,
    ]


ERROR_POINTS = [
    "负号分配到括号内每一项的知识点定义",
    "上次去括号时漏乘了第二项，符号也写错了",
    "能正确把负号分配进去",
    "存在具体错误证据，安排纠错",
    "教师说明：先只练负号分配，不急着做整题",
]


# --------------------------------------------------------------------------- #
# FIND-08：个性化依据
# --------------------------------------------------------------------------- #


def test_student_error_context_is_derived_from_teaching_points() -> None:
    context = build_student_error_context(teaching_points=ERROR_POINTS, evidence_refs=_refs())
    assert context["personalized"] is True
    assert context["basis"] == "student_error"
    assert "漏乘" in context["observed_error_notes"][0]
    assert "教师说明" in context["teacher_reason"]
    assert "具体复述" in context["requirement"]


def test_generic_lesson_when_no_error_evidence() -> None:
    context = build_student_error_context(
        teaching_points=["负号分配到括号内每一项的知识点定义"],
        evidence_refs=_refs(),
    )
    assert context["personalized"] is False
    assert context["basis"] == "generic_knowledge"
    assert "通用知识讲解" in context["requirement"]


def test_planner_boilerplate_is_not_treated_as_student_error() -> None:
    """规划器套话含“错误/纠错”，但没有具体错步，不能抬成个性化。"""

    context = build_student_error_context(
        teaching_points=[
            "存在具体错误证据，安排纠错；依据 F-STU-D01-N01-K1（证据状态 sufficient）；选用材料 Q001。",
            "围绕「表示数量关系并代入求值」安排本轮学习",
        ],
        evidence_refs=_refs(
            EvidenceRef(kind="finding", id="F-STU-D01-N01-K1", run_id="run-1", analysis_version="B-1")
        ),
    )
    assert context["personalized"] is False
    assert context["basis"] == "student_diagnosis"
    assert "没有具体错步" in context["requirement"]


def test_specific_error_enters_write_content_payload() -> None:
    service = RecordingService(write=_draft(), review=_pass_review())
    draft, limitations = generate_with_model(
        service=service,  # type: ignore[arg-type]
        title="去括号的符号",
        goal="会变号",
        kp_id="kp-1",
        evidence_refs=_refs(
            EvidenceRef(kind="finding", id="F-SIGN", run_id="run-1", analysis_version="B-1")
        ),
        teaching_points=ERROR_POINTS,
        protected_texts=[],
    )
    assert isinstance(draft, LessonDraft)
    assert limitations == []
    context = service.write_payload["student_error_context"]
    assert context["personalized"] is True
    assert "漏乘" in context["observed_error_notes"][0]
    assert "教师说明" in context["teacher_reason"]
    # 引用索引也是生成输入的一部分，模型不能自拟页码。
    assert service.write_payload["evidence_labels"][0]["evidence_index"] == 0


def test_no_error_evidence_marks_generic_lesson() -> None:
    service = RecordingService(write=_generic_draft(), review=_pass_review())
    draft, limitations = generate_with_model(
        service=service,  # type: ignore[arg-type]
        title="去括号的符号",
        goal="会变号",
        kp_id="kp-1",
        evidence_refs=_refs(),
        teaching_points=["负号分配到括号内每一项的知识点定义"],
        protected_texts=[],
    )
    assert isinstance(draft, LessonDraft)
    context = service.write_payload["student_error_context"]
    assert context["personalized"] is False
    assert "通用知识讲解" in context["requirement"]
    assert any("通用知识讲解" in item for item in limitations)


def test_generic_model_draft_claiming_personal_error_is_rejected() -> None:
    service = RecordingService(write=_draft(), review=_pass_review())
    draft, limitations = generate_with_model(
        service=service,  # type: ignore[arg-type]
        title="去括号的符号",
        goal="会变号",
        kp_id="kp-1",
        evidence_refs=_refs(),
        teaching_points=["负号分配到括号内每一项的知识点定义"],
        protected_texts=[],
    )
    assert draft is None
    assert limitations and "没有具体错误证据" in limitations[0]


def test_model_gets_material_ids_and_diagnosis_scope() -> None:
    service = RecordingService(write=_generic_draft(), review=_pass_review())
    draft, _ = generate_with_model(
        service=service,  # type: ignore[arg-type]
        title="去括号的符号",
        goal="会变号",
        kp_id="kp-1",
        evidence_refs=_refs(),
        teaching_points=["学生具体表现：得分率0.20（1/5）（错因类型：transfer_difficulty）"],
        protected_texts=[],
        material_ids=["Q001", "Q007"],
        student_id="D01",
    )
    assert isinstance(draft, LessonDraft)
    assert service.write_payload["material_ids"] == ["Q001", "Q007"]
    context = service.write_payload["student_error_context"]
    assert context["basis"] == "student_diagnosis"
    assert context["student_token"] == "D01"


# --------------------------------------------------------------------------- #
# FIND-09：复核 fail-closed
# --------------------------------------------------------------------------- #


def test_review_call_failure_falls_back_to_rule() -> None:
    service = RecordingService(write=_draft(), review=None)
    draft, limitations = generate_with_model(
        service=service,  # type: ignore[arg-type]
        title="去括号",
        goal="会变号",
        kp_id="kp-1",
        evidence_refs=_refs(),
        teaching_points=ERROR_POINTS,
        protected_texts=[],
    )
    assert draft is None
    assert limitations and "复核未完成" in limitations[0]
    assert "MODEL_TIMEOUT" in limitations[0]


def test_review_exception_falls_back_to_rule() -> None:
    service = RecordingService(
        write=_draft(), review_error=RuntimeError("network down")
    )
    draft, limitations = generate_with_model(
        service=service,  # type: ignore[arg-type]
        title="去括号",
        goal="会变号",
        kp_id="kp-1",
        evidence_refs=_refs(),
        teaching_points=ERROR_POINTS,
        protected_texts=[],
    )
    assert draft is None
    assert limitations and "复核调用异常" in limitations[0]


@pytest.mark.parametrize("decision", ["repair", "reject", "uncertain"])
def test_review_non_pass_falls_back_to_rule(decision: str) -> None:
    service = RecordingService(
        write=_draft(),
        review=LessonReviewOut(decision=decision, checks=_checks(), reasons=["推导第 2 步不等价"]),
    )
    draft, limitations = generate_with_model(
        service=service,  # type: ignore[arg-type]
        title="去括号",
        goal="会变号",
        kp_id="kp-1",
        evidence_refs=_refs(),
        teaching_points=ERROR_POINTS,
        protected_texts=[],
    )
    assert draft is None
    assert limitations and "复核未通过" in limitations[0]


def test_review_pass_with_missing_checks_falls_back() -> None:
    service = RecordingService(
        write=_draft(),
        review=LessonReviewOut(decision="pass", checks=_checks()[:3]),
    )
    draft, limitations = generate_with_model(
        service=service,  # type: ignore[arg-type]
        title="去括号",
        goal="会变号",
        kp_id="kp-1",
        evidence_refs=_refs(),
        teaching_points=ERROR_POINTS,
        protected_texts=[],
    )
    assert draft is None
    assert limitations and "不完整" in limitations[0]


def test_review_pass_with_failing_check_falls_back() -> None:
    service = RecordingService(
        write=_draft(),
        review=LessonReviewOut(decision="pass", checks=_checks(omit=("lesson_math",))),
    )
    draft, limitations = generate_with_model(
        service=service,  # type: ignore[arg-type]
        title="去括号",
        goal="会变号",
        kp_id="kp-1",
        evidence_refs=_refs(),
        teaching_points=ERROR_POINTS,
        protected_texts=[],
    )
    assert draft is None
    assert limitations and "lesson_math" in limitations[0]


def test_latex_formula_is_rejected_before_review() -> None:
    draft_out = _draft()
    draft_out.components = [
        {"id": "f", "type": "formula", "expr": "\\frac{1}{2}", "caption": "LaTeX"}
    ]
    service = RecordingService(write=draft_out, review=_pass_review())
    draft, limitations = generate_with_model(
        service=service,  # type: ignore[arg-type]
        title="分数",
        goal="会看分数",
        kp_id="kp-1",
        evidence_refs=_refs(),
        teaching_points=ERROR_POINTS,
        protected_texts=[],
    )
    assert draft is None
    assert limitations and "数学表达式硬校验" in limitations[0]
    assert [item.task_key for item in service.requests] == ["c.write_content.v1"]


# --------------------------------------------------------------------------- #
# FIND-10：可核验引用
# --------------------------------------------------------------------------- #


def test_citation_labels_come_from_evidence_refs() -> None:
    refs = _refs(
        EvidenceRef(kind="finding", id="F-SIGN", run_id="run-1", analysis_version="B-1"),
        EvidenceRef(kind="source_page", id="page-12", material_id="MAT-3"),
    )
    labels = build_citation_labels(refs, lesson_title="去括号的符号")
    assert len(labels) == 3
    assert labels[0].evidence_index == 0
    assert "知识点" in labels[0].label and "kp-1" in labels[0].label
    assert "学情诊断" in labels[1].label and "F-SIGN" in labels[1].label
    assert "MAT-3" in labels[2].label and "page-12" in labels[2].label
    assert all("http" not in label.label for label in labels)


def test_model_fabricated_citation_is_replaced() -> None:
    service = RecordingService(write=_draft(with_citation=True), review=_pass_review())
    draft, _ = generate_with_model(
        service=service,  # type: ignore[arg-type]
        title="去括号",
        goal="会变号",
        kp_id="kp-1",
        evidence_refs=_refs(),
        teaching_points=ERROR_POINTS,
        protected_texts=[],
    )
    assert isinstance(draft, LessonDraft)
    citations = [item for item in draft.components if item.type == "citation"]
    assert len(citations) == 1
    labels = [label.label for label in citations[0].labels]
    assert not any("99" in label for label in labels)
    assert any("知识点" in label for label in labels)
    assert [item.type for item in draft.components].count("citation") == 1


def test_no_evidence_means_no_citation_component() -> None:
    service = RecordingService(write=_draft(with_citation=True), review=_pass_review())
    draft, limitations = generate_with_model(
        service=service,  # type: ignore[arg-type]
        title="去括号",
        goal="会变号",
        kp_id="kp-1",
        evidence_refs=[],
        teaching_points=ERROR_POINTS,
        protected_texts=[],
    )
    assert isinstance(draft, LessonDraft)
    assert not any(item.type == "citation" for item in draft.components)
    assert any("没有可核验" in item for item in limitations)


def test_prompt_files_require_personalization_and_sources() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "prompts" / "c"
    write_prompt = (root / "write_content.v1.md").read_text(encoding="utf-8")
    review_prompt = (root / "review_lesson.v1.md").read_text(encoding="utf-8")
    assert "student_error_context" in write_prompt
    assert "通用讲解" in write_prompt
    assert "evidence_labels" in write_prompt
    assert "required_checks" in review_prompt
    assert "lesson_personalization" in review_prompt
    assert "lesson_sources" in review_prompt
