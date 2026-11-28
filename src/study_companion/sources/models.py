"""材料来源精度的输入、输出与审计模型。

本模块只定义纯数据对象，不读数据库、不写文件。来源核验的判定逻辑在
:mod:`study_companion.sources.enrich`，知识点投影在
:mod:`study_companion.sources.knowledge`。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

VerificationStatus = Literal["verified", "unverified", "not_applicable"]


def _mapping(value: Any) -> Mapping[str, Any]:
    """把 pydantic 对象或普通映射统一读成映射；读不出时返回空映射。"""

    if value is None:
        return {}
    if isinstance(value, Mapping):
        return value
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        dumped = dump(mode="json")
        if isinstance(dumped, Mapping):
            return dumped
    return {}


def _text(value: Any) -> str:
    return "" if value is None else str(value)


@dataclass(frozen=True, slots=True)
class MaterialInput:
    """待核验的材料。

    ``source_locator`` 保留目录里已有的出处字段；本模块只做加法，不删除调用方
    已经登记的来源信息。
    """

    material_id: str
    material_type: str
    content: str
    source_locator: Mapping[str, Any] = field(default_factory=dict)
    source_label: str | None = None
    indicator_ids: tuple[str, ...] = ()

    @classmethod
    def from_payload(cls, payload: Any) -> "MaterialInput":
        data = _mapping(payload)
        locator = _mapping(data.get("source_locator"))
        indicator_ids = data.get("indicator_ids") or ()
        if isinstance(indicator_ids, str):
            indicator_ids = (indicator_ids,)
        label = _text(data.get("source_label")).strip()
        return cls(
            material_id=_text(data.get("material_id")).strip(),
            material_type=_text(data.get("material_type")).strip(),
            content=_text(data.get("content")),
            source_locator=dict(locator),
            source_label=label or None,
            indicator_ids=tuple(str(item) for item in indicator_ids),
        )


@dataclass(frozen=True, slots=True)
class RawMaterialRow:
    """原始材料表中的一行。

    对应 ``source_rows.table_name == "materials"`` 的逻辑行。``row_number`` 是
    表内逻辑行号（1 起），不是 CSV 文件物理行号；物理行号在
    ``SourceRow.physical_row``，本模块不使用它做对外引用。
    """

    row_number: int
    payload: Mapping[str, Any]
    file_id: str | None = None
    source_file: str = "题目与材料"
    dataset_id: str | None = None
    table_name: str = "materials"

    def __post_init__(self) -> None:
        if self.row_number < 1:
            raise ValueError("row_number 必须从 1 开始")
        if self.table_name != "materials":
            raise ValueError("RawMaterialRow 只接受 table_name=materials 的逻辑行")

    @classmethod
    def from_source_row(
        cls,
        source_row: Any,
        *,
        source_file: str = "题目与材料",
    ) -> "RawMaterialRow":
        """从 ``db.models.SourceRow`` 或同形对象读取一行。"""

        return cls(
            row_number=int(getattr(source_row, "row_number")),
            payload=dict(_mapping(getattr(source_row, "payload_json", {}))),
            file_id=getattr(source_row, "file_id", None),
            source_file=source_file,
            dataset_id=getattr(source_row, "dataset_id", None),
            table_name=str(getattr(source_row, "table_name", "materials")),
        )

    @property
    def material_id(self) -> str:
        return _text(self.payload.get("材料编号")).strip()

    @property
    def content(self) -> str:
        return _text(self.payload.get("题干或内容"))

    @property
    def material_type(self) -> str:
        return _text(self.payload.get("材料类型")).strip()

    @property
    def declared_row_number(self) -> int | None:
        raw = self.payload.get("source_row_number")
        if raw in (None, ""):
            return None
        try:
            return int(str(raw))
        except (TypeError, ValueError):
            return -1


@dataclass(frozen=True, slots=True)
class TextbookPage:
    """教材 PDF 的一页文本。

    ``pdf_page`` 是 PDF 阅读器中的 1 起页序，不是印刷页码。印刷页码只在调用方
    能从页面内容可靠读出时填入；本模块绝不推算。
    """

    file_id: str
    pdf_page: int
    text: str
    printed_page: str | None = None
    source_file: str | None = None

    def __post_init__(self) -> None:
        if self.pdf_page < 1:
            raise ValueError("pdf_page 必须从 1 开始")


@dataclass(frozen=True, slots=True)
class EnrichedSource:
    """一条材料的核验结论。

    ``source_ref()`` 返回可直接放进导出 ``source_refs`` 的扁平字典；其中的
    ``source_row_number`` 与 ``quote`` 不属于正式 ``SourceLocator`` 契约，因此
    不能写回 ``Material.source_locator``，只能供导出投影使用。
    """

    material_id: str
    material_type: str
    verification_status: VerificationStatus
    reason: str
    reason_detail: str
    source_file: str | None = None
    source_row_number: int | None = None
    file_id: str | None = None
    quote: str | None = None
    pdf_page: int | None = None
    printed_page: str | None = None
    source_label: str | None = None
    source_kind: str | None = None
    previous_status: str | None = None

    @property
    def page(self) -> int | None:
        """``pdf_page`` 的语义别名，方便调用方按“页码”读取。"""

        return self.pdf_page

    @property
    def source_status(self) -> str:
        """来源状态标签。

        ``verification_status`` 沿用正式契约的 verified/unverified/not_applicable；
        当一条出处信息都没有时，这里返回 ``unknown``，与导出里“来源未知”的语义
        对齐，同时不改变 ``SourceLocator`` 只能取 verified/unverified 的约束。
        """

        if self.verification_status != "unverified":
            return self.verification_status
        if any(
            value
            for value in (
                self.source_file,
                self.file_id,
                self.source_row_number,
                self.pdf_page,
                self.quote,
            )
        ):
            return "unverified"
        return "unknown"

    def source_ref(self, *, catalog_version: str | None = None) -> dict[str, Any]:
        """导出友好的出处对象；字段名与 ``materials_index.json`` 对齐。"""

        ref: dict[str, Any] = {
            "kind": "material_source",
            "id": self.material_id,
            "material_id": self.material_id,
            "source_file": self.source_file or "",
            "verification_status": self.verification_status,
        }
        if catalog_version:
            ref["catalog_version"] = catalog_version
        if self.source_kind:
            ref["source_kind"] = self.source_kind
        if self.file_id:
            ref["file_id"] = self.file_id
        if self.source_row_number is not None:
            ref["source_row_number"] = self.source_row_number
        if self.quote:
            ref["quote"] = self.quote
        if self.pdf_page is not None:
            ref["pdf_page"] = self.pdf_page
        if self.printed_page:
            ref["printed_page"] = self.printed_page
        if self.source_label:
            ref["source_label"] = self.source_label

        if self.verification_status == "verified":
            ref["locator_level"] = "structured"
        elif self.source_file in {"题目与材料", "材料表"}:
            ref["locator_level"] = "table_only"
        else:
            ref["locator_level"] = "label_only"
        return ref

    def to_dict(self, *, include_quote: bool = True) -> dict[str, Any]:
        data: dict[str, Any] = {
            "material_id": self.material_id,
            "material_type": self.material_type,
            "verification_status": self.verification_status,
            "source_status": self.source_status,
            "reason": self.reason,
            "reason_detail": self.reason_detail,
            "source_file": self.source_file,
            "source_row_number": self.source_row_number,
            "file_id": self.file_id,
            "pdf_page": self.pdf_page,
            "source_label": self.source_label,
            "previous_status": self.previous_status,
        }
        if include_quote:
            data["quote"] = self.quote
        else:
            data["quote_length"] = len(self.quote) if self.quote else 0
        return data


@dataclass(frozen=True, slots=True)
class EnrichmentReport:
    """一次材料来源核验的完整结果与汇总。"""

    items: tuple[EnrichedSource, ...]

    def by_material_id(self) -> dict[str, EnrichedSource]:
        return {item.material_id: item for item in self.items}

    def source_refs_by_material_id(
        self,
        *,
        catalog_version: str | None = None,
    ) -> dict[str, dict[str, Any]]:
        """按材料编号返回可直接放进导出 ``source_refs`` 的对象。"""

        return {
            item.material_id: item.source_ref(catalog_version=catalog_version)
            for item in self.items
        }

    def by_status(self) -> dict[str, int]:
        counts = {"verified": 0, "unverified": 0, "not_applicable": 0}
        for item in self.items:
            counts[item.verification_status] += 1
        return counts

    def by_reason(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.items:
            counts[item.reason] = counts.get(item.reason, 0) + 1
        return dict(sorted(counts.items()))

    def reasons(self) -> dict[str, list[str]]:
        grouped: dict[str, list[str]] = {}
        for item in self.items:
            grouped.setdefault(item.reason, []).append(item.material_id)
        return {code: sorted(ids) for code, ids in sorted(grouped.items())}

    def by_material_type(self) -> dict[str, dict[str, int]]:
        grouped: dict[str, dict[str, int]] = {}
        for item in self.items:
            bucket = grouped.setdefault(
                item.material_type or "unknown",
                {"total": 0, "verified": 0, "unverified": 0, "not_applicable": 0},
            )
            bucket["total"] += 1
            bucket[item.verification_status] += 1
        return dict(sorted(grouped.items()))

    def to_dict(self, *, include_items: bool = True, include_quote: bool = True) -> dict[str, Any]:
        data: dict[str, Any] = {
            "total": len(self.items),
            **self.by_status(),
            "by_material_type": self.by_material_type(),
            "reason_counts": self.by_reason(),
            "reasons": self.reasons(),
        }
        if include_items:
            data["items"] = [item.to_dict(include_quote=include_quote) for item in self.items]
        return data
