"""PDF 文本层读取与逐页质量判定（02 §5）。

测试用 pypdf 现场生成最小 PDF，不依赖任何外部教材文件；断言的是阈值与
"不可用页面只进 limitations、不伪造内容"这两条可解释的规则。
"""

from __future__ import annotations

from io import BytesIO

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from study_companion.contracts import DomainError
from study_companion.knowledge.pdf_reader import (
    GARBLED_RATIO_LIMIT,
    MIN_TEXT_CHARS,
    PageText,
    char_count,
    classify_page_text,
    count_garbled,
    extract_page_pdf,
    garbled_ratio,
    is_usable,
    normalize_text,
    page_count,
    read_pdf,
)

USABLE_TEXT = (
    "Rational numbers can be written as a fraction of two integers. "
    "This page carries enough characters to be treated as usable text layer."
)
SPARSE_TEXT = "Page two"


def _text_pdf(pages: list[str]) -> bytes:
    """用 pypdf 生成每页一段 ASCII 文本的最小 PDF（不需要外部字体文件）。"""

    writer = PdfWriter()
    for text in pages:
        page = writer.add_blank_page(width=595, height=842)
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
                NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
            }
        )
        font_ref = writer._add_object(font)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})}
        )
        operations = ["BT", "/F1 12 Tf", "14 TL", "72 760 Td"]
        for line in text.split("\n"):
            escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
            operations.append(f"({escaped}) Tj")
            operations.append("T*")
        operations.append("ET")
        stream = DecodedStreamObject()
        stream.set_data("\n".join(operations).encode("latin-1"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_read_pdf_marks_usable_sparse_and_image_only_pages() -> None:
    data = _text_pdf([USABLE_TEXT, SPARSE_TEXT, ""])

    document = read_pdf("file-abc", data)

    assert document.file_id == "file-abc"
    assert document.page_count == 3
    assert [page.page for page in document.pages] == [1, 2, 3]

    usable, sparse, blank = document.pages
    assert usable.quality == "usable"
    assert usable.char_count == char_count(usable.text) > MIN_TEXT_CHARS
    assert "fraction" in usable.text

    assert sparse.quality == "sparse"
    assert sparse.text == SPARSE_TEXT
    assert 0 < sparse.char_count < MIN_TEXT_CHARS

    assert blank.quality == "image_only"
    assert blank.text == ""
    assert blank.char_count == 0

    # 不可用页面只进 limitations，正文保持原样，没有 OCR 补出来的内容。
    assert any("第 2 页" in item and "sparse" in item for item in document.limitations)
    assert any("第 3 页" in item and "image_only" in item for item in document.limitations)
    assert len(document.limitations) == 2

    assert is_usable(usable) is True
    assert is_usable(sparse) is False
    assert is_usable(blank) is False


def test_read_pdf_on_single_usable_page_has_no_limitations() -> None:
    document = read_pdf("file-one", _text_pdf([USABLE_TEXT]))
    assert document.page_count == 1
    assert document.limitations == []
    assert is_usable(document.pages[0])


def test_classification_thresholds_are_explainable() -> None:
    chinese = (
        "有理数是整数和分数的统称，可以写成两个整数之比，也可以写成有限小数或无限循环小数的形式。"
    )
    assert char_count(chinese) > MIN_TEXT_CHARS
    assert classify_page_text(chinese) == "usable"

    assert classify_page_text("短") == "sparse"
    assert classify_page_text("   \n\t ") == "image_only"

    garbled = "\ufffd" * 5 + "乱码页内容" * 3
    assert count_garbled(garbled) == 5
    assert garbled_ratio(garbled) >= GARBLED_RATIO_LIMIT
    assert classify_page_text(garbled) == "garbled"

    # 少量异常字符不把整页判成乱码：占比和绝对数量都要过线。
    mixed = "有理数是整数和分数的统称，可以写成两个整数之比。" * 3 + "\ufffd\ufffd"
    assert count_garbled(mixed) == 2
    assert garbled_ratio(mixed) < GARBLED_RATIO_LIMIT
    assert classify_page_text(mixed) == "usable"


def test_normalize_text_only_converts_fullwidth_and_keeps_length() -> None:
    assert normalize_text("ＡＢＣ１２３，ｘ²＋１") == "ABC123,x²+1"
    assert normalize_text("Ｐ（Ａ）＝０．５") == "P(A)=0.5"
    assert len(normalize_text("Ａ，²½")) == len("Ａ，²½")


def test_extract_page_pdf_returns_only_requested_page() -> None:
    data = _text_pdf([USABLE_TEXT, SPARSE_TEXT])
    assert page_count(data) == 2

    single = extract_page_pdf(data, 2)
    reader = PdfReader(BytesIO(single))
    assert len(reader.pages) == 1
    extracted = reader.pages[0].extract_text() or ""
    assert SPARSE_TEXT in extracted
    assert "fraction" not in extracted

    with pytest.raises(IndexError):
        extract_page_pdf(data, 3)
    with pytest.raises(IndexError):
        extract_page_pdf(data, 0)


def test_unreadable_bytes_raise_domain_error() -> None:
    with pytest.raises(DomainError) as excinfo:
        read_pdf("file-broken", b"not a pdf at all")
    assert excinfo.value.code == "VALIDATION_ERROR"
    assert "PDF" in excinfo.value.message


def test_is_usable_rejects_mislabelled_blank_page() -> None:
    fake = PageText(page=1, text="   ", char_count=0, quality="usable")
    assert is_usable(fake) is False
