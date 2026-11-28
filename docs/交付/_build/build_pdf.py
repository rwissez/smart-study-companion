"""生成《湛知｜智慧学伴设计方案》PDF（v2.0）。

流程：pandoc GFM -> HTML5（片段） -> 套打印 CSS -> 无头 Edge 打印 A4 PDF
-> PyMuPDF 加页码/元数据 -> 输出 docs/交付/设计方案_v2.0.pdf，并做交付自检。

用法（仓库根目录，用装有 PyMuPDF 的 python 执行）：
    python "docs/交付/_build/build_pdf.py"
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parents[3]
OUT_DIR = ROOT / "docs" / "交付"
BUILD = Path(__file__).resolve().parent
SRC = OUT_DIR / "设计方案_v2.0.md"
CSS = BUILD / "print.css"
BODY_HTML = BUILD / "design_v2.body.html"
FULL_HTML = BUILD / "design_v2.html"
RAW_PDF = BUILD / "design_v2.raw.pdf"
FINAL_PDF = OUT_DIR / "设计方案_v2.0.pdf"
PROFILE = BUILD / "edge-profile"

EDGE_CANDIDATES = [
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
]
PANDOC = shutil.which("pandoc") or r"C:\Users\LEGION\AppData\Local\Pandoc\pandoc.exe"
PAGE_FONT = Path(r"C:\Windows\Fonts\msyh.ttc")

# A4 595pt 宽，左右边距各 20mm≈56.7pt，正文右边界约 538pt；留 2pt 容差。
CONTENT_RIGHT = 540.0

EXPECTED_HEADINGS = [
    "摘要",
    "1 需求分析",
    "2 方案设计",
    "3 功能说明",
    "4 结果验证",
    "5 创新点",
    "6 使用平台、完成方式与数据范围",
    "7 关键结果依据",
    "8 已知限制",
]

# 按表头文字给每张表固定列宽（百分比），避免自动布局把窄列压成两三个字一行。
TABLE_WIDTHS: dict[tuple[str, ...], tuple[int, ...]] = {
    ("赛题要求", "系统交出什么", "评委怎么核"): (15, 45, 40),
    ("环节", "谁定值", "谁释义"): (42, 30, 28),
    ("步骤", "做什么", "40 人数据实测"): (14, 56, 30),
    ("评委会问", "系统怎么回答", "在哪能核"): (30, 42, 28),
    ("页面", "回答什么问题", "打开后看到什么"): (14, 39, 47),
    ("能做什么", "教师", "学生"): (46, 27, 27),
    ("组件", "做什么", "检查什么"): (17, 24, 59),
    ("包内文件", "装了什么", "对应哪道题"): (46, 38, 16),
    ("产出", "数量", "一句话说明"): (22, 14, 64),
    ("对象", "问题与处置"): (28, 72),
    ("层次", "实测", "证明什么"): (20, 35, 45),
    ("项目", "实测"): (30, 70),
    ("结论", "依据", "怎么核"): (34, 28, 38),
    ("限制", "具体表现", "怎么理解"): (16, 50, 34),
    ("类型", "学生", "节点", "主要依据"): (13, 9, 8, 70),
}


def mark_code_paragraphs(body: str) -> str:
    """含行内代码的段落改为左对齐，避免两端对齐把字距拉散。"""
    return re.sub(r"<p>(?=(?:(?!</p>).)*?<code>)", '<p class="code-p">', body, flags=re.S)


def tune_tables(body: str) -> str:
    """给表格注入 colgroup 固定列宽，返回 HTML 与未命中表头的清单。"""
    missed: list[tuple[str, ...]] = []

    def repl(match: re.Match[str]) -> str:
        table = match.group(0)
        head = re.search(r"<thead>(.*?)</thead>", table, re.S)
        if head is None:
            return table
        headers = tuple(
            re.sub(r"<[^>]+>", "", cell).replace("\xa0", " ").strip()
            for cell in re.findall(r"<th[^>]*>.*?</th>", head.group(1), re.S)
        )
        widths = TABLE_WIDTHS.get(headers)
        if widths is None:
            missed.append(headers)
            return table
        cols = "".join(f'<col style="width:{w}%" />' for w in widths)
        rows = len(re.findall(r"<tr>", table))
        # 长表允许跨页（表头会重复），短表整表保持在同一页。
        break_rule = "break-inside:auto;page-break-inside:auto" if rows > 9 else "break-inside:avoid"
        return table.replace(
            "<table>",
            f'<table style="table-layout:fixed;{break_rule}">\n<colgroup>{cols}</colgroup>',
            1,
        )

    result = re.sub(r"<table>.*?</table>", repl, body, flags=re.S)
    total = len(re.findall(r"<table[ >]", result))
    print(f"tables_total={total} tables_fixed={total - len(missed)} tables_auto={len(missed)}")
    for headers in missed:
        print("  auto-layout table: " + " | ".join(headers))
    return result


KEY_FACTS = [
    "803,771",
    "799",
    "841",
    "82",
    "226,140",
    "159",
    "160",
    "37",
]


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    print("+ " + " ".join(f'"{c}"' if " " in str(c) else str(c) for c in cmd))
    return subprocess.run(cmd, check=True, **kwargs)


def build_html() -> None:
    body = run(
        [PANDOC, str(SRC), "-f", "gfm", "-t", "html5", "--wrap=none"],
        capture_output=True,
    ).stdout.decode("utf-8")
    body = mark_code_paragraphs(body)
    body = tune_tables(body)
    css = CSS.read_text(encoding="utf-8")
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>湛知｜智慧学伴设计方案</title>
<style>
{css}
</style>
</head>
<body>
{body}
</body>
</html>
"""
    BODY_HTML.write_text(body, encoding="utf-8")
    FULL_HTML.write_text(html, encoding="utf-8")


