"""教材 PDF 的文本层读取与逐页质量判定（02 §5）。

本模块只做**文本层**：pypdf 抽文字、逐页判定可用性。不做 OCR、不调用模型、
不渲染页面图像、不猜写乱码字符。扫描页或字体映射坏掉的页如实标为不可用并写进
``limitations``，交给后续的 OCR / 视觉恢复环节，绝不伪造正文。

判定规则（工程初始值，可配置，不是教学准确率保证）：

* ``MIN_TEXT_CHARS = 30``：非空白字符少于 30 视为低文字量（对齐
  ``KnowledgeConfig.min_text_chars``）。封面、图页可能正常，所以只降低可用级别，
  不直接销毁内容。
* ``GARBLED_RATIO_LIMIT = 0.15``：乱码字符占比达到 15% 判 ``garbled``。
  乱码 = 不属于「ASCII 可打印 / 中日韩汉字与标点 / 全角形式 / 常用数学与几何符号」
  的字符，包括控制符、U+FFFD 替换符、私用区与不可打印区。
* ``GARBLED_MIN_COUNT = 3``：占比之外还要求至少 3 个乱码字符，避免 5 个字符的小页
  因为 1 个异常符号就被判成乱码。
* 非空白字符为 0 → ``image_only``（无文本层，通常是扫描页）。这一判定只看抽出的
  文本，不读内嵌图像，所以不声称页面里有没有图。

归一化只做**全角转半角**（``str.translate``，1 个字符对 1 个字符），这样字符偏移
在原文与归一化文本之间保持一一对应，候选的 ``char_start``/``char_end`` 才有意义。
不使用 ``unicodedata.normalize("NFKC")``：它会把 ``²`` 变成 ``2``、``½`` 变成
``1⁄2``，破坏上下标语义并改变字符串长度（02 §5.2）。
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from pypdf import PdfReader, PdfWriter

from study_companion.contracts import DomainError
from study_companion.knowledge.extract import FOOTER_MARK

# 工程初始阈值：来源见模块文档与 02 §3 的 KnowledgeConfig 默认值。
MIN_TEXT_CHARS = 30
GARBLED_RATIO_LIMIT = 0.15
GARBLED_MIN_COUNT = 3

# 全角 ASCII（U+FF01—U+FF5E）、全角空格及表意空格 → 半角。逐字符替换，长度不变。
_FULLWIDTH = str.maketrans(
    {chr(code): chr(code - 0xFEE0) for code in range(0xFF01, 0xFF5F)}
    | {0x3000: 0x20}
)

# 允许出现在教材文本里的字符区段：ASCII、拉丁补充符号、希腊字母、通用标点、
# 箭头、数学运算符、几何与带圈数字、CJK 汉字与标点、全角形式、竖排标点。
_ALLOWED_RANGES: tuple[tuple[int, int], ...] = (
    (0x0020, 0x007E),  # ASCII 可打印
    (0x00A0, 0x00FF),  # × ÷ ± ° ² ³ 等
    (0x0370, 0x03FF),  # 希腊字母
    (0x2000, 0x206F),  # 通用标点（引号、破折号、省略号）
    (0x2070, 0x209F),  # 上下标
    (0x2190, 0x21FF),  # 箭头
    (0x2200, 0x22FF),  # 数学运算符
    (0x2460, 0x24FF),  # 带圈数字
    (0x25A0, 0x25FF),  # 几何图形（◆ ▲ 等）
    (0x2E80, 0x2EFF),  # CJK 部首补充
    (0x3000, 0x303F),  # CJK 标点
    (0x3040, 0x30FF),  # 日文假名（教材夹注可能出现）
    (0x3100, 0x312F),  # 注音
    (0x3400, 0x4DBF),  # CJK 扩展 A
    (0x4E00, 0x9FFF),  # CJK 基本区
    (0xF900, 0xFAFF),  # CJK 兼容汉字
    (0xFE30, 0xFE4F),  # 竖排标点
    (0xFF00, 0xFFEF),  # 全角形式
)

QUALITY_USABLE = "usable"
QUALITY_SPARSE = "sparse"
QUALITY_IMAGE_ONLY = "image_only"
QUALITY_GARBLED = "garbled"

_QUALITY_TEXT = {
    QUALITY_USABLE: "可用",
    QUALITY_SPARSE: "文字量偏低",
    QUALITY_IMAGE_ONLY: "无文本层",
    QUALITY_GARBLED: "乱码占比过高",
}


@dataclass
class PageText:
    """一页的文本层结果。``page`` 是文件页，从 1 起，不推断印刷页码。"""

    page: int
    text: str
    char_count: int
    quality: str  # usable / sparse / image_only / garbled


@dataclass
class PdfDocument:
    file_id: str
    page_count: int
    pages: list[PageText]
    limitations: list[str]


def normalize_text(text: str) -> str:
    """只做全角 → 半角，长度与原文一致，保留数学符号与上下标。"""

    return text.translate(_FULLWIDTH)


def _is_allowed(char: str) -> bool:
    code = ord(char)
    return any(low <= code <= high for low, high in _ALLOWED_RANGES)


def char_count(text: str) -> int:
    """非空白字符数。"""

    return sum(1 for char in text if not char.isspace())


def garbled_ratio(text: str) -> float:
    """乱码字符占非空白字符的比例；没有非空白字符时为 0.0。"""

    counted = 0
    garbled = 0
    for char in text:
        if char.isspace():
            continue
        counted += 1
        if not _is_allowed(char):
            garbled += 1
    return 0.0 if counted == 0 else garbled / counted


def count_garbled(text: str) -> int:
    return sum(1 for char in text if not char.isspace() and not _is_allowed(char))


def classify_page_text(text: str) -> str:
    """按上面的阈值给出页面质量。判定顺序固定，结果可解释。"""

    counted = char_count(text)
    if counted == 0:
        return QUALITY_IMAGE_ONLY
    if count_garbled(text) >= GARBLED_MIN_COUNT and garbled_ratio(text) >= GARBLED_RATIO_LIMIT:
        return QUALITY_GARBLED
    if counted < MIN_TEXT_CHARS:
        return QUALITY_SPARSE
    return QUALITY_USABLE


def is_usable(page: PageText) -> bool:
    """只有 ``usable`` 页可以直接当作正文依据。"""

    return page.quality == QUALITY_USABLE and bool(page.text.strip())


def _clean_page_text(raw: str) -> str:
    """去掉空白行与页脚版权行，再统一全角字符。"""

    lines = [line.strip() for line in raw.split("\n") if line.strip()]
    body = [line for line in lines if FOOTER_MARK not in line]
    return normalize_text("\n".join(body))


def read_pdf(file_id: str, data: bytes) -> PdfDocument:
    """读取已登记 PDF 的文本层。

    ``data`` 是受控源解析后的原始字节（由 ``library/blobs`` 按 ``storage_key`` 读出），
    本函数不接受路径参数。整份文件无法解析时抛 ``DomainError``，不静默返回空文档。
    """

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            # 空口令有时能打开「仅限制编辑」的文件；打不开就明确失败，不伪造内容。
            try:
                reader.decrypt("")
            except Exception as exc:  # pragma: no cover - 取决于具体 PDF 加密方式
                raise DomainError(
                    "VALIDATION_ERROR", "PDF 已加密，无法读取文本层，需要教师提供可读原件"
                ) from exc
        raw_pages = list(reader.pages)
    except DomainError:
        raise
    except Exception as exc:
        raise DomainError("VALIDATION_ERROR", f"PDF 无法解析（{type(exc).__name__}）") from exc

    pages: list[PageText] = []
    limitations: list[str] = []
    for index, raw_page in enumerate(raw_pages, start=1):
        try:
            raw = raw_page.extract_text() or ""
        except Exception as exc:  # pragma: no cover - 取决于具体文件
            raw = ""
            limitations.append(f"第 {index} 页文本层抽取异常（{type(exc).__name__}），未填造内容")
        text = _clean_page_text(raw)
        counted = char_count(text)
        quality = classify_page_text(text)
        pages.append(PageText(page=index, text=text, char_count=counted, quality=quality))
        if quality != QUALITY_USABLE:
            limitations.append(
                f"第 {index} 页{_QUALITY_TEXT[quality]}"
                f"（{quality}，非空白字符 {counted}，乱码占比 {garbled_ratio(text):.2f}），"
                "未做 OCR，交由后续恢复环节处理"
            )

    if not pages:
        limitations.append("PDF 没有可读页面")
    return PdfDocument(
        file_id=file_id,
        page_count=len(pages),
        pages=pages,
        limitations=limitations,
    )


def page_count(data: bytes) -> int:
    """只读页数，不抽文本。供受控页读取接口做越界判断。"""

    try:
        return len(list(PdfReader(io.BytesIO(data)).pages))
    except Exception as exc:
        raise DomainError("VALIDATION_ERROR", f"PDF 无法解析（{type(exc).__name__}）") from exc


def extract_page_pdf(data: bytes, page: int) -> bytes:
    """把第 ``page`` 页（从 1 起）单独写成一份单页 PDF 字节。

    只返回该页，不返回全文；越界抛 ``IndexError``，由调用方转成 404。
    """

    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:
        raise DomainError("VALIDATION_ERROR", f"PDF 无法解析（{type(exc).__name__}）") from exc
    total = len(reader.pages)
    if page < 1 or page > total:
        raise IndexError(f"页码 {page} 超出 1—{total}")
    writer = PdfWriter()
    writer.add_page(reader.pages[page - 1])
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


__all__ = [
    "GARBLED_MIN_COUNT",
    "GARBLED_RATIO_LIMIT",
    "MIN_TEXT_CHARS",
    "PageText",
    "PdfDocument",
    "char_count",
    "classify_page_text",
    "count_garbled",
    "extract_page_pdf",
    "garbled_ratio",
    "is_usable",
    "normalize_text",
    "page_count",
    "read_pdf",
]
