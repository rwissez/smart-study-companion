"""上传文件解析：六张 CSV 或一份 XLSX（对应六张工作表）。

原始值一律保留为字符串或 null；不做数字转换、不做全角替换，转换交给 B 的清洗。
无法识别表头时返回 ``None``，由导入服务标记为待教师确认，而不是猜。
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass

from starlette.datastructures import UploadFile

from study_companion.contracts import DomainError, RawRow

TABLE_NAMES: tuple[str, ...] = (
    "requirements",
    "materials",
    "students",
    "nodes",
    "records",
    "dictionary",
)

#: 每个逻辑表的识别特征（表头必须同时包含这些列）。官方列名见 01 §3.1。
HEADER_SIGNATURES: dict[str, tuple[str, ...]] = {
    "requirements": ("主题编号", "指标编号", "观察指标"),
    "materials": ("材料编号", "材料类型", "题干或内容"),
    "students": ("学生编号", "模拟班级编号"),
    "nodes": ("节点编号", "模拟测验日期"),
    "records": ("记录编号", "学生编号", "材料编号", "节点编号"),
    "dictionary": ("数据表", "字段名"),
}

ROLE_BY_TABLE = {
    "requirements": "requirements",
    "materials": "materials",
    "students": "students",
    "nodes": "nodes",
    "records": "records",
    "dictionary": "dictionary",
}


@dataclass
class ParsedSheet:
    name: str
    headers: list[str]
    rows: list[RawRow]


def detect_role(headers: list[str]) -> str | None:
    normalized = {header.strip() for header in headers if header}
    matches = [
        table
        for table, needed in HEADER_SIGNATURES.items()
        if all(item in normalized for item in needed)
    ]
    if len(matches) == 1:
        return ROLE_BY_TABLE[matches[0]]
    return None


def _rows_from_matrix(matrix: list[list[str]]) -> ParsedSheet:
    if not matrix:
        raise DomainError("UNPROCESSABLE", "文件没有数据行")
    headers = [str(cell).strip() for cell in matrix[0]]
    if any(not header for header in headers):
        raise DomainError("UNPROCESSABLE", "文件缺少列名")
    if len(set(headers)) != len(headers):
        raise DomainError("UNPROCESSABLE", "文件列名重复")
    if "source_row_number" in headers:
        raise DomainError("UNPROCESSABLE", "文件包含保留列名 source_row_number")
    rows: list[RawRow] = []
    for index, raw in enumerate(matrix[1:], start=1):
        if not any(str(cell).strip() for cell in raw):
            continue
        padded = list(raw) + [""] * (len(headers) - len(raw))
        if len(padded) > len(headers) and any(str(cell).strip() for cell in padded[len(headers):]):
            raise DomainError("UNPROCESSABLE", f"第 {index} 行的字段数量超过列数")
        row: RawRow = {}
        for header, cell in zip(headers, padded, strict=False):
            text = str(cell)
            row[header] = text if text != "" else None
        row["source_row_number"] = str(index)
        rows.append(row)
    if not rows:
        raise DomainError("UNPROCESSABLE", "文件没有数据行")
    return ParsedSheet(name="", headers=headers, rows=rows)


def parse_csv(data: bytes, name: str = "") -> ParsedSheet:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DomainError("UNPROCESSABLE", f"{name or 'CSV'} 不是合法 UTF-8 文本") from exc
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    matrix = [row for row in reader if row]
    sheet = _rows_from_matrix(matrix)
    sheet.name = name
    return sheet


def parse_xlsx(data: bytes, name: str = "") -> list[ParsedSheet]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover - 依赖缺失时明确报错
        raise DomainError(
            "SERVICE_UNAVAILABLE", "缺少 XLSX 解析依赖（openpyxl），请先安装依赖"
        ) from exc
    try:
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:
        raise DomainError("UNPROCESSABLE", f"{name or 'XLSX'} 无法解析：{type(exc).__name__}") from exc
    sheets: list[ParsedSheet] = []
    try:
        for worksheet in workbook.worksheets:
            matrix: list[list[str]] = []
            for row in worksheet.iter_rows(values_only=True):
                cells = ["" if value is None else str(value) for value in row]
                if not any(cell.strip() for cell in cells):
                    continue
                matrix.append(cells)
            if not matrix:
                continue
            parsed = _rows_from_matrix(matrix)
            parsed.name = worksheet.title
            sheets.append(parsed)
    finally:
        workbook.close()
    if not sheets:
        raise DomainError("UNPROCESSABLE", "XLSX 中没有可读取的工作表")
    return sheets


def sniff_format(filename: str, data: bytes) -> str:
    lower = (filename or "").lower()
    if lower.endswith(".xlsx") or data[:2] == b"PK":
        return "xlsx"
    if lower.endswith(".pdf") or data[:5] == b"%PDF-":
        return "pdf"
    return "csv"


async def read_upload(upload: UploadFile, max_bytes: int) -> bytes:
    data = await upload.read()
    if len(data) > max_bytes:
        raise DomainError(
            "UPLOAD_TOO_LARGE",
            f"文件超过 {max_bytes // (1024 * 1024)} MB 上限",
            [{"field": upload.filename or "file", "reason": "文件过大"}],
        )
    return data
