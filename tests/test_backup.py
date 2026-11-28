"""备份与恢复（RUN-02 的机制部分，合成环境）。"""

from __future__ import annotations

from pathlib import Path

from study_companion.maintenance.backup import create_backup, restore_backup, verify_backup


def test_backup_verify_restore_roundtrip(app_env: Path) -> None:
    backup_dir = app_env.parent / "backup"
    manifest = create_backup(backup_dir)
    assert manifest["db_path"] == "app.sqlite3"
    assert (backup_dir / "manifest.json").is_file()
    assert manifest["counts"]["datasets"] >= 0

    report = verify_backup(backup_dir)
    assert report["ok"] is True, report["issues"]

    restored_root = app_env.parent / "restored"
    result = restore_backup(backup_dir, restored_root)
    assert result["restored"] is True
    assert (restored_root / "app.sqlite3").is_file()


def test_verify_detects_tampered_backup(app_env: Path) -> None:
    backup_dir = app_env.parent / "backup-tampered"
    create_backup(backup_dir)
    db_path = backup_dir / "app.sqlite3"
    db_path.write_bytes(db_path.read_bytes() + b"tampered")
    report = verify_backup(backup_dir)
    assert report["ok"] is False
    assert any("哈希" in issue or "integrity" in issue for issue in report["issues"])
