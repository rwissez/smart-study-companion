"""材料来源核验。

设计约束：

* 只有“唯一且逐字相同”的原始材料行，或“唯一且逐字相同”的教材页面，才写
  ``verified``。
* 多行/多页命中视为无法唯一定位，保持 ``unverified``，不挑其中一条。
* 教材找不到原文时只写原因，不写页码；页码只从命中的 :class:`TextbookPage`
  读取，从不推算。
* 所有结论都带稳定原因码和中文说明，便于审计与后续人工复核。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Mapping, Sequence
from typing import Any

from .models import (
    EnrichedSource,
    EnrichmentReport,
    MaterialInput,
    RawMaterialRow,
    TextbookPage,
)
from .text import (
    exact_key,
    file_name,
    find_quote,
    is_probably_chapter_label,
    normalize_exact,
    normalize_material_type,
)

REASON_DETAILS: dict[str, str] = {
    "matched_by_material_id": "材料编号在原始材料表中唯一命中，题干逐字一致",
    "matched_by_content": "材料编号未命中，题干原文在原始材料表中唯一命中",
    "material_row_not_found": "原始材料表中找不到材料编号或题干原文，保持未核验",
    "material_row_ambiguous": "原始材料表中存在多条同编号或同题干记录，无法唯一定位",
    "material_content_mismatch": "材料编号命中，但题干与原始材料行逐字不一致，拒绝核验",
    "material_type_mismatch": "材料编号命中，但材料类型与原始材料行不一致，拒绝核验",
    "material_content_missing": "材料或原始材料行缺少题干文本，无法逐字比对",
    "material_identity_missing": "材料编号与题干均为空，无法定位原始材料行",
    "row_number_inconsistent": "原始材料行自带的 source_row_number 与逻辑行号冲突",
    "textbook_quote_found": "教材 PDF 页面中唯一命中逐字相同的题干或定义句",
    "textbook_quote_not_found": "教材 PDF 中找不到逐字相同的题干或定义句，不写页码",
    "textbook_quote_ambiguous": "教材 PDF 中同一句在多页命中，无法唯一定位，不写页码",
    "textbook_content_not_quotable": "教材材料内容是章节标题或章节组合，不是可逐字引用的题干/定义句",
    "textbook_content_missing": "教材材料缺少题干或定义句文本，无法核验",
    "textbook_file_not_registered": "教材来源文件没有可用的登记 file_id，无法做页码核验",
    "textbook_text_unavailable": "教材来源文件已登记，但没有可检索的页面文本",
    "textbook_not_applicable": "目录、封面等非正文材料，页码核验不适用",
}


def _detail(code: str) -> str:
    return REASON_DETAILS[code]


def _str_or_none(value: Any) -> str | None:
    text = normalize_exact(value)
    return text or None


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _previous_status(material: MaterialInput) -> str:
    return _str_or_none(material.source_locator.get("verification_status")) or "unverified"


def _source_label(material: MaterialInput) -> str | None:
    return material.source_label or _str_or_none(material.source_locator.get("source_label"))


def _source_kind(material: MaterialInput) -> str | None:
    return _str_or_none(material.source_locator.get("source_kind"))


def _row_number_consistent(row: RawMaterialRow) -> bool:
    declared = row.declared_row_number
    return declared is None or declared == row.row_number


def _type_matches(row: RawMaterialRow, material: MaterialInput) -> bool:
    raw_type = normalize_material_type(row.material_type)
    if not raw_type:
        return True
    return raw_type == normalize_material_type(material.material_type)


def _content_matches(row: RawMaterialRow, material: MaterialInput) -> bool:
    row_content = exact_key(row.content)
    material_content = exact_key(material.content)
    return bool(row_content) and row_content == material_content


def _unverified(
    material: MaterialInput,
    code: str,
    *,
    file_id: str | None = None,
) -> EnrichedSource:
    """保持未核验，并保留已有书目字段；已有页码/引文只在原状态已核验时保留。"""

    existing = material.source_locator
    previous = _previous_status(material)
    keep_existing_evidence = previous == "verified"
    return EnrichedSource(
        material_id=material.material_id,
        material_type=material.material_type,
        verification_status="unverified",
        reason=code,
        reason_detail=_detail(code),
        source_file=_str_or_none(existing.get("source_file")),
        source_row_number=_int_or_none(existing.get("source_row_number")) if keep_existing_evidence else None,
        file_id=file_id or _str_or_none(existing.get("file_id")),
        quote=_str_or_none(existing.get("quote")) if keep_existing_evidence else None,
        pdf_page=_int_or_none(existing.get("pdf_page")) if keep_existing_evidence else None,
        printed_page=_str_or_none(existing.get("printed_page")) if keep_existing_evidence else None,
        source_label=_source_label(material),
        source_kind=_source_kind(material),
        previous_status=previous,
    )


def _not_applicable(material: MaterialInput) -> EnrichedSource:
    existing = material.source_locator
    return EnrichedSource(
        material_id=material.material_id,
        material_type=material.material_type,
        verification_status="not_applicable",
        reason="textbook_not_applicable",
        reason_detail=_detail("textbook_not_applicable"),
        source_file=_str_or_none(existing.get("source_file")),
        file_id=_str_or_none(existing.get("file_id")),
        source_label=_source_label(material),
        source_kind=_source_kind(material),
        previous_status=_previous_status(material),
    )


def _verified_row(
    material: MaterialInput,
    row: RawMaterialRow,
    *,
    reason: str,
) -> EnrichedSource:
    existing = material.source_locator
    return EnrichedSource(
        material_id=material.material_id,
        material_type=material.material_type,
        verification_status="verified",
        reason=reason,
        reason_detail=_detail(reason),
        source_file=row.source_file or _str_or_none(existing.get("source_file")) or "题目与材料",
        source_row_number=row.row_number,
        file_id=row.file_id or _str_or_none(existing.get("file_id")),
        quote=row.content,
        source_label=_source_label(material),
        source_kind=_source_kind(material) or "competition_material",
        previous_status=_previous_status(material),
    )


def _verified_textbook(
    material: MaterialInput,
    page: TextbookPage,
    quote: str,
    *,
    file_id: str,
    source_file: str | None,
) -> EnrichedSource:
    existing = material.source_locator
    return EnrichedSource(
        material_id=material.material_id,
        material_type=material.material_type,
        verification_status="verified",
        reason="textbook_quote_found",
        reason_detail=_detail("textbook_quote_found"),
        source_file=source_file or page.source_file or _str_or_none(existing.get("source_file")),
        file_id=page.file_id or file_id,
        quote=quote,
        pdf_page=page.pdf_page,
        printed_page=_str_or_none(page.printed_page),
        source_label=_source_label(material),
        source_kind=_source_kind(material) or "competition_material",
        previous_status=_previous_status(material),
    )


def _lookup_file_id(
    existing: Mapping[str, Any],
    source_file: str | None,
    file_ids: Mapping[str, str],
) -> str | None:
    candidates: list[Any] = [existing.get("file_id")]
    if source_file:
        candidates.extend([file_ids.get(source_file), file_ids.get(file_name(source_file))])
        normalized_source = normalize_exact(source_file).replace("\\", "/")
        target_name = file_name(source_file)
        for key, value in file_ids.items():
            normalized_key = normalize_exact(key).replace("\\", "/")
            if normalized_key == normalized_source or file_name(key) == target_name:
                candidates.append(value)
    for candidate in candidates:
        text = _str_or_none(candidate)
        if text:
            return text
    return None


def _pages_for(
    file_id: str,
    source_file: str | None,
    pages: Sequence[TextbookPage],
) -> list[TextbookPage]:
    by_id = [page for page in pages if page.file_id == file_id]
    if by_id:
        return by_id
    target_name = file_name(source_file) if source_file else ""
    if not target_name:
        return []
    return [
        page
        for page in pages
        if page.source_file and file_name(page.source_file) == target_name
    ]


def _enrich_row_backed(
    material: MaterialInput,
    by_id: dict[str, list[RawMaterialRow]],
    by_content: dict[str, list[RawMaterialRow]],
) -> EnrichedSource:
    material_key = exact_key(material.material_id)
    content_key = exact_key(material.content)
    if not material_key and not content_key:
        return _unverified(material, "material_identity_missing")

    id_rows = by_id.get(material_key, []) if material_key else []
    if len(id_rows) > 1:
        return _unverified(material, "material_row_ambiguous")
    if len(id_rows) == 1:
        row = id_rows[0]
        if not _row_number_consistent(row):
            return _unverified(material, "row_number_inconsistent")
        if not _type_matches(row, material):
            return _unverified(material, "material_type_mismatch")
        if not _content_matches(row, material):
            code = "material_content_missing" if not exact_key(row.content) else "material_content_mismatch"
            return _unverified(material, code)
        return _verified_row(material, row, reason="matched_by_material_id")

    content_rows = by_content.get(content_key, []) if content_key else []
    if len(content_rows) > 1:
        return _unverified(material, "material_row_ambiguous")
    if len(content_rows) == 1:
        row = content_rows[0]
        if not _row_number_consistent(row):
            return _unverified(material, "row_number_inconsistent")
        if not _type_matches(row, material):
            return _unverified(material, "material_type_mismatch")
        return _verified_row(material, row, reason="matched_by_content")

    return _unverified(material, "material_row_not_found")


def _enrich_textbook(
    material: MaterialInput,
    pages: Sequence[TextbookPage],
    file_ids: Mapping[str, str],
    not_applicable: Collection[str],
) -> EnrichedSource:
    existing = material.source_locator
    if material.material_id in not_applicable:
        return _not_applicable(material)

    source_file = _str_or_none(existing.get("source_file")) or material.source_label
    file_id = _lookup_file_id(existing, source_file, file_ids)
    if not file_id:
        return _unverified(material, "textbook_file_not_registered")

    pages_for_file = _pages_for(file_id, source_file, pages)
    if not pages_for_file:
        return _unverified(material, "textbook_text_unavailable", file_id=file_id)

    if is_probably_chapter_label(material.content):
        return _unverified(material, "textbook_content_not_quotable", file_id=file_id)
    if not exact_key(material.content):
        return _unverified(material, "textbook_content_missing", file_id=file_id)

    hits: list[tuple[TextbookPage, str]] = []
    for page in pages_for_file:
        quote = find_quote(page.text, material.content)
        if quote is not None:
            hits.append((page, quote))
    if not hits:
        return _unverified(material, "textbook_quote_not_found", file_id=file_id)
    if len(hits) > 1:
        return _unverified(material, "textbook_quote_ambiguous", file_id=file_id)

    page, quote = hits[0]
    return _verified_textbook(
        material,
        page,
        quote,
        file_id=file_id,
        source_file=source_file,
    )


def enrich_sources(
    materials: Sequence[MaterialInput],
    *,
    material_rows: Sequence[RawMaterialRow] = (),
    textbook_pages: Sequence[TextbookPage] = (),
    file_ids: Mapping[str, str] | None = None,
    not_applicable_material_ids: Collection[str] = (),
) -> EnrichmentReport:
    """核验一批材料，返回逐条结论与汇总。

    ``material_rows`` 是原始材料表逻辑行；``textbook_pages`` 是已抽取的教材
    页面文本；``file_ids`` 把来源路径或文件名映射到已登记 file_id。
    """

    normalized_materials = tuple(
        item if isinstance(item, MaterialInput) else MaterialInput.from_payload(item)
        for item in materials
    )
    rows = tuple(material_rows)
    pages = tuple(textbook_pages)
    file_id_map = {str(key): str(value) for key, value in (file_ids or {}).items()}
    not_applicable = {str(item) for item in not_applicable_material_ids}

    by_id: dict[str, list[RawMaterialRow]] = defaultdict(list)
    by_content: dict[str, list[RawMaterialRow]] = defaultdict(list)
    for row in rows:
        material_key = exact_key(row.material_id)
        content_key = exact_key(row.content)
        if material_key:
            by_id[material_key].append(row)
        if content_key:
            by_content[content_key].append(row)

    results: list[EnrichedSource] = []
    for material in normalized_materials:
        if normalize_material_type(material.material_type) == "textbook":
            results.append(
                _enrich_textbook(material, pages, file_id_map, not_applicable)
            )
        else:
            results.append(_enrich_row_backed(material, by_id, by_content))
    return EnrichmentReport(tuple(results))
