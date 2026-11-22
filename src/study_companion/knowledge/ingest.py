"""把一份 PDF 变成知识库：采集 -> 页码矫正 -> 定位信息 -> 规则抽取。

页码矫正与小节识别复用 B 的 textbook_split，不复制其逻辑。
"""

from __future__ import annotations

import json
from pathlib import Path

from study_companion.analysis.textbook_split import split_textbook
from study_companion.knowledge.extract import PageRecord, read_pdf
from study_companion.knowledge.models import KnowledgeBase, Locator
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


def save(kb: KnowledgeBase, pages: list[PageRecord], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "knowledge_base.json").write_text(
        json.dumps(kb.model_dump(), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8", newline="\n"
    )
    (out_dir / "pages.json").write_text(
        json.dumps({str(p.file_page): p.text for p in pages}, ensure_ascii=False, indent=1),
        encoding="utf-8", newline="\n",
    )
