"""知识版本差异与影响集（02 §11）。

比较两版正式知识体系，回答三件事：新增/删除/改名/改内容各是什么，影响哪些
指标与题目，哪些已发布方案需要重发。只读比较，不写库、不改版本。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

OBJECTS = (
    ("point", "points", "kp_id"),
    ("relation", "relations", "relation_id"),
    ("annotation", "annotations", "annotation_id"),
)


@dataclass
class ObjectChange:
    object_type: str
    object_id: str
    kind: str  # added / removed / renamed / changed
    previous_id: str | None = None
    fields: list[str] = field(default_factory=list)
    name: str = ""
    detail: str = ""


def _index(payload: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    indexed: dict[tuple[str, str], dict[str, Any]] = {}
    for object_type, key, id_field in OBJECTS:
        for item in payload.get(key, []) or []:
            object_id = str(item.get(id_field) or "")
            if object_id:
                indexed[(object_type, object_id)] = item
    return indexed


def _name_of(object_type: str, item: dict[str, Any]) -> str:
    if object_type == "point":
        return str(item.get("name") or "")
    if object_type == "relation":
        return str(item.get("type") or "")
    return str(item.get("source_id") or "")


def _content(item: dict[str, Any]) -> str:
    return str(item.get("content_hash") or "")


def diff_payloads(previous: dict[str, Any], current: dict[str, Any]) -> list[ObjectChange]:
    """按对象类型与 id 对齐；id 变了但名称与内容一致记为改名。"""

    before = _index(previous)
    after = _index(current)
    changes: list[ObjectChange] = []

    removed = [key for key in before if key not in after]
    added = [key for key in after if key not in before]
    renamed_pairs: set[tuple[str, str]] = set()

    # 改名与"删旧加新"区分：名称相同但 id 不同，或内容哈希相同。
    for old_key in list(removed):
        old_item = before[old_key]
        match = next(
            (
                new_key
                for new_key in added
                if new_key[0] == old_key[0]
                and (
                    (_name_of(new_key[0], after[new_key]) == _name_of(old_key[0], old_item) != "")
                    or (_content(after[new_key]) == _content(old_item) != "")
                )
            ),
            None,
        )
        if match is None:
            continue
        changes.append(
            ObjectChange(
                object_type=old_key[0],
                object_id=match[1],
                kind="renamed",
                previous_id=old_key[1],
                name=_name_of(old_key[0], old_item),
                detail="对象标识变化，名称与内容一致，按纯改名处理",
            )
        )
        renamed_pairs.add((old_key, match))
        removed.remove(old_key)
        added.remove(match)

    for key in added:
        changes.append(
            ObjectChange(
                object_type=key[0],
                object_id=key[1],
                kind="added",
                name=_name_of(key[0], after[key]),
                detail="新版本新增对象",
            )
        )
    for key in removed:
        changes.append(
            ObjectChange(
                object_type=key[0],
                object_id=key[1],
                kind="removed",
                name=_name_of(key[0], before[key]),
                detail="新版本删除对象，依赖它的方案需要复查",
            )
        )
    for key in before:
        if key not in after:
            continue
        old_item, new_item = before[key], after[key]
        if _content(old_item) and _content(new_item) and _content(old_item) == _content(new_item):
            continue
        changed_fields = [
            name
            for name in ("name", "definition", "type", "target_id", "target_ids", "source_id")
            if old_item.get(name) != new_item.get(name)
        ]
        changes.append(
            ObjectChange(
                object_type=key[0],
                object_id=key[1],
                kind="changed",
                name=_name_of(key[0], new_item),
                fields=changed_fields or ["content_hash"],
                detail="对象内容变化",
            )
        )
    changes.sort(key=lambda item: (item.object_type, item.kind, item.object_id))
    return changes


def impact_of(changes: list[ObjectChange], current: dict[str, Any]) -> dict[str, Any]:
    """影响集：指标、主题、题目、需要复查的方案。"""

    indexed = _index(current)
    indicator_ids: set[str] = set()
    topic_ids: set[str] = set()
    material_ids: set[str] = set()
    for change in changes:
        if change.object_type == "point":
            item = indexed.get(("point", change.object_id), {})
            indicator_ids.update(item.get("indicator_ids") or [])
            topic_ids.update(item.get("topic_ids") or [])
        elif change.object_type == "annotation":
            item = indexed.get(("annotation", change.object_id), {})
            if item.get("source_type") == "material" and item.get("source_id"):
                material_ids.add(str(item["source_id"]))
            if change.kind == "removed":
                # 被删除的关联只有旧版本里能查到来源，调用方会另行补充题目清单。
                material_ids.add(f"removed:{change.object_id}")
    requires_replan = bool(indicator_ids or material_ids)
    return {
        "indicator_ids": sorted(indicator_ids),
        "topic_ids": sorted(topic_ids),
        "material_ids": sorted(material_ids),
        "requires_replan": requires_replan,
        "reason": "知识点或题目关联发生变化" if requires_replan else "仅涉及无方案依赖的对象",
    }
