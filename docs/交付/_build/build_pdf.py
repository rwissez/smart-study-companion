"""生成《湛知｜智慧学伴设计方案》PDF。

流程：pandoc GFM -> HTML5（片段） -> 套打印 CSS -> 无头 Edge 打印 A4 PDF
-> PyMuPDF 加页码/元数据 -> 输出 docs/交付/设计方案_v1.0.pdf。

用法（仓库根目录）：
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
SRC = OUT_DIR / "设计方案_v1.0.md"
CSS = BUILD / "print.css"
BODY_HTML = BUILD / "design.body.html"
FULL_HTML = BUILD / "design.html"
RAW_PDF = BUILD / "design.raw.pdf"
FINAL_PDF = OUT_DIR / "设计方案_v1.0.pdf"
PROFILE = BUILD / "edge-profile"

EDGE_CANDIDATES = [
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
]
PANDOC = shutil.which("pandoc") or r"C:\Users\LEGION\AppData\Local\Pandoc\pandoc.exe"
PAGE_FONT = Path(r"C:\Windows\Fonts\msyh.ttc")


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    print("+ " + " ".join(f'"{c}"' if " " in str(c) else str(c) for c in cmd))
    return subprocess.run(cmd, check=True, **kwargs)


def build_html() -> None:
    body = run(
        [PANDOC, str(SRC), "-f", "gfm", "-t", "html5", "--wrap=none"],
        capture_output=True,
    ).stdout.decode("utf-8")
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


def stamp(raw: Path) -> tuple[int, list[float]]:
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
            color=(0.42, 0.47, 0.44),
        )
    doc.set_metadata(
        {
            "title": "湛知｜智慧学伴设计方案",
            "subject": "智慧学伴赛题设计方案 v1.0（2026-11-28）",
            "creator": "pandoc + Microsoft Edge (headless) + PyMuPDF",
            "producer": "pandoc + Microsoft Edge (headless) + PyMuPDF",
            "keywords": "湛知, 智慧学伴, 设计方案, A/B/C, 学情分析",
        }
    )
    doc.subset_fonts()
    if FINAL_PDF.exists():
        FINAL_PDF.unlink()
    doc.save(FINAL_PDF, garbage=4, deflate=True)
    doc.close()

    check = fitz.open(FINAL_PDF)
    pages = check.page_count
    widths = [round(p.rect.width, 2) for p in check]
    heights = [round(p.rect.height, 2) for p in check]
    text = "\n".join(p.get_text() for p in check)
    text_norm = text.replace("\xa0", " ")
    fonts = sorted({f[3] for p in check for f in p.get_fonts()})
    print(f"pages={pages}")
    print(f"page_size_pt={widths[0]}x{heights[0]}  (A4=595.28x841.89)")
    cn_count = len(re.findall(r"[\u4e00-\u9fff]", text))
    print(f"chars={len(text)}  chinese_chars={cn_count}")
    print("fonts=" + ", ".join(fonts))
    for needle in [
        "湛知｜智慧学伴设计方案",
        "规则定值，模型释义",
        "当前限制与未完成项",
        "22 分 03",
        "第 1 页 / 共",
    ]:
        print(f"contains[{needle}]={needle in text_norm}")
    for heading in [
        "摘要",
        "1 需求分析",
        "2 方案设计",
        "3 功能说明",
        "4 结果验证",
        "5 创新点",
        "6 使用平台、完成方式与数据范围",
        "7 当前限制与未完成项",
    ]:
        page_no = next((i + 1 for i, p in enumerate(check) if p.search_for(heading)), None)
        print(f"section[{heading}]={page_no}")
    short = [(i + 1, len(p.get_text().strip())) for i, p in enumerate(check) if len(p.get_text().strip()) < 120]
    print(f"short_pages={short}")
    print(f"per_page_chars={[len(p.get_text().strip()) for p in check]}")
    check.close()
    return pages, widths


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    build_html()
    print_pdf()
    pages, _ = stamp(RAW_PDF)
    print(f"OUTPUT={FINAL_PDF}")
    print(f"OK pages={pages} size={FINAL_PDF.stat().st_size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
