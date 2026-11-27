"""契约基类、错误与关闭枚举。

本模块是 01 规范的最底层。所有业务对象继承 :class:`Contract`，一律
``extra="forbid"`` 且 strict 校验，避免模型或前端悄悄塞入未定义字段。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class Contract(BaseModel):
    """共享业务对象基类：禁止额外字段，严格类型。"""

    model_config = ConfigDict(extra="forbid", strict=True)


class DomainError(Exception):
    """跨模块统一业务错误。``code`` 由 ``api/errors.py`` 映射为 HTTP 状态。"""

    def __init__(self, code: str, message: str, details: list[dict[str, str]] | None = None):
        self.code = code
        self.message = message
        self.details = details or []
        super().__init__(message)


# --------------------------------------------------------------------------- #
# 关闭枚举（01 §2.1）
# --------------------------------------------------------------------------- #

JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]
ReviewResultStatus = Literal["draft", "validated", "needs_review", "rejected", "human_confirmed"]
PublicationStatus = Literal["unpublished", "published", "superseded"]
Freshness = Literal["current", "compatible_history", "stale"]
ReviewMethod = Literal["rule", "model", "hybrid", "human"]
GenerationMethod = Literal["rule", "model", "hybrid", "human"]
Role = Literal["teacher", "student"]
ScopeKind = Literal["assessment", "learning_current"]
PublishPath = Literal["system_auto", "agent_revision", "teacher_direct"]
PublicationBasis = Literal["automatic_review", "teacher_confirmation"]
PlanApplicabilityResult = Literal["applicable", "limited", "incompatible"]
DependencyState = Literal["pending_recheck", "stale_usable", "suspended", "unavailable"]
StrategyType = Literal[
    "collect_evidence",
    "prerequisite_support",
    "error_correction",
    "consolidation",
    "transfer",
    "teacher_intervention",
]
StrategyShort = Literal[
    "evidence_gap",
    "prerequisite_gap",
    "concrete_error",
    "unstable_performance",
    "transfer_ready",
    "teacher_attention",
]
ActivityKind = Literal["question", "study_material", "micro_lesson"]
ActivityState = Literal["not_started", "in_progress", "completed", "cancelled"]
QuestionType = Literal["choice", "fill_blank", "solution"]
DifficultyLevel = Literal["basic", "intermediate", "advanced"]
EvidenceStatus = Literal["sufficient", "insufficient", "conflicting"]
Performance = Literal["observed_strength", "needs_practice", "undetermined"]
ErrorType = Literal[
    "foundation_gap", "step_error", "hint_dependency", "transfer_difficulty", "other_supported"
]
ProgressClassification = Literal[
    "improved", "partially_improved", "unchanged_or_declined", "insufficient"
]
AppErrorCode = Literal[
    "VALIDATION_ERROR",
    "UNAUTHORIZED",
    "FORBIDDEN",
    "CONTEXT_MISMATCH",
    "NOT_FOUND",
    "KB_NOT_FOUND",
    "KB_NOT_PUBLISHED",
    "CONFLICT",
    "REVISION_CONFLICT",
    "RESULT_NOT_READY",
    "DEPENDENCY_STALE",
    "IDEMPOTENCY_CONFLICT",
    "JOB_RETRY_NOT_AVAILABLE",
    "UNPROCESSABLE",
    "TASK_UNCLEAR",
    "TASK_NOT_AVAILABLE",
    "MATERIAL_INVALID",
    "LESSON_SCHEMA_INVALID",
    "MATH_INVALID",
    "ANSWER_PROTECTION",
    "PLAN_SUSPENDED",
    "EXPORT_INCOMPLETE",
    "EXPORT_SCOPE_INVALID",
    "EXPORT_ARTIFACT_INVALID",
    "UPLOAD_TOO_LARGE",
    "MODEL_NOT_CONFIGURED",
    "MODEL_TIMEOUT",
    "MODEL_CAPABILITY_UNAVAILABLE",
    "MODEL_BAD_RESPONSE",
    "SERVICE_UNAVAILABLE",
    "INTERNAL_ERROR",
]


def canonical_json(value: Any) -> str:
    """稳定 JSON：用于哈希与摘要，禁止 NaN/Infinity。"""

    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
