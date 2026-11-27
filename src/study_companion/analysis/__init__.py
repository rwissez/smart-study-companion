"""B模块：数据导入、质量检查与统计分析。

对外只暴露固定签名 prepare_dataset / analyze_dataset（签名以统一实施规范第四节为准）。
"""

from study_companion.analysis.evidence import EvidencePackage, build_evidence_package
from study_companion.analysis.observation import (
    ObservationSignal,
    get_previous_observations,
    rerun_profile_with_observations,
    translate_observations,
)
from study_companion.analysis.profile_comparison import (
    ProfileComparison,
    compare_student_profiles,
)
from study_companion.analysis.learning_profile import (
    StudentLearningProfile,
    Weakness,
    build_dialog_basis,
    build_student_learning_profile,
)
from study_companion.analysis.prerequisite_gap import (
    PrerequisiteGapSignal,
    detect_prerequisite_gap,
    gap_summary_for_c,
)
from study_companion.analysis.prepare import prepare_dataset
from study_companion.analysis.rubric_loss import compute_class_rubric_losses, common_loss_issues
from study_companion.analysis.service import analyze_dataset
from study_companion.analysis.strategy_signal import (
    StrategySignal,
    aggregate_signals_by_topic,
    build_strategy_signals,
)

__all__ = [
    "prepare_dataset",
    "analyze_dataset",
    "StrategySignal",
    "build_strategy_signals",
    "aggregate_signals_by_topic",
    "compute_class_rubric_losses",
    "common_loss_issues",
    "EvidencePackage",
    "build_evidence_package",
    "StudentLearningProfile",
    "Weakness",
    "build_student_learning_profile",
    "build_dialog_basis",
    "ObservationSignal",
    "get_previous_observations",
    "translate_observations",
    "rerun_profile_with_observations",
    "ProfileComparison",
    "compare_student_profiles",
    "PrerequisiteGapSignal",
    "detect_prerequisite_gap",
    "gap_summary_for_c",
]
