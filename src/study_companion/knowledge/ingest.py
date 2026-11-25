"""把一份 PDF 变成知识库：采集 -> 页码矫正 -> 定位信息 -> 规则抽取。

页码矫正与小节识别复用 B 的 textbook_split，不复制其逻辑。
"""

from __future__ import annotations

import json
from pathlib import Path

from study_companion.analysis.textbook_split import split_textbook
from study_companion.knowledge.extract import PageRecord, read_pdf
from study_companion.knowledge.models import KnowledgeBase, Locator
from study_companion.knowledge.store import KnowledgeStore, source_manifest_entry
from study_companion.knowledge.terms import extract_knowledge


def _locators(pdf: Path, pages: list[PageRecord]) -> dict[int, Locator]:
    split = split_textbook(pdf)
    printed = {int(k): v for k, v in split["page_mapping"]["file_page_to_printed"].items()}
    # 每个文件页归属其之前最近的一个小节
    sections = sorted(
        ((m["file_pages"][0], m["chapter"], m["section"]) for m in split["materials"]),
        key=lambda t: t[0],
    )
    out: dict[int, Locator] = {}
    for p in pages:
        chapter = section = ""
        for start, ch, sec in sections:
            if start <= p.file_page:
                chapter, section = ch, sec
        out[p.file_page] = Locator(
            source_file=pdf.name,
            file_page=p.file_page,
            printed_page=printed.get(p.file_page),
            chapter=chapter,
            section=section,
        )
    return out


def ingest_textbook(pdf: Path, prefix: str, kb_version: str = "kb-draft") -> tuple[KnowledgeBase, list[PageRecord]]:
    pages = read_pdf(pdf)
    kb = extract_knowledge(pages, _locators(pdf, pages), prefix, kb_version)
    return kb, pages


def ingest_textbook_snapshot(
    pdf: Path,
    *,
    data_root: Path,
    dataset_id: str,
    catalog_version: str,
    prefix: str,
    kb_version: str,
    page_count: int | None = None,
    parent_version: str | None = None,
) -> KnowledgeBase:
    """Ingest a textbook into a manifest-bound draft snapshot."""
    relative = pdf.resolve().relative_to(data_root.resolve()).as_posix()
    identity_prefix = prefix if parent_version is None else f"{prefix}-{__import__('hashlib').sha1(relative.encode()).hexdigest()[:8]}"
    kb, pages = ingest_textbook(pdf, identity_prefix, kb_version)
    for point in kb.points:
        point.definition_locator = point.definition_locator.model_copy(update={"source_file": relative})
        point.source_locators = [item.model_copy(update={"source_file": relative}) for item in point.source_locators]
    for relation in kb.relations:
        relation.source_file = relative
    store = KnowledgeStore(data_root)
    entry = source_manifest_entry(
        data_root, pdf, source_id=relative, page_count=page_count or len(pages),
        extraction_method=pages[0].method if pages else "text", extraction_version="1",
    )
    sources = [entry]
    if parent_version is not None:
        from study_companion.contracts import DomainError
        from study_companion.knowledge.dedup import deduplicate_knowledge

        parent, manifest = store.load_version(dataset_id, catalog_version, parent_version, verify_sources=True)
        if any(entry.source_id in {item.source_id, item.relative_path} or
               entry.relative_path in {item.source_id, item.relative_path} for item in manifest.source_files):
            raise DomainError("SOURCE_ID_DUPLICATE", "Source already exists in parent manifest")
        existing_ids = {point.kp_id for point in parent.points}
        if existing_ids & {point.kp_id for point in kb.points}:
            raise DomainError("CANDIDATE_ID_DUPLICATE", "Imported point IDs conflict with parent")
        imported = kb
        kb = parent.model_copy(deep=True, update={"kb_version": kb_version, "status": "draft", "created_at": None})
        kb.points.extend(imported.points)
        kb.relations.extend(imported.relations)
        kb.annotations.extend(imported.annotations)
        kb.merge_candidates.extend(imported.merge_candidates)
        kb = deduplicate_knowledge(kb)
        sources = [*manifest.source_files, entry]
    return store.save_draft(dataset_id, catalog_version, kb, parent_version=parent_version, source_files=sources)


def save(kb: KnowledgeBase, pages: list[PageRecord], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "knowledge_base.json").write_text(
        json.dumps(kb.model_dump(), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8", newline="\n"
    )
    # Page text stays in memory; source pages can be reread when validating evidence.
