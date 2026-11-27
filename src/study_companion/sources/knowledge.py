"""知识点投影。

只读取调用方传入的正式 annotations 与 source_locators，把它们原样投影成材料
引用和知识点来源；不生成新的材料-知识点关联，也不补造 source_locator。
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from .models import MaterialInput

KnowledgeStatus = Literal["mapped", "unknown"]


def _mapping(value: Any) -> Mapping[str, Any]:
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


def _locators(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    result: list[dict[str, Any]] = []
    for item in value:
        data = _mapping(item)
        if not data:
            continue
        if _text(data.get("verification_status")) == "rejected":
            continue
        result.append(dict(data))
    return tuple(result)


def _rejected(annotation: Mapping[str, Any]) -> bool:
    if _text(annotation.get("verification_status")) == "rejected":
        return True
    review_info = _mapping(annotation.get("review_info"))
    return _text(review_info.get("status")) == "rejected"


@dataclass(frozen=True, slots=True)
class KnowledgePointRef:
    """材料到知识点的正式引用。"""

    kp_id: str
    role: str = "primary"
    basis: str = ""
    annotation_id: str | None = None
    knowledge_version: str = ""
    current: bool = True
    source: str = "annotation"
    source_locators: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "kp_id": self.kp_id,
            "role": self.role,
            "basis": self.basis,
            "annotation_id": self.annotation_id,
            "knowledge_version": self.knowledge_version,
            "current": self.current,
            "source": self.source,
            "source_locators": [dict(item) for item in self.source_locators],
        }


@dataclass(frozen=True, slots=True)
class KnowledgeProjection:
    """一条材料的知识点投影结论。"""

    material_id: str
    status: KnowledgeStatus
    refs: tuple[KnowledgePointRef, ...] = ()
    unresolved_kp_ids: tuple[str, ...] = ()
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "material_id": self.material_id,
            "status": self.status,
            "kp_refs": [ref.to_dict() for ref in self.refs],
            "unresolved_kp_ids": list(self.unresolved_kp_ids),
            "note": self.note,
        }


def project_knowledge_refs(
    materials: Sequence[MaterialInput | str],
    *,
    annotations: Sequence[Any],
    knowledge_version: str,
    knowledge_point_ids: Collection[str],
    include_indicator_fallback: bool = False,
) -> tuple[KnowledgeProjection, ...]:
    """把正式 annotations 投影成材料的 ``kp_refs``。

    ``include_indicator_fallback`` 默认关闭。打开时才会使用材料自身的正式
    ``indicator_ids`` 生成 ``kp-{indicator_id}`` 引用；这是既有课程要求的映射，
    不是本模块新增的推断。
    """

    allowed = {str(item) for item in knowledge_point_ids}
    by_material: dict[str, list[Mapping[str, Any]]] = {}
    for raw in annotations:
        annotation = _mapping(raw)
        if not annotation or _rejected(annotation):
            continue
        if _text(annotation.get("source_type")) != "material":
            continue
        if _text(annotation.get("target_type")) != "knowledge_point":
            continue
        source_id = _text(annotation.get("source_id")).strip()
        if not source_id:
            continue
        by_material.setdefault(source_id, []).append(annotation)

    normalized: list[MaterialInput] = []
    for item in materials:
        if isinstance(item, MaterialInput):
            normalized.append(item)
        else:
            normalized.append(MaterialInput(material_id=str(item), material_type="", content=""))

    results: list[KnowledgeProjection] = []
    for material in normalized:
        refs_by_kp: dict[str, KnowledgePointRef] = {}
        unresolved: list[str] = []
        for annotation in by_material.get(material.material_id, []):
            annotation_version = _text(annotation.get("knowledge_version")).strip() or knowledge_version
            locators = _locators(annotation.get("source_locators"))
            for raw_target in annotation.get("target_ids") or []:
                kp_id = _text(raw_target).strip()
                if not kp_id:
                    continue
                if kp_id not in allowed:
                    if kp_id not in unresolved:
                        unresolved.append(kp_id)
                    continue
                if kp_id not in refs_by_kp:
                    refs_by_kp[kp_id] = KnowledgePointRef(
                        kp_id=kp_id,
                        role=_text(annotation.get("knowledge_role")) or "primary",
                        basis=_text(annotation.get("basis")),
                        annotation_id=_text(annotation.get("annotation_id")).strip() or None,
                        knowledge_version=annotation_version,
                        current=annotation_version == knowledge_version,
                        source="annotation",
                        source_locators=locators,
                    )

        if not refs_by_kp and include_indicator_fallback:
            for raw_indicator in material.indicator_ids:
                indicator = _text(raw_indicator).strip()
                if not indicator:
                    continue
                kp_id = indicator if indicator.startswith("kp-") else f"kp-{indicator}"
                if kp_id not in allowed:
                    if kp_id not in unresolved:
                        unresolved.append(kp_id)
                    continue
                refs_by_kp.setdefault(
                    kp_id,
                    KnowledgePointRef(
                        kp_id=kp_id,
                        role="primary",
                        basis="standard",
                        annotation_id=None,
                        knowledge_version=knowledge_version,
                        current=True,
                        source="indicator_mapping",
                    ),
                )

        refs = tuple(refs_by_kp[key] for key in sorted(refs_by_kp))
        if refs:
            note = ""
            status: KnowledgeStatus = "mapped"
        elif unresolved:
            status = "unknown"
            note = "正式标注的目标知识点不在当前知识版本，未新增关联"
        else:
            status = "unknown"
            note = "没有正式材料-知识点标注，知识点关联保持 unknown"
        results.append(
            KnowledgeProjection(
                material_id=material.material_id,
                status=status,
                refs=refs,
                unresolved_kp_ids=tuple(sorted(unresolved)),
                note=note,
            )
        )
    return tuple(results)


def summarize_knowledge_refs(
    projections: Sequence[KnowledgeProjection],
) -> dict[str, Any]:
    mapped = [item.material_id for item in projections if item.status == "mapped"]
    unknown = [item.material_id for item in projections if item.status == "unknown"]
    return {
        "materials_total": len(projections),
        "materials_mapped": len(mapped),
        "materials_unknown": len(unknown),
        "mapped_material_ids": sorted(mapped),
        "unknown_material_ids": sorted(unknown),
    }


@dataclass(frozen=True, slots=True)
class KnowledgePointSource:
    """一个知识点的来源投影；只搬运已有 source_locators。"""

    kp_id: str
    definition_locator: dict[str, Any] | None = None
    source_locators: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "kp_id": self.kp_id,
            "definition_locator": dict(self.definition_locator) if self.definition_locator else None,
            "source_locators": [dict(item) for item in self.source_locators],
        }


def project_knowledge_sources(points: Sequence[Any]) -> tuple[KnowledgePointSource, ...]:
    """逐点投影正式 source_locators，过滤 rejected，不新增任何定位。"""

    results: list[KnowledgePointSource] = []
    for raw in points:
        point = _mapping(raw)
        kp_id = _text(point.get("kp_id")).strip()
        if not kp_id:
            continue
        definition = _mapping(point.get("definition_locator"))
        if definition and _text(definition.get("verification_status")) == "rejected":
            definition = {}
        results.append(
            KnowledgePointSource(
                kp_id=kp_id,
                definition_locator=dict(definition) if definition else None,
                source_locators=_locators(point.get("source_locators")),
            )
        )
    return tuple(results)


def summarize_knowledge_sources(
    sources: Sequence[KnowledgePointSource],
) -> dict[str, Any]:
    locators = [locator for item in sources for locator in item.source_locators]
    verified = [
        locator
        for locator in locators
        if _text(locator.get("verification_status")) == "verified"
    ]
    points_with_any = [
        item.kp_id
        for item in sources
        if item.source_locators or item.definition_locator is not None
    ]
    points_with_verified = [
        item.kp_id
        for item in sources
        if any(_text(locator.get("verification_status")) == "verified" for locator in item.source_locators)
        or (
            item.definition_locator is not None
            and _text(item.definition_locator.get("verification_status")) == "verified"
        )
    ]
    return {
        "points_total": len(sources),
        "points_with_any_locator": len(points_with_any),
        "points_with_verified_locator": len(points_with_verified),
        "locators_total": len(locators),
        "locators_verified": len(verified),
        "locators_unverified": len(locators) - len(verified),
    }
