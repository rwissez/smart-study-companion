"""Operator commands: seed the synthetic demo dataset, export contract artifacts.

Both commands are read-only with respect to competition material: the seeder
accepts only the checked-in synthetic fixture and refuses anything that is not
marked ``synthetic``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from study_companion import __version__
from study_companion.config import REPO_ROOT
from study_companion.contracts import PreparedDataset
from study_companion.db import models
from study_companion.db.session import session_scope

FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "synthetic_contracts.json"
CONTRACTS_DIR = REPO_ROOT / "contracts"
RULE_VERSION_FALLBACK = "synthetic-rules-v1"


# --------------------------------------------------------------------------- #
# seed-demo
# --------------------------------------------------------------------------- #


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _canonical_hash(payload: Any) -> str:
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _source_row_id(dataset_id: str, table_name: str, row_number: int) -> str:
    return f"{dataset_id}:{table_name}:{row_number}"


def _load_fixture(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    prepared = payload["prepared_dataset"]
    if not prepared["source_manifest"].get("synthetic", False):
        raise SystemExit(f"拒绝导入：{path} 未标记为合成数据。")
    return payload


def _clear_dataset(session: Session, dataset_id: str) -> None:
    """Delete one dataset's rows, children first, for an idempotent reseed."""
    session.execute(
        delete(models.AnalysisResult).where(
            models.AnalysisResult.run_id.in_(
                select(models.AnalysisRun.id).where(models.AnalysisRun.dataset_id == dataset_id)
            )
        )
    )
    session.execute(delete(models.AnalysisRun).where(models.AnalysisRun.dataset_id == dataset_id))
    session.execute(
        delete(models.QualityIssue).where(models.QualityIssue.dataset_id == dataset_id)
    )
    session.execute(delete(models.AnswerEvent).where(models.AnswerEvent.dataset_id == dataset_id))
    for version in session.scalars(
        select(models.Catalog.version).where(models.Catalog.dataset_id == dataset_id)
    ).all():
        session.execute(
            delete(models.Material).where(models.Material.catalog_version == version)
        )
        session.execute(
            delete(models.Indicator).where(models.Indicator.catalog_version == version)
        )
        session.execute(delete(models.Catalog).where(models.Catalog.version == version))
    session.execute(
        delete(models.DemoSession).where(models.DemoSession.dataset_id == dataset_id)
    )
    session.execute(delete(models.Node).where(models.Node.dataset_id == dataset_id))
    session.execute(delete(models.Student).where(models.Student.dataset_id == dataset_id))
    session.execute(delete(models.SourceRow).where(models.SourceRow.dataset_id == dataset_id))
    session.execute(delete(models.Dataset).where(models.Dataset.id == dataset_id))
    session.flush()


