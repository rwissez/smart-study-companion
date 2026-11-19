"""SQLite needs explicit pragmas; a silent default here corrupts data later."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from study_companion.db import models
from study_companion.db.engine import get_engine


def test_connection_pragmas_are_applied(app_env) -> None:
    with get_engine().connect() as connection:
        assert connection.scalar(text("PRAGMA foreign_keys")) == 1
        assert connection.scalar(text("PRAGMA journal_mode")).lower() == "wal"
        assert connection.scalar(text("PRAGMA busy_timeout")) == 5000


def test_foreign_keys_are_actually_enforced(session: Session) -> None:
    session.add(
        models.Student(dataset_id="ds-missing", student_id="S01", class_id="C01", grade=None)
    )
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_seeded_event_keeps_the_scoring_fields(seeded: dict, session: Session) -> None:
    event = session.get(models.AnswerEvent, (seeded["dataset_id"], "E02"))
    assert event is not None
    assert event.disposition == "exclude_score"
    # score 3 against max_score 2 is exactly the case B's rules must exclude.
    assert event.payload_json["score"] == 3
    assert event.payload_json["max_score"] == 2
    assert event.payload_json["status"] == "graded"


def test_seeded_event_links_back_to_its_source_row(seeded: dict, session: Session) -> None:
    event = session.get(models.AnswerEvent, (seeded["dataset_id"], "E01"))
    assert event is not None
    assert event.selected_source_row_id is not None

    row = session.get(models.SourceRow, event.selected_source_row_id)
    assert row is not None
    assert row.table_name == "records"
    assert row.row_number == 1
