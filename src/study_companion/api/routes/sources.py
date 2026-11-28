"""受控来源页读取：``GET /sources/{file_id}/pages/{pdf_page}``（01 §9.2、02 §5）。

规则：

* 仅教师可用（学生看不到教材原件；教师也要有 ``view_curriculum`` 能力）；
* ``file_id`` 必须属于当前会话绑定的数据集：查 ``dataset_sources`` 登记，或该数据集
  导入批次里的文件（``imports`` → ``import_files``）。不属于就是越权 403；
* 只接受登记过的 ``file_id``，不接受任何路径参数；
* 用 pypdf 抽出**这一页**写成单页 PDF，``Content-Disposition: inline``，
  不返回全文，也不在响应里带正文文本；
* 页码按文件页从 1 起，越界 404；同时校验原文件 SHA-256，哈希不符拒绝返回。
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Response
from sqlalchemy import select

from study_companion.api.deps import AppSettings, DbSession, Scope
from study_companion.contracts import DomainError
from study_companion.db import models
from study_companion.knowledge import pdf_reader
from study_companion.library import blobs

router = APIRouter(tags=["sources"])

PDF_MEDIA_TYPE = "application/pdf"
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _dataset_file_ids(db: DbSession, dataset_id: str) -> set[str]:
    """当前数据集登记过的来源文件：数据来源表 + 导入批次文件。"""

    ids = set(
        db.scalars(
            select(models.DatasetSource.file_id).where(
                models.DatasetSource.dataset_id == dataset_id
            )
        ).all()
    )
    ids.update(
        db.scalars(
            select(models.ImportFileRow.file_id)
            .join(
                models.ImportRecord,
                models.ImportRecord.import_id == models.ImportFileRow.import_id,
            )
            .where(models.ImportRecord.dataset_id == dataset_id)
        ).all()
    )
    return ids


def _attachment_name(file_id: str, pdf_page: int) -> str:
    safe = _SAFE_NAME.sub("-", file_id) or "source"
    return f"source-{safe}-p{pdf_page}.pdf"


@router.get("/sources/{file_id}/pages/{pdf_page}")
def get_source_page(
    file_id: str,
    pdf_page: int,
    db: DbSession,
    scope: Scope,
    settings: AppSettings,
) -> Response:
    scope.require_teacher()
    scope.require("view_curriculum")
    if scope.dataset_id is None:
        raise DomainError("FORBIDDEN", "当前会话尚未绑定数据集")

    row = db.get(models.FileObject, file_id)
    if row is None:
        raise DomainError("NOT_FOUND", "来源文件不存在")
    if file_id not in _dataset_file_ids(db, scope.dataset_id):
        raise DomainError("FORBIDDEN", "当前会话无权访问该来源文件")
    if (row.media_type or "") != PDF_MEDIA_TYPE:
        raise DomainError("UNPROCESSABLE", "该来源不是 PDF，不能按页读取")

    # 先按登记哈希核对原字节，再解析；被替换或损坏的文件不返回页面。
    path = blobs.verify(row.storage_key, row.sha256, settings)
    data = path.read_bytes()

    total = pdf_reader.page_count(data)
    if pdf_page < 1 or pdf_page > total:
        raise DomainError("NOT_FOUND", f"页码越界：该 PDF 共 {total} 页")
    payload = pdf_reader.extract_page_pdf(data, pdf_page)

    return Response(
        content=payload,
        media_type=PDF_MEDIA_TYPE,
        headers={
            "Content-Disposition": f'inline; filename="{_attachment_name(file_id, pdf_page)}"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
