"""只读审计：实际材料里能核验多少、不能核验的原因分布。

审计分两层：

* :func:`build_audit_report` 是纯函数，接收已读出的数据，便于测试。
* :func:`load_database_inputs` 用 SQLite ``mode=ro`` 只读连接读取正式库，
  不写数据库、不写正式导出。

命令行::

    python -m study_companion.sources --database data/app.sqlite3
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .enrich import enrich_sources
from .knowledge import (
    KnowledgeProjection,
    project_knowledge_refs,
    project_knowledge_sources,
    summarize_knowledge_refs,
    summarize_knowledge_sources,
)
from .models import MaterialInput, RawMaterialRow, TextbookPage
from .text import is_probably_chapter_label


@dataclass(frozen=True, slots=True)
class AuditInputs:
    """一次审计所需的只读快照。"""

    materials: tuple[MaterialInput, ...]
    material_rows: tuple[RawMaterialRow, ...]
    textbook_pages: tuple[TextbookPage, ...] = ()
    file_ids: Mapping[str, str] = field(default_factory=dict)
    not_applicable_material_ids: tuple[str, ...] = ()
    annotations: tuple[Mapping[str, Any], ...] = ()
    knowledge_version: str = ""
    knowledge_point_ids: tuple[str, ...] = ()
    points: tuple[Mapping[str, Any], ...] = ()
    dataset_id: str = ""
    catalog_version: str = ""


def build_audit_report(
    inputs: AuditInputs,
    *,
    include_items: bool = True,
) -> dict[str, Any]:
    """纯函数审计：只组合核验与知识点投影，不产生副作用。"""

    enrichment = enrich_sources(
        inputs.materials,
        material_rows=inputs.material_rows,
        textbook_pages=inputs.textbook_pages,
        file_ids=inputs.file_ids,
        not_applicable_material_ids=inputs.not_applicable_material_ids,
    )
    knowledge = project_knowledge_refs(
        inputs.materials,
        annotations=inputs.annotations,
        knowledge_version=inputs.knowledge_version,
        knowledge_point_ids=inputs.knowledge_point_ids,
    )
    point_sources = project_knowledge_sources(inputs.points)
    knowledge_summary = summarize_knowledge_refs(knowledge)
    point_summary = summarize_knowledge_sources(point_sources)

    limitations = ["只读审计：未写数据库、未改正式导出"]
    unregistered = [
        item.material_id
        for item in enrichment.items
        if item.reason == "textbook_file_not_registered"
    ]
    if unregistered:
        limitations.append(
            f"{len(unregistered)} 项教材来源 PDF 没有登记 file_id，无法做页码核验："
            + "、".join(unregistered)
        )
    quote_missing = [
        item.material_id
        for item in enrichment.items
        if item.reason == "textbook_quote_not_found"
    ]
    if quote_missing:
        limitations.append(
            f"{len(quote_missing)} 项教材材料在已登记 PDF 中找不到逐字相同的题干或定义句，未写页码："
            + "、".join(quote_missing)
        )
    chapter_labels = [
        material.material_id
        for material in inputs.materials
        if is_probably_chapter_label(material.content)
    ]
    if chapter_labels:
        limitations.append(
            f"{len(chapter_labels)} 项教材材料的内容是章节标题或章节组合，"
            "不是可逐字引用的题干/定义句；按规则不把章节标题当页码证据："
            + "、".join(chapter_labels)
        )
    if knowledge_summary["materials_unknown"]:
        limitations.append(
            f"{knowledge_summary['materials_unknown']} 项材料的正式知识点关联缺失，"
            "按原样保留 unknown，未新增关联"
        )

    result: dict[str, Any] = {
        "read_only": True,
        "dataset_id": inputs.dataset_id,
        "catalog_version": inputs.catalog_version,
        "materials": enrichment.to_dict(include_items=False),
        "knowledge": {
            "knowledge_version": inputs.knowledge_version,
            **knowledge_summary,
            "points": point_summary,
        },
        "limitations": limitations,
    }
    if include_items:
        items: list[dict[str, Any]] = []
        by_material_id = {item.material_id: item for item in knowledge}
        for item in enrichment.items:
            row = item.to_dict(include_quote=False)
            row["quote_preview"] = (item.quote or "")[:120]
            kp = by_material_id.get(item.material_id)
            row["kp_status"] = kp.status if kp else "unknown"
            row["kp_ref_count"] = len(kp.refs) if kp else 0
            items.append(row)
        result["items"] = items
    return result


def _json_object(raw: Any) -> dict[str, Any]:
    if isinstance(raw, Mapping):
        return dict(raw)
    if not raw:
        return {}
    try:
        value = json.loads(str(raw))
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _connect_read_only(database: Path) -> sqlite3.Connection:
    if not database.is_file():
        raise FileNotFoundError(f"数据库不存在：{database}")
    uri = f"file:{database.as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.execute("PRAGMA query_only = ON")
    return connection


def _pick_dataset(connection: sqlite3.Connection, dataset_id: str | None) -> str:
    if dataset_id:
        row = connection.execute("SELECT id FROM datasets WHERE id = ?", (dataset_id,)).fetchone()
        if row is None:
            raise ValueError(f"数据集不存在：{dataset_id}")
        return str(row[0])
    row = connection.execute(
        "SELECT id FROM datasets ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    if row is None:
        raise ValueError("数据库里没有可用于审计的数据集")
    return str(row[0])


def _pick_catalog_version(connection: sqlite3.Connection, dataset_id: str) -> str:
    row = connection.execute(
        "SELECT version FROM catalogs WHERE dataset_id = ? ORDER BY created_at DESC LIMIT 1",
        (dataset_id,),
    ).fetchone()
    return str(row[0]) if row else ""


def _read_materials(
    connection: sqlite3.Connection,
    catalog_version: str,
) -> tuple[MaterialInput, ...]:
    if not catalog_version:
        return ()
    rows = connection.execute(
        "SELECT material_id, payload_json FROM materials WHERE catalog_version = ? ORDER BY material_id",
        (catalog_version,),
    ).fetchall()
    materials: list[MaterialInput] = []
    for material_id, payload in rows:
        data = _json_object(payload)
        data.setdefault("material_id", str(material_id))
        materials.append(MaterialInput.from_payload(data))
    return tuple(materials)


def _read_material_rows(
    connection: sqlite3.Connection,
    dataset_id: str,
    *,
    file_id: str | None,
) -> tuple[RawMaterialRow, ...]:
    rows = connection.execute(
        """
        SELECT row_number, payload_json, file_id
        FROM source_rows
        WHERE dataset_id = ? AND table_name = 'materials'
        ORDER BY row_number
        """,
        (dataset_id,),
    ).fetchall()
    result: list[RawMaterialRow] = []
    for row_number, payload, row_file_id in rows:
        result.append(
            RawMaterialRow(
                row_number=int(row_number),
                payload=_json_object(payload),
                file_id=str(row_file_id) if row_file_id else file_id,
                source_file="题目与材料",
                dataset_id=dataset_id,
            )
        )
    return tuple(result)


def _read_file_objects(
    connection: sqlite3.Connection,
) -> dict[str, dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT i.file_id, i.original_name, f.storage_key, f.media_type
        FROM import_files i
        JOIN file_objects f ON f.file_id = i.file_id
        """
    ).fetchall()
    result: dict[str, dict[str, Any]] = {}
    for file_id, original_name, storage_key, media_type in rows:
        result[str(file_id)] = {
            "file_id": str(file_id),
            "original_name": str(original_name or ""),
            "storage_key": str(storage_key or ""),
            "media_type": str(media_type or ""),
        }
    return result


