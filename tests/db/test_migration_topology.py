from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory


def _config(database_url: str) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("script_location", "migrations")
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_current_migration_history_has_one_resolvable_head() -> None:
    script = ScriptDirectory.from_config(_config("sqlite:///ignored.sqlite3"))
    revisions = list(script.walk_revisions())
    revision_ids = {revision.revision for revision in revisions}

    assert len(revision_ids) == len(revisions)
    assert len(script.get_heads()) == 1
    assert "0002_learning_observations" in revision_ids
    for revision in revisions:
        parents = revision.down_revision
        if parents is None:
            continue
        parent_ids = (parents,) if isinstance(parents, str) else tuple(parents)
        assert set(parent_ids) <= revision_ids


def test_fresh_database_can_upgrade_to_head(tmp_path: Path, monkeypatch) -> None:
    database_url = f"sqlite:///{(tmp_path / 'migrated.sqlite3').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)

    from study_companion.config import reset_settings_cache
    from study_companion.db.engine import dispose_engine
    from study_companion.db.session import reset_sessionmaker

    reset_settings_cache()
    dispose_engine()
    reset_sessionmaker()
    try:
        command.upgrade(_config(database_url), "head")
    finally:
        dispose_engine()
        reset_sessionmaker()
        reset_settings_cache()

    assert (tmp_path / "migrated.sqlite3").exists()
