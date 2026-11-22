"""教材 PDF 切片自动切分（半自动，供 A 人工核对后入库）。

定位（对应13号文档"教材切片"核对要求）：
- 解析 PDF 文字层，按 章/小节 切分为教材条目，生成 A 规划的 materials_index 草稿。
- 自动矫正页脚倒序数字（如 ３２１ → 123），并做相邻页连续性校验。
- 所有自动结果 verification_status 一律 unverified；A 核对后改 verified。
  未验证的页码不猜页数（01文档），页码缺失时 printed_page 为 null。
- 不直接写数据库；产出 JSON + 核对清单，走 A 的目录流程。

已知数据特征（B02 实测）：
- 页眉行为章标题（如"第十一章　不等式与不等式组"），每页重复。
- 小节标题以全角数字编号开头（如"１１．１ 不等式"）。
- 页脚数字为全角且逐字倒序（３２１ 实为 123），页脚还有固定的版权行。
- 变量字母存在字体映射错乱（狓=ｘ），保留原文不改写。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from pypdf import PdfReader

CHAPTER_RE = re.compile(r"^第[一二三四五六七八九十百]+章")
SECTION_RE = re.compile(r"^[０-９0-9]+[．.][０-９0-9]+(?:[．.][０-９0-9]+)?[\s　]")
FOOTER_MARK = "仅供个人学习使用"
# 页脚页码行：整行只有全角或半角数字（1-4位）
PAGE_NUMBER_LINE_RE = re.compile(r"^[０-９0-9]{1,4}$")


def _chapter_key(title: str) -> str:
    """章归一键：去掉空白差异（'第十二章数据' 与 '第十二章　数据' 视为同一章）。

    仅归并空白，不改写任何文字内容；原始写法保留在条目里供人工核对。
    """
    return re.sub(r"[\s\u3000]+", "", title)


def _to_halfwidth(text: str) -> str:
    """全角数字/点转半角（１２３ -> 123）。"""
    out = []
    for ch in text:
        code = ord(ch)
        if code == 0xFF0E or ch == "．":
            out.append(".")
        elif 0xFF10 <= code <= 0xFF19:
            out.append(chr(code - 0xFF10 + ord("0")))
        else:
            out.append(ch)
    return "".join(out)


@dataclass
class TextbookSection:
    """一个小节（切分单元）：标题、所属章、文件页范围、印刷页范围。"""

    chapter: str
    section: str
    start_file_page: int
    end_file_page: int
    printed_start: int | None
    printed_end: int | None
    content_preview: str = ""
    issues: list[str] = field(default_factory=list)

    def to_material_entry(self, source_pdf: str) -> dict:
        return {
            "material_type": "textbook",
            "chapter": self.chapter,
            "section": self.section,
            "file_pages": [self.start_file_page, self.end_file_page],
            "printed_pages": (
                [self.printed_start, self.printed_end]
                if self.printed_start is not None and self.printed_end is not None
                else None
            ),
            "content_preview": self.content_preview[:120],
            "source_locator": {
                "source_file": source_pdf,
                "pdf_page": self.start_file_page,
                "printed_page": str(self.printed_start) if self.printed_start is not None else None,
                "verification_status": "unverified",
            },
        }


def extract_page_number(lines: list[str]) -> str | None:
    """从页面行中取页脚页码行（版权行之上的纯数字行），返回原文。"""
    candidates = [ln.strip() for ln in lines if PAGE_NUMBER_LINE_RE.match(ln.strip())]
    if not candidates:
        return None
    # 多个候选时取最后一个（页脚在末尾，且上方可能有正文中的独立数字行）
    return candidates[-1]


def normalize_page_number(raw: str) -> int | None:
    """矫正页码：全角转半角后逐字反转。无法解析返回 None。"""
    half = _to_halfwidth(raw)
    try:
        return int(half[::-1])
    except ValueError:
        return None


def check_continuity(mapping: dict[int, int | None]) -> list[str]:
    """相邻文件页的印刷页应递增1；断档处生成待核对提示。"""
    issues: list[str] = []
    keys = sorted(mapping)
    for prev, curr in zip(keys, keys[1:]):
        a, b = mapping[prev], mapping[curr]
        if a is not None and b is not None and b != a + 1:
            issues.append(
                f"文件页{prev}->{curr}: 印刷页 {a}->{b} 不连续（差{b - a}），需人工核对"
            )
    return issues


def split_textbook(pdf_path: Path) -> dict:
    """切分一份教材切片 PDF，返回完整产出结构（JSON可序列化）。"""
    reader = PdfReader(str(pdf_path))
    pages_lines: list[list[str]] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        pages_lines.append([ln.strip() for ln in text.split("\n") if ln.strip()])

    # 1. 页码抽取与矫正
    raw_numbers: dict[int, str | None] = {}
    printed: dict[int, int | None] = {}
    for idx, lines in enumerate(pages_lines, start=1):
        body = [ln for ln in lines if FOOTER_MARK not in ln]
        raw = extract_page_number(body)
        raw_numbers[idx] = raw
        printed[idx] = normalize_page_number(raw) if raw else None

    continuity_issues = check_continuity(printed)
    reversed_flag = sum(1 for v in printed.values() if v is not None) >= len(pages_lines) * 0.6

    # 2. 章/节识别
    sections: list[TextbookSection] = []
    chapter_pages: list[tuple[str, int]] = []  # (章标题, 文件页)，含章级兜底
    current_chapter = ""
    for idx, lines in enumerate(pages_lines, start=1):
        body = [ln for ln in lines if FOOTER_MARK not in ln and not PAGE_NUMBER_LINE_RE.match(ln)]
        for ln in body:
            if CHAPTER_RE.match(ln) and len(ln) < 40:
                if _chapter_key(ln) != _chapter_key(current_chapter or ""):
                    current_chapter = ln
                    chapter_pages.append((ln, idx))
                break  # 页眉行只看第一条
        for ln in body:
            if SECTION_RE.match(ln) and len(ln) < 50:
                sections.append(
                    TextbookSection(
                        chapter=current_chapter,
                        section=ln,
                        start_file_page=idx,
                        end_file_page=idx,
                        printed_start=printed.get(idx),
                        printed_end=printed.get(idx),
                    )
                )
                break  # 每页最多认一个小节起点（简化：小节起点所在页）

    # 2b. 章级兜底：章标题首次出现的页面若无任何小节覆盖，生成章级条目
    sec_pages = {s.start_file_page for s in sections}
    for title, page in chapter_pages:
        if page not in sec_pages:
            sections.append(
                TextbookSection(
                    chapter=title,
                    section=f"{title}（章级条目，未识别到小节号）",
                    start_file_page=page,
                    end_file_page=page,
                    printed_start=printed.get(page),
                    printed_end=printed.get(page),
                )
            )
    sections.sort(key=lambda s: s.start_file_page)

    # 3. 合并同一小节跨页（相邻节之间的页归属前一节）
    merged: list[TextbookSection] = []
    for sec in sections:
        if merged and merged[-1].section == sec.section and sec.start_file_page == merged[-1].end_file_page + 1:
            merged[-1].end_file_page = sec.start_file_page
            merged[-1].printed_end = sec.printed_start
        else:
            merged.append(sec)

    # 4. 内容预览与问题标记
    continuity_by_page: dict[int, str] = {}
    for msg in continuity_issues:
        m = re.match(r"文件页(\d+)", msg)
        if m:
            continuity_by_page[int(m.group(1))] = msg
    for sec in merged:
        page_lines = pages_lines[sec.start_file_page - 1]
        body = [ln for ln in page_lines if FOOTER_MARK not in ln and ln != sec.section]
        sec.content_preview = " ".join(body[:5])
        if sec.printed_start is None:
            sec.issues.append("起始文件页页脚无页码")
        if sec.start_file_page != sec.end_file_page and sec.printed_end is None:
            sec.issues.append("结束文件页页脚无页码")
        for pg in (sec.start_file_page, sec.end_file_page):
            if pg in continuity_by_page:
                sec.issues.append(continuity_by_page[pg])

    # 5. 产出
    seen: set[str] = set()
    chapters_seen: list[str] = []
    for s in merged:
        key = _chapter_key(s.chapter)
        if key not in seen:
            seen.add(key)
            chapters_seen.append(s.chapter)
    page_mapping = {
        "file_page_to_printed": {str(k): v for k, v in printed.items()},
        "raw_footer_numbers": {str(k): v for k, v in raw_numbers.items()},
        "reversal_detected": reversed_flag,
        "continuity_issues": continuity_issues,
    }
    materials = [sec.to_material_entry(pdf_path.name) for sec in merged]
    return {
        "source_pdf": pdf_path.name,
        "total_file_pages": len(pages_lines),
        "chapters_seen": sorted(chapters_seen),
        "materials": materials,
        "page_mapping": page_mapping,
        "review_notes": {
            "sections_without_page_number": [
                s.section for s in merged if s.printed_start is None
            ],
            "page_continuity_issues": continuity_issues,
            "note": "全部条目 verification_status=unverified，需A逐条核对后置verified",
        },
    }
