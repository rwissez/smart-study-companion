"""Decode the six uploaded CSV tables before applying business rules."""

import csv
import io

from fastapi import UploadFile

from study_companion.contracts import DomainError, RawTables


async def raw_tables_from_uploads(
    requirements: UploadFile,
    materials: UploadFile,
    students: UploadFile,
    nodes: UploadFile,
    records: UploadFile,
    dictionary: UploadFile,
) -> RawTables:
    uploads = {
        "requirements": requirements,
        "materials": materials,
        "students": students,
        "nodes": nodes,
        "records": records,
        "dictionary": dictionary,
    }
    tables = {}
    for name, upload in uploads.items():
        try:
            content = (await upload.read()).decode("utf-8-sig")
            reader = csv.DictReader(io.StringIO(content, newline=""), strict=True)
            headers = reader.fieldnames
            if not headers or any(
                not header or not header.strip() for header in headers
            ):
                raise ValueError("缺少列名")
            if len(set(headers)) != len(headers) or "source_row_number" in headers:
                raise ValueError("列名重复或包含保留字段")
            rows = []
            for row_number, row in enumerate(reader, start=1):
                if None in row:
                    raise ValueError("行中的字段数量超过列数")
                row["source_row_number"] = str(row_number)
                rows.append(row)
            if not rows:
                raise ValueError("文件没有数据行")
            tables[name] = rows
        except (UnicodeDecodeError, csv.Error, ValueError) as exc:
            raise DomainError("UNPROCESSABLE", f"{name} 文件格式无效：{exc}") from exc
    return RawTables.model_validate(tables)
