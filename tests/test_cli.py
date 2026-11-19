"""The CLI is the only writer of demo data, and must stay idempotent."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from study_companion.cli import FIXTURE_PATH, export_contracts, seed_demo
from study_companion.db import models


def test_seed_is_idempotent(app_env, session: Session) -> None:
    first = seed_demo()
    assert first["skipped"] is False

    second = seed_demo()
    assert second["skipped"] is True

    third = seed_demo(force=True)
    assert third["skipped"] is False

    assert session.scalar(select(func.count()).select_from(models.Dataset)) == 1
    assert session.scalar(select(func.count()).select_from(models.AnswerEvent)) == 4
    assert session.scalar(select(func.count()).select_from(models.Student)) == 4


def test_seed_refuses_non_synthetic_fixtures(app_env, tmp_path: Path) -> None:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    payload["prepared_dataset"]["source_manifest"]["synthetic"] = False
    disguised = tmp_path / "looks_real.json"
    disguised.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(SystemExit):
        seed_demo(disguised)


def test_export_is_byte_stable(app_env, tmp_path: Path) -> None:
    target = tmp_path / "contracts"

    written = export_contracts(target)
    assert (target / "openapi.json").exists()
    assert (target / "schemas" / "Material.json").exists()
    assert (target / "examples" / "synthetic_contracts.json").exists()

    before = {path: path.read_bytes() for path in written}
    export_contracts(target)
    assert {path: path.read_bytes() for path in written} == before


def test_exported_material_schema_keeps_teacher_fields(app_env, tmp_path: Path) -> None:
    target = tmp_path / "contracts"
    export_contracts(target)

    schema = json.loads((target / "schemas" / "Material.json").read_text(encoding="utf-8"))
    # The contract is the full teacher view; trimming is a transport concern.
    assert "reference_answer" in schema["properties"]
