"""章节切分与范围计划 ScopePlan（02 §6.1）。

只做**规则**判定，不调用模型：

* 章节切分读标题行（``第X章``/``第X篇``、``第X节``、``1.2`` 数字编号、页面顶部的
  短行标题）。标题行保留所在文件页，章节页范围不跨越标题边界。
* 范围判定只在标题与正文里匹配术语：主题名、指标名、课标编号（如 ``7.1.2``）。
  命中 → ``core``；只命中先修术语 → ``prerequisite``；命中明确超范围标记且没有任何
  主题/指标命中 → ``irrelevant``；其余一律 ``unknown``。**无命中不自动判无关**，
  因为字面不匹配不等于教材没讲（02 §6.1「不能因字面不匹配丢弃」）。

每条决策都带 ``matched_terms`` 与 ``reason``，教师可以核对"为什么判成这个角色"。
全部阈值与词表都是工程初始值，不是教学准确率保证。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from study_companion.knowledge.dedup import normalize_name
from study_companion.knowledge.pdf_reader import PdfDocument, is_usable

# --------------------------------------------------------------------------- #
# 标题规则（工程初始值）
# --------------------------------------------------------------------------- #

_CN_NUM = "一二三四五六七八九十百零〇0-9"
CHAPTER_RE = re.compile(rf"^第\s*[{_CN_NUM}]{{1,4}}\s*[章篇]")
SECTION_RE = re.compile(rf"^第\s*[{_CN_NUM}]{{1,4}}\s*节")
NUMBERED_RE = re.compile(r"^\d{1,2}(?:[.．]\d{1,2}){0,2}(?:\s|[、.．]|$)")
CURRICULUM_CODE_RE = re.compile(r"\d{1,2}(?:\.\d{1,3}){1,3}")

# 标题行的长度上限：超过这个长度的行按正文处理，避免把整段话当标题。
MAX_HEADING_CHARS = 30
# 短行标题的长度上限（不含编号的纯标题更短）。
MAX_SHORT_TITLE_CHARS = 20
# 只在页面顶部若干非空行里找标题，正文段落里的同名行不算。
HEADING_SCAN_LINES = 3
# 标题行末尾出现这些标点，视为句子而不是标题。
SENTENCE_MARKS = "。；，、：？！;,?"

# 先修术语（工程初始值）：只用于把「复习/前置」类章节标成 prerequisite 候选。
PREREQUISITE_TERMS: tuple[str, ...] = (
    "复习",
    "回顾",
    "预备知识",
    "先修",
    "已学",
    "前面学过",
    "小学学过",
    "前置知识",
    "知识回顾",
)

# 明确超范围标记（工程初始值）：只有命中这里、且没有任何主题/指标命中，才判 irrelevant。
OUT_OF_SCOPE_TERMS: tuple[str, ...] = (
    "附录",
    "参考答案",
    "习题答案",
    "教师用书",
    "版权页",
    "致谢",
    "参考文献",
    "索引",
)

ROLE_CORE = "core"
ROLE_PREREQUISITE = "prerequisite"
ROLE_IRRELEVANT = "irrelevant"
ROLE_UNKNOWN = "unknown"


@dataclass
class Chapter:
    chapter_id: str
    title: str
    start_page: int
    end_page: int
    text: str
    # (文件页, 起始偏移, 结束偏移)，按页记录，供候选把字符偏移还原成页码。
    page_spans: list[tuple[int, int, int]] = field(default_factory=list)


@dataclass
class ScopeDecision:
    chapter_id: str
    role: str  # core / prerequisite / irrelevant / unknown
    reason: str
    matched_terms: list[str]


@dataclass
class ScopePlan:
    chapters: list[Chapter]
    decisions: list[ScopeDecision]
    limitations: list[str]


def _fold(text: str) -> str:
    """匹配用的归一化：NFKC、去空白、大小写折叠（复用既有 dedup.normalize_name）。"""

    return normalize_name(text)


def _first_nonempty_lines(text: str, limit: int) -> list[str]:
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    return lines[:limit]


def detect_heading_kind(line: str) -> str | None:
    """判定单行标题类型：chapter / section / numbered / short；不是标题返回 None。

    「短行标题」只在页面顶部判定（见 ``_detect_heading``），单独一行调用它时
    只表示"长得像标题"，不能直接当章节边界用。
    """

    line = line.strip()
    if not line or len(line) > MAX_HEADING_CHARS:
        return None
    if line[-1] in SENTENCE_MARKS:
        return None
    if CHAPTER_RE.match(line):
        return "chapter"
    if SECTION_RE.match(line):
        return "section"
    if NUMBERED_RE.match(line) and any("\u4e00" <= char <= "\u9fff" for char in line):
        return "numbered"
    if (
        2 <= len(line) <= MAX_SHORT_TITLE_CHARS
        and not any(mark in line for mark in SENTENCE_MARKS)
        and not line.isdigit()
    ):
        return "short"
    return None


def is_structural_heading(line: str) -> bool:
    """章/节/数字编号这类有明确结构的标题。

    与「短行标题」不同，这种判定不依赖页面位置，可以被正文分句过程直接当作
    行边界使用（``candidates`` 需要它，避免把标题行粘进定义句）。
    """

    return detect_heading_kind(line) in {"chapter", "section", "numbered"}


def _detect_heading(text: str) -> tuple[str, str] | None:
    """在页面顶部的非空行里找标题，返回 (kind, title)。"""

    for line in _first_nonempty_lines(text, HEADING_SCAN_LINES):
        kind = detect_heading_kind(line)
        if kind is not None:
            return kind, line
    return None


def _chapter_text(pages: list, start_page: int, end_page: int) -> tuple[str, list[tuple[int, int, int]]]:
    """把页范围内的可用页拼成章节正文，同时记录每页的字符区间。"""

    chunks: list[str] = []
    spans: list[tuple[int, int, int]] = []
    offset = 0
    for page in pages:
        if page.page < start_page or page.page > end_page:
            continue
        if not is_usable(page):
            continue
        chunk = page.text.strip()
        if not chunk:
            continue
        if chunks:
            chunks.append("\n")
            offset += 1
        chunks.append(chunk)
        spans.append((page.page, offset, offset + len(chunk)))
        offset += len(chunk)
    return "".join(chunks), spans


def split_chapters(document: PdfDocument) -> list[Chapter]:
    """按标题行切分章节；标题行所在页就是该章起始页。

    没有识别到任何标题时，整份文档作为一章（标题取「全文（未识别章节标题）」），
    由 ``plan_scope`` 在限制说明里提示。全篇没有可用文本层页面时返回空列表，
    不伪造章节。
    """

    pages = [page for page in document.pages if page.page >= 1]
    if not pages:
        return []

    boundaries: list[tuple[int, str]] = []  # (起始页, 标题)
    for page in pages:
        if not page.text.strip():
            continue
        heading = _detect_heading(page.text)
        if heading is None:
            continue
        if boundaries and boundaries[-1][0] == page.page:
            # 同一页已有标题（例如章标题与节标题同页）：以先出现的行为章边界。
            continue
        boundaries.append((page.page, heading[1]))

    last_page = max(page.page for page in pages)
    chapters: list[Chapter] = []

    if not boundaries:
        text, spans = _chapter_text(pages, 1, last_page)
        if not text.strip():
            return []
        chapters.append(
            Chapter(
                chapter_id="ch-001",
                title="全文（未识别章节标题）",
                start_page=1,
                end_page=last_page,
                text=text,
                page_spans=spans,
            )
        )
        return chapters

    first_heading_page = boundaries[0][0]
    if first_heading_page > 1:
        text, spans = _chapter_text(pages, 1, first_heading_page - 1)
        if text.strip():
            chapters.append(
                Chapter(
                    chapter_id="ch-000",
                    title="（首个标题之前的内容）",
                    start_page=1,
                    end_page=first_heading_page - 1,
                    text=text,
                    page_spans=spans,
                )
            )

    for index, (start_page, title) in enumerate(boundaries):
        next_start = boundaries[index + 1][0] if index + 1 < len(boundaries) else last_page + 1
        end_page = min(next_start - 1, last_page)
        text, spans = _chapter_text(pages, start_page, end_page)
        chapters.append(
            Chapter(
                chapter_id=f"ch-{index + 1:03d}",
                title=title,
                start_page=start_page,
                end_page=end_page,
                text=text,
                page_spans=spans,
            )
        )
    return chapters


def _catalog_terms(topics: list[str], indicators: list[str]) -> list[tuple[str, str]]:
    """把主题名、指标名、课标编号整理成待匹配术语，保留来源类别。"""

    terms: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(raw: str, kind: str) -> None:
        term = raw.strip()
        if len(term) < 2:
            return
        key = _fold(term)
        if not key or key in seen:
            return
        seen.add(key)
        terms.append((term, kind))

    for topic in topics:
        add(topic, "主题")
    for indicator in indicators:
        add(indicator, "指标")
        for code in CURRICULUM_CODE_RE.findall(indicator):
            add(code, "课标编号")
    for topic in topics:
        for code in CURRICULUM_CODE_RE.findall(topic):
            add(code, "课标编号")
    return terms


def _match_terms(haystack: str, terms: list[tuple[str, str]]) -> list[str]:
    return [term for term, _kind in terms if _fold(term) and _fold(term) in haystack]


def plan_scope(
    chapters: list[Chapter], *, topics: list[str], indicators: list[str]
) -> ScopePlan:
    """逐章节做术语匹配，给出 core/prerequisite/irrelevant/unknown 与理由。"""

    terms = _catalog_terms(topics, indicators)
    prerequisite_terms = [(term, "先修") for term in PREREQUISITE_TERMS]
    out_of_scope_terms = [(term, "超范围") for term in OUT_OF_SCOPE_TERMS]

    decisions: list[ScopeDecision] = []
    counts = {ROLE_CORE: 0, ROLE_PREREQUISITE: 0, ROLE_IRRELEVANT: 0, ROLE_UNKNOWN: 0}

    for chapter in chapters:
        if not terms:
            # 没有课程术语就没有判定依据：一律 unknown，不借先修/超范围词替代范围判断。
            decisions.append(
                ScopeDecision(
                    chapter_id=chapter.chapter_id,
                    role=ROLE_UNKNOWN,
                    reason="未提供主题或指标术语，无法判定范围，保留 unknown 供教师或模型复核",
                    matched_terms=[],
                )
            )
            counts[ROLE_UNKNOWN] += 1
            continue

        haystack = _fold(f"{chapter.title}\n{chapter.text}")
        core_hits = _match_terms(haystack, terms)
        prerequisite_hits = _match_terms(haystack, prerequisite_terms)
        out_of_scope_hits = _match_terms(haystack, out_of_scope_terms)

        if core_hits:
            role = ROLE_CORE
            reason = "标题或正文命中主题/指标/课标编号术语：" + "、".join(core_hits[:5])
        elif prerequisite_hits:
            role = ROLE_PREREQUISITE
            reason = (
                "仅命中先修术语：" + "、".join(prerequisite_hits[:5])
                + "；未命中主题或指标，按前置候选保留，需教师确认它支撑哪条核心知识"
            )
        elif out_of_scope_hits and not chapter.text.strip():
            role = ROLE_IRRELEVANT
            reason = (
                "命中超范围标记：" + "、".join(out_of_scope_hits[:5])
                + "；且没有可用正文，判为无关（不是质量失败）"
            )
        elif out_of_scope_hits:
            role = ROLE_IRRELEVANT
            reason = (
                "命中超范围标记：" + "、".join(out_of_scope_hits[:5])
                + "；未命中任何主题或指标，判为无关（不是质量失败）"
            )
        elif not chapter.text.strip():
            role = ROLE_UNKNOWN
            reason = "该章节没有可用文本层页面，无法判定，保留 unknown（见 pdf_reader 的 limitations）"
        else:
            role = ROLE_UNKNOWN
            reason = (
                "未命中主题、指标或课标编号术语；字面不匹配不等于无关，"
                "保留 unknown 交由教师或模型复核"
            )

        # 命中术语按主题/指标顺序输出，去重后保持稳定，便于比对。
        matched = list(dict.fromkeys([*core_hits, *prerequisite_hits]))
        if role == ROLE_IRRELEVANT:
            matched = list(dict.fromkeys([*out_of_scope_hits, *matched]))
        decisions.append(
            ScopeDecision(
                chapter_id=chapter.chapter_id,
                role=role,
                reason=reason,
                matched_terms=matched,
            )
        )
        counts[role] += 1

    limitations: list[str] = []
    if not chapters:
        limitations.append("未切分出任何章节，也无法做范围判定；请检查 PDF 文本层")
    if not terms:
        limitations.append("未提供主题或指标术语，全部章节按 unknown 保留，未判无关")
    if len(chapters) == 1:
        limitations.append("只切出 1 个章节，可能未识别到章节标题，全部正文按单章处理")
    if counts[ROLE_PREREQUISITE]:
        limitations.append(
            f"{counts[ROLE_PREREQUISITE]} 个章节只命中先修术语，按前置候选保留，需教师确认适用范围"
        )
    if counts[ROLE_UNKNOWN]:
        limitations.append(
            f"{counts[ROLE_UNKNOWN]} 个章节未命中主题或指标术语，保留 unknown，未判无关"
        )
    return ScopePlan(chapters=chapters, decisions=decisions, limitations=limitations)



def page_stats(document: Any, plan: "ScopePlan") -> dict[str, int]:
    """按范围计划统计页面分母：总量、相关、可用相关（02 §12）。

    只统计真实存在的页；不可用页计入 recovered=0 并在返回里给出差距。
    """

    total_pages = getattr(document, "page_count", 0) or len(getattr(document, "pages", []) or [])
    usable_pages = {
        page.page
        for page in getattr(document, "pages", []) or []
        if getattr(page, "quality", "") == "usable"
    }
    relevant_pages: set[int] = set()
    for decision in plan.decisions:
        if decision.role not in {"core", "prerequisite"}:
            continue
        chapter = next(
            (item for item in plan.chapters if item.chapter_id == decision.chapter_id), None
        )
        if chapter is None:
            continue
        relevant_pages.update(range(chapter.start_page, chapter.end_page + 1))
    usable_relevant = relevant_pages & usable_pages
    return {
        "source_files": 1 if total_pages else 0,
        "total_pages": total_pages,
        "relevant_pages": len(relevant_pages),
        "usable_relevant_pages": len(usable_relevant),
        "recovered_pages": 0,
        "unusable_relevant_pages": len(relevant_pages - usable_pages),
    }

__all__ = [
    "OUT_OF_SCOPE_TERMS",
    "PREREQUISITE_TERMS",
    "Chapter",
    "ScopeDecision",
    "ScopePlan",
    "detect_heading_kind",
    "is_structural_heading",
    "page_stats",
    "plan_scope",
    "split_chapters",
]