def _read_dataset_sources(connection: sqlite3.Connection, dataset_id: str) -> dict[str, str]:
    rows = connection.execute(
        "SELECT source_key, file_id FROM dataset_sources WHERE dataset_id = ?",
        (dataset_id,),
    ).fetchall()
    return {str(key): str(file_id) for key, file_id in rows}


def _read_knowledge(
    connection: sqlite3.Connection,
    dataset_id: str,
) -> tuple[str, tuple[Mapping[str, Any], ...], tuple[Mapping[str, Any], ...], tuple[str, ...]]:
    row = connection.execute(
        """
        SELECT version_id, payload_json
        FROM versions
        WHERE dataset_id = ? AND kind = 'knowledge'
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (dataset_id,),
    ).fetchone()
    if row is None:
        return "", (), (), ()
    version_id, payload = row
    body = _json_object(payload)
    annotations = tuple(
        _json_object(item) for item in body.get("annotations") or [] if isinstance(item, Mapping)
    )
    points = tuple(
        _json_object(item) for item in body.get("points") or [] if isinstance(item, Mapping)
    )
    point_ids = tuple(
        str(item.get("kp_id")) for item in points if str(item.get("kp_id") or "").strip()
    )
    return str(version_id), annotations, points, point_ids


def _extract_pdf_pages(
    path: Path,
    *,
    file_id: str,
    source_file: str,
) -> tuple[TextbookPage, ...]:
    try:
        from pypdf import PdfReader
    except ImportError:
        return ()

    logging.getLogger("pypdf").setLevel(logging.ERROR)
    try:
        reader = PdfReader(str(path))
    except Exception:
        return ()

    pages: list[TextbookPage] = []
    for index, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        pages.append(
            TextbookPage(
                file_id=file_id,
                pdf_page=index,
                text=text,
                printed_page=None,
                source_file=source_file,
            )
        )
    return tuple(pages)


def _collect_textbook_pages(
    file_objects: Mapping[str, Mapping[str, Any]],
    file_ids: Mapping[str, str],
    blob_root: Path,
) -> tuple[TextbookPage, ...]:
    pages: list[TextbookPage] = []
    seen: set[str] = set()
    for source_name, file_id in file_ids.items():
        if file_id in seen:
            continue
        meta = file_objects.get(file_id)
        if not meta or meta.get("media_type") != "application/pdf":
            continue
        storage_key = str(meta.get("storage_key") or "")
        if not storage_key:
            continue
        path = blob_root / storage_key
        if not path.is_file():
            continue
        seen.add(file_id)
        pages.extend(
            _extract_pdf_pages(
                path,
                file_id=file_id,
                source_file=str(meta.get("original_name") or source_name),
            )
        )
    return tuple(pages)


def load_database_inputs(
    database: str | Path,
    *,
    dataset_id: str | None = None,
    blob_root: str | Path | None = None,
) -> AuditInputs:
    """只读读取正式库，组装审计输入。"""

    db_path = Path(database)
    connection = _connect_read_only(db_path)
    try:
        chosen_dataset = _pick_dataset(connection, dataset_id)
        catalog_version = _pick_catalog_version(connection, chosen_dataset)
        materials = _read_materials(connection, catalog_version)
        dataset_sources = _read_dataset_sources(connection, chosen_dataset)
        file_objects = _read_file_objects(connection)
        material_rows = _read_material_rows(
            connection,
            chosen_dataset,
            file_id=dataset_sources.get("materials"),
        )
        knowledge_version, annotations, points, point_ids = _read_knowledge(
            connection, chosen_dataset
        )
    finally:
        connection.close()

    file_ids: dict[str, str] = {}
    for meta in file_objects.values():
        if meta.get("media_type") != "application/pdf":
            continue
        original_name = str(meta.get("original_name") or "")
        file_id = str(meta.get("file_id") or "")
        if not original_name or not file_id:
            continue
        file_ids[original_name] = file_id
        file_ids[Path(original_name).name] = file_id
    for role, file_id in dataset_sources.items():
        if role in {"standard", "textbook"}:
            meta = file_objects.get(file_id)
            if meta:
                file_ids[str(meta.get("original_name") or role)] = file_id
                file_ids[Path(str(meta.get("original_name") or role)).name] = file_id

    root = Path(blob_root) if blob_root else db_path.parent / "blobs"
    textbook_pages = _collect_textbook_pages(file_objects, file_ids, root)

    return AuditInputs(
        materials=materials,
        material_rows=material_rows,
        textbook_pages=textbook_pages,
        file_ids=file_ids,
        annotations=annotations,
        knowledge_version=knowledge_version,
        knowledge_point_ids=point_ids,
        points=points,
        dataset_id=chosen_dataset,
        catalog_version=catalog_version,
    )


def run_audit(
    database: str | Path,
    *,
    dataset_id: str | None = None,
    blob_root: str | Path | None = None,
    include_items: bool = True,
) -> dict[str, Any]:
    inputs = load_database_inputs(database, dataset_id=dataset_id, blob_root=blob_root)
    return build_audit_report(inputs, include_items=include_items)


def _print_human(report: Mapping[str, Any]) -> None:
    materials = report["materials"]
    print("只读审计：材料来源精度")
    print(f"数据集：{report.get('dataset_id') or '未知'}")
    print(f"目录版本：{report.get('catalog_version') or '未知'}")
    print(
        "材料：总数 {total}，可验证 {verified}，不可验证 {unverified}，不适用 {not_applicable}".format(
            **materials
        )
    )
    print("按材料类型：")
    for kind, counts in materials["by_material_type"].items():
        print(
            f"  {kind}: 总数 {counts['total']}，可验证 {counts['verified']}，"
            f"不可验证 {counts['unverified']}，不适用 {counts['not_applicable']}"
        )
    print("原因分布：")
    for reason, count in materials["reason_counts"].items():
        print(f"  {reason}: {count}")
    knowledge = report["knowledge"]
    point_counts = knowledge.get("points") or {}
    print(
        "知识点：材料已映射 {mapped}，未映射 {unknown}；"
        "知识点来源 {verified}/{total} 已核验".format(
            mapped=knowledge["materials_mapped"],
            unknown=knowledge["materials_unknown"],
            verified=point_counts.get("locators_verified", 0),
            total=point_counts.get("locators_total", 0),
        )
    )
    print("未映射材料：" + "、".join(knowledge["unknown_material_ids"]))
    limitations = report.get("limitations") or []
    if limitations:
        print("限制：")
        for item in limitations:
            print(f"  - {item}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="材料来源精度只读审计")
    parser.add_argument("--database", default="data/app.sqlite3", help="SQLite 数据库路径")
    parser.add_argument("--dataset-id", default=None, help="指定数据集；默认取最近创建的数据集")
    parser.add_argument("--blob-root", default=None, help="PDF  blob 根目录；默认数据库同级的 blobs")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--no-items", action="store_true", help="JSON 不包含逐条明细")
    args = parser.parse_args(argv)

    report = run_audit(
        args.database,
        dataset_id=args.dataset_id,
        blob_root=args.blob_root,
        include_items=not args.no_items,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_human(report)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
