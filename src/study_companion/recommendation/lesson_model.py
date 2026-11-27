"""C 的模型协作：生成微课堂正文并做独立复核（04 §5、§6）。

只在模型可用且通过规则校验时采用模型正文；任何一步失败都退回规则模板，
并如实记录限制，不让未经验证的内容进入正式方案。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from study_companion.contracts import (
    EvidenceRef,
    LessonComponent,
    LessonDraft,
)
from study_companion.model_service import ModelService, ModelTaskRequest


class LessonModelOut(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    goal: str = Field(min_length=1, max_length=200)
    estimated_minutes: int = Field(default=3, ge=3, le=6)
    components: list[LessonComponent] = Field(min_length=1, max_length=12)
    fallback_text: str | None = Field(default=None, max_length=500)


class LessonReviewOut(BaseModel):
    decision: Literal["pass", "repair", "reject", "uncertain"]
    reasons: list[str] = Field(default_factory=list, max_length=8)
    issues: list[str] = Field(default_factory=list, max_length=8)


def generate_with_model(
    *,
    service: ModelService,
    title: str,
    goal: str,
    kp_id: str,
    evidence_refs: list[EvidenceRef],
    teaching_points: list[str],
    protected_texts: list[str],
) -> tuple[LessonDraft | None, list[str]]:
    """返回 (草稿, 限制说明)。失败时草稿为 None，由调用方退回规则模板。"""

    limitations: list[str] = []
    request = ModelTaskRequest(
        task_key="c.write_content.v1",
        prompt_version="1.0",
        namespace="c",
        input_payload={
            "title": title,
            "goal": goal,
            "knowledge_point_id": kp_id,
            "teaching_points": [item[:300] for item in teaching_points if item][:6],
            "protected_material_contents": [item[:300] for item in protected_texts][:12],
        },
        allow_external=False,
    )
    result = service.generate_structured(request, LessonModelOut)
    if not result.succeeded or result.value is None:
        code = (result.error or {}).get("code", "MODEL_BAD_RESPONSE")
        return None, [f"模型生成微课堂失败（{code}），本轮使用规则模板"]
    value = result.value
    draft = LessonDraft(
        title=value.title,
        goal=value.goal,
        estimated_minutes=value.estimated_minutes,
        kp_ids=[kp_id],
        evidence_refs=evidence_refs[:12],
        source_refs=[],
        components=value.components,
        fallback_text=value.fallback_text or goal,
    )

    review_request = ModelTaskRequest(
        task_key="c.review_lesson.v1",
        prompt_version="1.0",
        namespace="c",
        input_payload={
            "draft": draft.model_dump(mode="json"),
            "knowledge_point_id": kp_id,
            "protected_material_contents": [item[:300] for item in protected_texts][:12],
        },
        allow_external=False,
    )
    review = service.generate_structured(review_request, LessonReviewOut)
    if not review.succeeded or review.value is None:
        limitations.append("微课堂独立复核未完成，按规则校验结果采用")
        return draft, limitations
    if review.value.decision != "pass":
        limitations.append(
            "微课堂独立复核未通过（" + "；".join(review.value.reasons or review.value.issues)[:200] + "），本轮使用规则模板"
        )
        return None, limitations
    return draft, limitations
