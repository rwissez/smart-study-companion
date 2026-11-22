"""S0 采集：把 PDF 逐页变成 PageRecord。

- 有文字层的 PDF（教材切片）：pypdf 抽文字，文本统一做 NFKC（全角数字/标点转半角）。
- 无文字层的扫描 PDF（课标）：本地离线 OCR（RapidOCR，可选依赖），记录每页置信度。
OCR 不联网：识别发生在本机，页面图像不外发。依赖未安装时给出明确提示，不静默跳过。
"""

from __future__ import annotations

import io
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from pypdf import PdfReader

FOOTER_MARK = "仅供个人学习使用"
LOW_CONFIDENCE = 0.90


@dataclass
class PageRecord:
    file_page: int
    text: str
    method: str  # text / ocr
    ocr_confidence: float | None = None
    issues: list[str] = field(default_factory=list)


def normalize_text(text: str) -> str:
    """NFKC：全角→半角。字体映射错乱的字符（如 狓）保持原样，不猜测改写。"""
    return unicodedata.normalize("NFKC", text)


class PageExtractor(Protocol):
    def extract(self, pdf_path: Path, pages: list[int] | None = None) -> list[PageRecord]: ...


class PyPdfExtractor:
    """读取文字层。"""

    def extract(self, pdf_path: Path, pages: list[int] | None = None) -> list[PageRecord]:
        reader = PdfReader(str(pdf_path))
        wanted = pages or list(range(1, len(reader.pages) + 1))
        records: list[PageRecord] = []
        for n in wanted:
            raw = reader.pages[n - 1].extract_text() or ""
            lines = [ln.strip() for ln in raw.split("\n") if ln.strip()]
            body = [ln for ln in lines if FOOTER_MARK not in ln]
            records.append(PageRecord(file_page=n, text=normalize_text("\n".join(body)), method="text"))
        return records


class RapidOcrExtractor:
    """本地离线 OCR，用于扫描页。需要安装 rapidocr-onnxruntime（可选依赖）。"""

    def __init__(self) -> None:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as exc:  # pragma: no cover - 取决于环境
            raise RuntimeError(
                "未安装离线 OCR：请执行 `uv run --with rapidocr-onnxruntime ...` 或将其加入可选依赖"
            ) from exc
        self._ocr = RapidOCR()

    def extract(self, pdf_path: Path, pages: list[int] | None = None) -> list[PageRecord]:
        reader = PdfReader(str(pdf_path))
        wanted = pages or list(range(1, len(reader.pages) + 1))
        records: list[PageRecord] = []
        for n in wanted:
            page = reader.pages[n - 1]
            images = list(page.images)
            if not images:
                records.append(PageRecord(n, "", "ocr", None, ["页面没有可识别的图像"]))
                continue
            result, _ = self._ocr(images[0].data)
            result = result or []
            lines = [normalize_text(item[1]) for item in result]
            conf = [float(item[2]) for item in result]
            avg = round(sum(conf) / len(conf), 3) if conf else None
            issues = []
            if avg is None:
                issues.append("OCR 未识别到文字")
            elif avg < LOW_CONFIDENCE or min(conf) < 0.6:
                issues.append(f"OCR 置信度偏低（均值{avg}，最低{round(min(conf), 3)}），需人工看图核对")
            records.append(PageRecord(n, "\n".join(lines), "ocr", avg, issues))
        return records


def has_text_layer(pdf_path: Path, probe_pages: int = 3) -> bool:
    reader = PdfReader(str(pdf_path))
    for pg in reader.pages[:probe_pages]:
        if len((pg.extract_text() or "").strip()) > 30:
            return True
    return False


def pick_extractor(pdf_path: Path) -> PageExtractor:
    """有文字层用 pypdf，否则用离线 OCR。"""
    return PyPdfExtractor() if has_text_layer(pdf_path) else RapidOcrExtractor()


def read_pdf(pdf_path: Path, pages: list[int] | None = None) -> list[PageRecord]:
    return pick_extractor(pdf_path).extract(pdf_path, pages)


__all__ = [
    "PageExtractor",
    "PageRecord",
    "PyPdfExtractor",
    "RapidOcrExtractor",
    "has_text_layer",
    "normalize_text",
    "pick_extractor",
    "read_pdf",
    "io",
]