def seed_demo(path: Path = FIXTURE_PATH, force: bool = False) -> dict[str, Any]:
    payload = _load_fixture(path)
    prepared = PreparedDataset.model_validate(payload["prepared_dataset"])
    catalog = payload["catalog"]
    analysis = payload["analysis_bundle"]

    dataset_id = prepared.source_manifest.dataset_id
    catalog_version = catalog["version"]

    with session_scope() as session:
        if session.get(models.Dataset, dataset_id) is not None:
            if not force:
                return {"dataset_id": dataset_id, "skipped": True}
            _clear_dataset(session, dataset_id)

        session.add(
            models.Dataset(
                id=dataset_id,
                created_at=_now(),
                parent_id=None,
                status="ready",
                manifest_json=dict(prepared.source_manifest.model_dump()),
            )
        )
        session.flush()

        row_ids: dict[str, str] = {}
        for table_name, rows in payload["raw_tables"].items():
            for row in rows:
                row_number = int(row["source_row_number"])
                row_id = _source_row_id(dataset_id, table_name, row_number)
                session.add(
                    models.SourceRow(
                        id=row_id,
                        dataset_id=dataset_id,
                        table_name=table_name,
                        row_number=row_number,
                        payload_json=dict(row),
                    )
                )
                row_ids[f"{table_name}:{row_number}"] = row_id

        for student in prepared.students:
            session.add(
                models.Student(
                    dataset_id=dataset_id,
                    student_id=student.student_id,
                    class_id=student.class_id,
                    grade=student.grade,
                )
            )
        for node in prepared.nodes:
            session.add(
                models.Node(
                    dataset_id=dataset_id,
                    node_id=node.node_id,
                    payload_json=node.model_dump(),
                )
            )

        session.add(
            models.Catalog(
                version=catalog_version,
                dataset_id=dataset_id,
                content_hash=_canonical_hash(catalog),
                created_at=_now(),
                payload_json={"topics": catalog["topics"], "links": catalog["links"]},
            )
        )
        session.flush()
        for indicator in catalog["indicators"]:
            session.add(
                models.Indicator(
                    catalog_version=catalog_version,
                    indicator_id=indicator["indicator_id"],
                    payload_json=dict(indicator),
                )
            )
        for material in catalog["materials"]:
            session.add(
                models.Material(
                    catalog_version=catalog_version,
                    material_id=material["material_id"],
                    payload_json=dict(material),
                )
            )

        for event in prepared.selected_events:
            selected = None
            if event.selected_record_id:
                record = next(
                    (r for r in prepared.raw_records if r.record_id == event.selected_record_id),
                    None,
                )
                if record is not None:
                    selected = row_ids.get(f"{record.table_name}:{record.source_row_number}")
            session.add(
                models.AnswerEvent(
                    dataset_id=dataset_id,
                    event_id=event.event_id,
                    student_id=event.student_id,
                    node_id=event.node_id,
                    material_id=event.material_id,
                    selected_source_row_id=selected,
                    disposition=event.disposition,
                    payload_json=event.model_dump(),
                )
            )

        for issue in prepared.quality_issues:
            session.add(
                models.QualityIssue(
                    id=issue.issue_id,
                    dataset_id=dataset_id,
                    event_id=issue.event_id,
                    payload_json=issue.model_dump(),
                )
            )

        session.add(
            models.AnalysisRun(
                id=analysis["run_id"],
                dataset_id=analysis["dataset_id"],
                catalog_version=analysis["catalog_version"],
                rule_version=analysis.get("rule_version", RULE_VERSION_FALLBACK),
                config_json=dict(analysis["config"]),
                status="succeeded",
                created_at=_now(),
                error_json=None,
            )
        )
        session.flush()
        for result in analysis["student_results"]:
            session.add(
                models.AnalysisResult(
                    run_id=result["run_id"],
                    subject_type="student",
                    subject_id=result["student_id"],
                    node_id=result["node_id"],
                    payload_json=dict(result),
                )
            )
        for result in analysis.get("class_results", []):
            session.add(
                models.AnalysisResult(
                    run_id=result["run_id"],
                    subject_type="class",
                    subject_id=result["class_id"],
                    node_id=result["node_id"],
                    payload_json=dict(result),
                )
            )

    return {
        "dataset_id": dataset_id,
        "catalog_version": catalog_version,
        "run_id": analysis["run_id"],
        "students": len(prepared.students),
        "events": len(prepared.selected_events),
        "skipped": False,
    }


# --------------------------------------------------------------------------- #
# export-contracts
# --------------------------------------------------------------------------- #

_JSON_KWARGS: dict[str, Any] = {"ensure_ascii": False, "indent": 2, "sort_keys": True}


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, **_JSON_KWARGS) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


def export_contracts(target: Path = CONTRACTS_DIR) -> list[Path]:
    from study_companion import contracts
    from study_companion.main import app

    written: list[Path] = []
    schema_dir = target / "schemas"

    for name, obj in sorted(vars(contracts).items()):
        if not isinstance(obj, type) or not issubclass(obj, contracts.Contract):
            continue
        if obj is contracts.Contract:
            continue
        path = schema_dir / f"{name}.json"
        _write_json(path, obj.model_json_schema())
        written.append(path)

    openapi_path = target / "openapi.json"
    _write_json(openapi_path, app.openapi())
    written.append(openapi_path)

    example_path = target / "examples" / "synthetic_contracts.json"
    _write_json(example_path, json.loads(FIXTURE_PATH.read_text(encoding="utf-8")))
    written.append(example_path)

    return written


# --------------------------------------------------------------------------- #
# split-textbook
# --------------------------------------------------------------------------- #