def print_pdf() -> None:
    edge = next((p for p in EDGE_CANDIDATES if p.exists()), None)
    if edge is None:
        raise RuntimeError("未找到 Microsoft Edge")
    if RAW_PDF.exists():
        RAW_PDF.unlink()
    PROFILE.mkdir(parents=True, exist_ok=True)
    uri = FULL_HTML.resolve().as_uri()
    run(
        [
            str(edge),
            "--headless=new",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            f"--user-data-dir={PROFILE}",
            "--allow-file-access-from-files",
            "--no-pdf-header-footer",
            "--run-all-compositor-stages-before-draw",
            f"--print-to-pdf={RAW_PDF}",
            uri,
        ],
        capture_output=True,
    )
    if not RAW_PDF.exists() or RAW_PDF.stat().st_size < 10_000:
        raise RuntimeError("Edge 未生成有效 PDF")


def add_outline(doc: fitz.Document) -> int:
    """按 Markdown 标题生成 PDF 书签，方便评委跳转。"""
    headings: list[tuple[int, str]] = []
    for line in SRC.read_text(encoding="utf-8").splitlines():
        if line.startswith("### "):
            headings.append((2, line[4:].strip()))
        elif line.startswith("## "):
            headings.append((1, line[3:].strip()))

    toc: list[list] = []
    cursor = 0
    for level, title in headings:
        page_no = None
        for i in range(cursor, doc.page_count):
            if doc[i].search_for(title):
                page_no = i
                break
        if page_no is None:
            continue
        cursor = page_no
        toc.append([level, title, page_no + 1])
    doc.set_toc(toc)
    return len(toc)


def stamp(raw: Path) -> int:
    doc = fitz.open(raw)
    total = doc.page_count
    for index, page in enumerate(doc, start=1):
        page.wrap_contents()
        page.insert_font(fontname="pgnum", fontfile=str(PAGE_FONT))
        label = f"第 {index} 页 / 共 {total} 页"
        width = fitz.Font(fontfile=str(PAGE_FONT)).text_length(label, 8.5)
        x = (page.rect.width - width) / 2
        y = page.rect.height - 13 * 72 / 25.4
        page.insert_text(
            (x, y),
            label,
            fontname="pgnum",
            fontsize=8.5,
            color=(0.0, 0.0, 0.0),
        )
    doc.set_metadata(
        {
            "title": "湛知｜智慧学伴设计方案",
            "subject": "智慧学伴赛题设计方案 v2.0（2026-11-28）",
            "creator": "pandoc + Microsoft Edge (headless) + PyMuPDF",
            "producer": "pandoc + Microsoft Edge (headless) + PyMuPDF",
            "keywords": "湛知, 智慧学伴, 设计方案, 学情分析, 学习方案",
        }
    )
    marks = add_outline(doc)
    print(f"outline_entries={marks}")
    doc.subset_fonts()
    if FINAL_PDF.exists():
        FINAL_PDF.unlink()
    doc.save(FINAL_PDF, garbage=4, deflate=True)
    doc.close()
    return total


