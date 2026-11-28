"""本地备份与恢复（06 §11）。

只导出**正式运行所需**的东西：SQLite 字典序一致的数据库快照、按 ``file_objects``
权威引用枚举的原始/内容文件，以及清单与计数。密钥、``.env``、供应商配置不备份。

恢复流程：先在 staging 校验（打开副本做 ``integrity_check`` / ``foreign_key_check``、
逐个文件核对 SHA-256），全部通过后才替换目标目录；失败保留原环境。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from study_companion.config import Settings, get_settings


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _db_path(settings: Settings) -> Path:
    path = settings.sqlite_path
    if path is None or not path.is_file():
        raise RuntimeError("当前配置不是本地 SQLite，无法执行本地备份")
    return path


def create_backup(out_dir: Path, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    db_path = _db_path(settings)
    backup_id = f"backup-{uuid.uuid4().hex[:12]}"
    target_db = out_dir / "app.sqlite3"

    # sqlite3 Connection.backup 取得一致快照（含 WAL 中已提交页），不复制正在写的文件。
    source = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    destination = sqlite3.connect(target_db)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()

    files: list[dict[str, Any]] = []
    blobs_root = settings.blobs_root
    connection = sqlite3.connect(f"file:{target_db.as_posix()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT file_id, storage_key, sha256, byte_size FROM file_objects"
        ).fetchall()
        counts = {
            "datasets": connection.execute("SELECT COUNT(*) FROM datasets").fetchone()[0],
            "students": connection.execute("SELECT COUNT(*) FROM students").fetchone()[0],
            "versions": connection.execute("SELECT COUNT(*) FROM versions").fetchone()[0],
            "learning_events": connection.execute("SELECT COUNT(*) FROM learning_events").fetchone()[0],
            "exports": connection.execute("SELECT COUNT(*) FROM exports").fetchone()[0],
        }
        try:
            heads = [
                row[0]
                for row in connection.execute("SELECT version_num FROM alembic_version").fetchall()
            ]
        except sqlite3.OperationalError:
            # 未通过 Alembic 建库（例如测试或早期骨架库）时如实记录为空。
            heads = []
    finally:
        connection.close()

    files_root = out_dir / "files"
    for file_id, storage_key, sha256, byte_size in rows:
        source_path = blobs_root / storage_key
        if not source_path.is_file():
            raise RuntimeError(f"原始文件缺失：{storage_key}")
        if _sha256(source_path) != sha256:
            raise RuntimeError(f"原始文件哈希不符：{storage_key}")
        destination_path = files_root / storage_key
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, destination_path)
        files.append(
            {
                "file_id": file_id,
                "path": f"files/{storage_key}",
                "bytes": byte_size,
                "sha256": sha256,
            }
        )

    manifest = {
        "backup_id": backup_id,
        "created_at": _now(),
        "app_version": _app_version(),
        "alembic_heads": heads,
        "db_path": "app.sqlite3",
        "db_sha256": _sha256(target_db),
        "files": files,
        "counts": counts,
        "mode": "stopped_write",
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def _app_version() -> str:
    from study_companion import __version__

    return __version__


def verify_backup(backup_dir: Path) -> dict[str, Any]:
    backup_dir = backup_dir.resolve()
    manifest_path = backup_dir / "manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError("备份目录缺少 manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    issues: list[str] = []
    db_path = backup_dir / manifest["db_path"]
    if not db_path.is_file():
        issues.append("数据库文件缺失")
    else:
        if _sha256(db_path) != manifest.get("db_sha256"):
            issues.append("数据库哈希与清单不一致")
        connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                issues.append(f"integrity_check 失败：{integrity}")
            violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                issues.append(f"外键检查失败：{len(violations)} 行")
        finally:
            connection.close()
    for item in manifest.get("files", []):
        path = backup_dir / item["path"]
        if not path.is_file():
            issues.append(f"附件缺失：{item['path']}")
            continue
        if _sha256(path) != item["sha256"]:
            issues.append(f"附件哈希不符：{item['path']}")
    return {"ok": not issues, "issues": issues, "manifest": manifest}


def restore_backup(backup_dir: Path, target: Path, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    report = verify_backup(backup_dir)
    if not report["ok"]:
        raise RuntimeError("备份校验失败，未做任何替换：" + "；".join(report["issues"][:3]))
    target = target.resolve()
    target.mkdir(parents=True, exist_ok=True)
    staging = target / f".restore-{uuid.uuid4().hex[:8]}"
    staging.mkdir(parents=True, exist_ok=True)
    try:
        manifest = report["manifest"]
        shutil.copy2(backup_dir / manifest["db_path"], staging / "app.sqlite3")
        files_root = staging / "files"
        files_root.mkdir(parents=True, exist_ok=True)
        for item in manifest.get("files", []):
            relative = Path(item["path"]).relative_to("files")
            destination = files_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup_dir / item["path"], destination)
        # 校验 staging 副本后再切到目标根，避免部分覆盖。
        staged_manifest = dict(manifest)
        staged_manifest["db_path"] = "app.sqlite3"
        (staging / "manifest.json").write_text(
            json.dumps(staged_manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        verification = verify_backup(staging)
        if not verification["ok"]:
            raise RuntimeError("恢复副本校验失败：" + "；".join(verification["issues"][:3]))
        shutil.copy2(staging / "app.sqlite3", target / "app.sqlite3")
        shutil.copytree(staging / "files", target / "files", dirs_exist_ok=True)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return {
        "restored": True,
        "backup_id": report["manifest"]["backup_id"],
        "counts": report["manifest"]["counts"],
        "target": str(target),
    }