def split_textbook_command(pdfs: list[Path], out: Path) -> dict[str, Any]:
    """切分教材切片 PDF，产出 materials_index / page_mapping / catalog_review 草稿。

    半自动流程：全部条目 verification_status=unverified，A 人工核对后置 verified。
    不写数据库；产出仅落在输出目录，走 A 的目录流程入库。
    """
    from study_companion.analysis.textbook_split import split_textbook

    if not pdfs:
        raise SystemExit("请提供至少一个 PDF 文件")

    all_materials: list[dict[str, Any]] = []
    review_lines: list[str] = [
        "# 教材切分核对清单（脚本自动生成草稿）",
        "",
        "> 全部条目 verification_status=unverified。逐条核对页码与章节归属后，",
        "> 将 materials_index.json 中对应条目改为 verified，并在此文件记录核对结论。",
        "",
    ]
    summary: dict[str, Any] = {"files": [], "total_sections": 0}

    for pdf in pdfs:
        result = split_textbook(pdf)
        all_materials.extend(result["materials"])
        notes = result["review_notes"]
        summary["files"].append(
            {
                "pdf": pdf.name,
                "file_pages": result["total_file_pages"],
                "chapters": result["chapters_seen"],
                "sections": len(result["materials"]),
                "continuity_issues": len(notes["page_continuity_issues"]),
            }
        )
        summary["total_sections"] += len(result["materials"])

        review_lines += [
            f"## {pdf.name}（{result['total_file_pages']} 文件页）",
            "",
            f"- 识别到的章：{'、'.join(result['chapters_seen']) or '（无）'}",
            f"- 切分出小节：{len(result['materials'])} 个",
        ]
        if notes["page_continuity_issues"]:
            review_lines.append("- ⚠️ 页码不连续（需对照页面图像确认）：")
            review_lines += [f"  - {msg}" for msg in notes["page_continuity_issues"]]
        if notes["sections_without_page_number"]:
            review_lines.append("- ⚠️ 以下小节起始页无页码（printed_page 为 null，未猜页数）：")
            review_lines += [f"  - {s}" for s in notes["sections_without_page_number"]]
        review_lines.append("")

        _write_json(out / "page_mappings" / f"{pdf.stem}.json", result["page_mapping"])

    _write_json(out / "materials_index.json", {"materials": all_materials})
    (out / "catalog_review.md").write_text("\n".join(review_lines), encoding="utf-8", newline="\n")
    return summary


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="study-companion", description="智慧学伴本地命令")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    seed = sub.add_parser("seed-demo", help="导入合成演示数据（可重复执行）")
    seed.add_argument("--fixture", type=Path, default=FIXTURE_PATH)
    seed.add_argument("--force", action="store_true", help="已存在时先删除该数据集再导入")

    export = sub.add_parser("export-contracts", help="导出 JSON Schema、OpenAPI 与示例")
    export.add_argument("--out", type=Path, default=CONTRACTS_DIR)

    split = sub.add_parser(
        "split-textbook", help="切分教材切片PDF为目录草稿（半自动，产出unverified待A核对）"
    )
    split.add_argument("pdfs", type=Path, nargs="+", help="教材切片PDF文件路径（可多个）")
    split.add_argument(
        "--out", type=Path, default=REPO_ROOT / "data" / "catalogs" / "catalog-1",
        help="输出目录（默认 data/catalogs/catalog-1/）",
    )

    backup = sub.add_parser("backup", help="停写窗口内备份数据库与原始文件")
    backup.add_argument("--out", type=Path, required=True, help="备份输出目录（新建或空目录）")

    verify_backup = sub.add_parser("verify-backup", help="校验备份清单、数据库完整性与文件哈希")
    verify_backup.add_argument("backup_dir", type=Path)

    restore = sub.add_parser("restore", help="从备份恢复到目标根目录（先校验，再原子替换）")
    restore.add_argument("backup_dir", type=Path)
    restore.add_argument("--target", type=Path, required=True)

    args = parser.parse_args(argv)

    if args.command == "seed-demo":
        result = seed_demo(args.fixture, force=args.force)
        print(json.dumps(result, ensure_ascii=False))
        return 0

    if args.command == "export-contracts":
        written = export_contracts(args.out)
        print(f"导出 {len(written)} 个文件到 {args.out}")
        return 0

    if args.command == "split-textbook":
        summary = split_textbook_command(args.pdfs, args.out)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    if args.command == "backup":
        from study_companion.maintenance.backup import create_backup

        manifest = create_backup(args.out)
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0

    if args.command == "verify-backup":
        from study_companion.maintenance.backup import verify_backup as _verify

        report = _verify(args.backup_dir)
        print(json.dumps({"ok": report["ok"], "issues": report["issues"]}, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1

    if args.command == "restore":
        from study_companion.maintenance.backup import restore_backup

        result = restore_backup(args.backup_dir, args.target)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    sys.exit(main())
