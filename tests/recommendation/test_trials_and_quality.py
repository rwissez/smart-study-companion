"""策略试验与 C 质量报告（04 §7、§9.2）。"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from study_companion.db import models
from study_companion.recommendation import quality, trials

STAMP = "2026-11-27T00:00:00+00:00"


def _base(session: Session) -> None:
    session.add(
        models.DatasetFamily(
            family_id="fam-1", label="t", data_origin="official", created_at=STAMP, generation=0
        )
    )
    session.flush()
    session.add(
        models.Dataset(
            id="ds-1",
            created_at=STAMP,
            status="ready",
            manifest_json={},
            family_id="fam-1",
            state="ready",
        )
    )
    session.flush()
    session.add(
        models.StudentIdentity(
            student_uid="su-1", family_id="fam-1", external_id="S01", created_at=STAMP
        )
    )
    session.flush()


def _plan_version(session: Session, version_id: str, *, created: str, payload: dict | None = None) -> None:
    session.add(
        models.Version(
            version_id=version_id,
            kind="plan",
            family_id="fam-1",
            dataset_id="ds-1",
            node_key="N01",
            target_key="S01",
            schema_version="1.1",
            rule_version="c-rules-1",
            config_hash="",
            input_digest="",
            body_hash="",
            payload_json=payload
            or {
                "publication_status": "published",
                "publish_path": "system_auto",
                "generation_method": "rule",
                "review_info": {"status": "validated", "method": "rule"},
            },
            created_at=created,
            origin_kind="system",
        )
    )
    session.flush()


def _trial(
    session: Session,
    status: str,
    *,
    created: str,
    target: str = "kp-T01I1",
    strategy: str = "error_correction",
) -> models.StrategyTrialRow:
    plan_version = f"plan-{created}"
    _plan_version(session, plan_version, created=created)
    row = models.StrategyTrialRow(
        trial_id=f"trial-{created}-{strategy}",
        plan_version=plan_version,
        group_id=f"grp-{created}",
        target_key=target,
        strategy=strategy,
        comparison_id=None,
        payload_json={"action_fingerprint": "fp"},
        status=status,
        created_at=created,
    )
    session.add(row)
    session.flush()
    return row


def test_record_trials_is_idempotent(session: Session) -> None:
    _base(session)
    _plan_version(session, "plan-1", created=STAMP)
    groups = [
        {"group_id": "g1", "strategy": "error_correction", "target_key": "kp-1", "material_ids": ["Q1"]}
    ]
    first = trials.record_trials(
        session, plan_version="plan-1", groups=groups, analysis_version="ban-1", created_at=STAMP
    )
    second = trials.record_trials(
        session, plan_version="plan-1", groups=groups, analysis_version="ban-1", created_at=STAMP
    )
    assert len(first) == 1
    assert first[0].trial_id == second[0].trial_id


def test_update_trials_uses_progress_classification(session: Session) -> None:
    _base(session)
    row = _trial(session, "running", created=STAMP)
    changes = trials.update_trials_from_progress(
        session,
        plan_version=row.plan_version,
        comparisons=[
            {
                "comparison_id": "PC-1",
                "scope": {"indicator_id": "kp-T01I1"},
                "classification": "unchanged_or_declined",
            }
        ],
        now_iso=STAMP,
    )
    assert changes and changes[0]["status"] == "ineffective"
    refreshed = session.get(models.StrategyTrialRow, changes[0]["trial_id"])
    assert refreshed is not None and refreshed.status == "ineffective"


def test_two_ineffective_rounds_suppress_strategy(session: Session) -> None:
    _base(session)
    _trial(session, "ineffective", created="2026-11-20T00:00:00+00:00")
    _trial(session, "ineffective", created="2026-11-25T00:00:00+00:00")
    outcome = trials.suppression_for(session, target_key="kp-T01I1")
    assert "error_correction" in outcome["suppressed"]
    assert outcome["escalate"] is False


def test_three_rounds_open_teacher_intervention(session: Session) -> None:
    _base(session)
    _trial(session, "ineffective", created="2026-11-10T00:00:00+00:00")
    _trial(session, "ineffective", created="2026-11-15T00:00:00+00:00")
    _trial(session, "inconclusive", created="2026-11-20T00:00:00+00:00")
    outcome = trials.suppression_for(session, target_key="kp-T01I1")
    assert outcome["escalate"] is True
    case = trials.open_intervention_for(
        session,
        dataset_id="ds-1",
        student_uid="su-1",
        student_id="S01",
        target_key="kp-T01I1",
        reason_code="no_improvement_after_rounds",
        summary="三轮无改善",
        evidence_refs=[],
        now_iso=STAMP,
    )
    assert case is not None and case.status == "open"
    again = trials.open_intervention_for(
        session,
        dataset_id="ds-1",
        student_uid="su-1",
        student_id="S01",
        target_key="kp-T01I1",
        reason_code="no_improvement_after_rounds",
        summary="三轮无改善",
        evidence_refs=[],
        now_iso=STAMP,
    )
    assert again is not None and again.case_id == case.case_id, "同一目标同一原因只保留一个开放案例"


def test_quality_report_counts_and_zero_denominator(session: Session) -> None:
    _base(session)
    _plan_version(session, "plan-a", created=STAMP)
    window = quality.QualityWindow(
        start=datetime(2026, 11, 26, tzinfo=UTC), end=datetime(2026, 11, 28, tzinfo=UTC)
    )
    report = quality.build_report(session, dataset_id="ds-1", node_id="N01", window=window)
    assert report.counts["plans_generated"] == 1
    assert report.counts["plans_published"] == 1
    assert report.counts["rule_degraded"] == 1
    publication_rate = next(item for item in report.coverage if item["key"] == "publication_rate")
    assert publication_rate["value"] == 1.0
    intervention_rate = next(
        item for item in report.coverage if item["key"] == "intervention_resolution_rate"
    )
    assert intervention_rate["value"] is None, "分母为 0 不能给百分比"