def audit() -> int:
    """交付自检：页数、中文、标题、关键数字、横向溢出。"""
    check = fitz.open(FINAL_PDF)
    problems: list[str] = []

    pages = check.page_count
    widths = {round(p.rect.width, 2) for p in check}
    heights = {round(p.rect.height, 2) for p in check}
    text = "\n".join(p.get_text() for p in check)
    text_norm = text.replace("\xa0", " ")
    fonts = sorted({f[3] for p in check for f in p.get_fonts()})

    print(f"pages={pages}")
    print(f"page_sizes={sorted(widths)}x{sorted(heights)}  (A4=595.28x841.89)")
    print(f"chars={len(text)}  chinese_chars={len(re.findall(r'[一-鿿]', text))}")
    print("fonts=" + ", ".join(fonts))

    if not 14 <= pages <= 18:
        problems.append(f"页数 {pages} 不在 14—18 页目标区间（硬上限 20）")
    if pages > 20:
        problems.append(f"页数 {pages} 超过 20 页上限")
    if len(widths) != 1:
        problems.append(f"页面宽度不一致：{sorted(widths)}")

    for heading in EXPECTED_HEADINGS:
        page_no = next((i + 1 for i, p in enumerate(check) if p.search_for(heading)), None)
        print(f"section[{heading}]={page_no}")
        if page_no is None:
            problems.append(f"缺少章节标题：{heading}")

    for fact in KEY_FACTS:
        print(f"contains[{fact}]={fact in text_norm}")
        if fact not in text_norm:
            problems.append(f"关键数据未出现在 PDF：{fact}")

    # 标题前不应有项目符号：正文未使用列表，出现 •/·/▪ 即视为异常。
    bullets = [ch for ch in "•·▪◦‣" if ch in text]
    print(f"bullet_chars={bullets}")
    if bullets:
        problems.append(f"出现项目符号字符：{bullets}")

    # 横向溢出：任何文本块右边界超过正文右边界。
    overflow: list[tuple[int, float, str]] = []
    for i, page in enumerate(check, start=1):
        for block in page.get_text("blocks"):
            x0, y0, x1, y1, content = block[0], block[1], block[2], block[3], block[4]
            if x1 > CONTENT_RIGHT + 1:
                snippet = content.strip().replace("\n", " ")[:40]
                overflow.append((i, round(x1, 1), snippet))
    print(f"overflow_blocks={len(overflow)}")
    for item in overflow[:8]:
        print(f"  page {item[0]} x1={item[1]} {item[2]}")
    if overflow:
        problems.append(f"{len(overflow)} 个文本块横向溢出正文区")

    per_page = [len(p.get_text().strip()) for p in check]
    print(f"per_page_chars={per_page}")
    short = [(i + 1, n) for i, n in enumerate(per_page) if n < 200]
    print(f"short_pages={short}")
    if short:
        problems.append(f"内容过少的页面：{short}")

    empty = [i + 1 for i, p in enumerate(check) if not p.get_text().strip()]
    if empty:
        problems.append(f"空白页：{empty}")

    check.close()

    if problems:
        print("AUDIT=FAIL")
        for p in problems:
            print("  - " + p)
        return 1
    print("AUDIT=PASS")
    return 0


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    build_html()
    print_pdf()
    stamp(RAW_PDF)
    print(f"OUTPUT={FINAL_PDF}")
    print(f"size={FINAL_PDF.stat().st_size}")
    return audit()


if __name__ == "__main__":
    raise SystemExit(main())
