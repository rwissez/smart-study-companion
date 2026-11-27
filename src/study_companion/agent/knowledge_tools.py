"""Read-only knowledge query adapters for chat's server-bound context.

正式知识以数据库 ``versions`` / ``formal_heads`` 为唯一正式来源；本地
``KnowledgeStore`` 快照只服务旧测试与旧部署的回退。现有查询函数按
``KnowledgeBase`` 的形状读取，因此这里用一个最小只读适配对象把正式
payload 投影成同样字段，不复制、不改写数据库内容。
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from study_companion.agent.expanded_tools import DB_SESSION_KEY
from study_companion.agent.loop import RegisteredTool, ToolRegistry
from study_companion.agent.tasks import TaskSpec
from study_companion.contracts import Catalog, DomainError
from study_companion.db import models
from study_companion.db import version_repository as repo
from study_companion.knowledge.query import get_knowledge_detail, get_knowledge_relations, list_knowledge_candidates, locate_knowledge
from study_companion.knowledge.store import KnowledgeStore
from study_companion.tools.b_analysis import ServerContext

_LOCATOR_STATUSES = {"verified", "unverified", "rejected"}
#: 正式版本只在复核通过后才会移动 ``formal_heads`` 指针；正文里的 review_info
#: 仍保留对象级结论，这里按它区分可见性，避免把 needs_review 当成已核对。
_REVIEW_VERIFIED = {"validated", "verified", "human_confirmed", "approved", "repaired"}
_REVIEW_UNVERIFIED = {"needs_review", "pending", "unverified", "candidate"}
_REVIEW_REJECTED = {"rejected"}


def _verification(item: Mapping[str, Any], *, formal: bool) -> str:
    explicit = item.get("verification_status")
    if isinstance(explicit, str) and explicit in {"verified", "unverified", "rejected"}:
        return explicit
    review = item.get("review_info")
    status = review.get("status") if isinstance(review, Mapping) else None
    if status in _REVIEW_REJECTED:
        return "rejected"
    if status in _REVIEW_UNVERIFIED:
        return "unverified"
    if status in _REVIEW_VERIFIED:
        return "verified"
    # 已发布正式版本里的对象默认来自复核通过的正文；非正式快照不适用。
    return "verified" if formal else "unverified"


class _LocatorView:
    """把正式 payload 的 source_locator 投影成查询函数需要的定位对象。"""

    def __init__(self, data: Mapping[str, Any] | None = None) -> None:
        raw = dict(data or {})
        page = raw.get("pdf_page", raw.get("file_page"))
        if isinstance(page, str) and page.strip().isdigit():
            page = int(page.strip())
        if isinstance(page, bool) or not isinstance(page, int) or page < 1:
            page = 1
        printed = raw.get("printed_page")
        if isinstance(printed, str) and printed.strip().isdigit():
            printed = int(printed.strip())
        elif isinstance(printed, bool) or not isinstance(printed, int):
            printed = None
        status = raw.get("verification_status")
        self.source_file = str(raw.get("source_file") or raw.get("source_id") or "unknown")
        self.file_page = page
        self.printed_page = printed
        self.chapter = str(raw.get("chapter") or "")
        self.section = str(raw.get("section") or "")
        self.verification_status = (
            status if isinstance(status, str) and status in _LOCATOR_STATUSES else "unverified"
        )
        self._payload = {
            **raw,
            "source_file": self.source_file,
            "file_page": self.file_page,
            "printed_page": self.printed_page,
            "chapter": self.chapter,
            "section": self.section,
            "verification_status": self.verification_status,
        }

    def model_dump(self, mode: str = "python", **_: Any) -> dict[str, Any]:
        return copy.deepcopy(self._payload)


class _PointView:
    def __init__(self, data: Mapping[str, Any]) -> None:
        raw = dict(data)
        locators = [item for item in raw.get("source_locators") or [] if isinstance(item, Mapping)]
        self.kp_id = str(raw.get("kp_id") or "")
        self.name = str(raw.get("name") or "")
        self.aliases = [str(item) for item in raw.get("aliases") or [] if isinstance(item, (str, int))]
        self.kind = str(raw.get("kind") or "unknown")
        self.snippet = str(raw.get("snippet") or "")
        self.topic_ids = [str(item) for item in raw.get("topic_ids") or [] if isinstance(item, (str, int))]
        self.indicator_ids = [str(item) for item in raw.get("indicator_ids") or [] if isinstance(item, (str, int))]
        self.source_locators = [_LocatorView(item) for item in locators]
        # 正式 payload 没有 definition_locator：用第一条来源定位，或缺来源时给出
        # 明确的 unknown 定位。定位核验状态原样保留，不伪造 verified。
        self.definition_locator = _LocatorView(locators[0]) if locators else _LocatorView(
            {"source_file": "unknown", "verification_status": "unverified"}
        )
        self.verification_status = _verification(raw, formal=True)
        self._payload = {
            **raw,
            "kp_id": self.kp_id,
            "name": self.name,
            "aliases": list(self.aliases),
            "kind": self.kind,
            "snippet": self.snippet,
            "topic_ids": list(self.topic_ids),
            "indicator_ids": list(self.indicator_ids),
            "source_locators": [item.model_dump() for item in self.source_locators],
            "definition_locator": self.definition_locator.model_dump(),
            "verification_status": self.verification_status,
        }

    def model_dump(self, mode: str = "python", **_: Any) -> dict[str, Any]:
        return copy.deepcopy(self._payload)


class _RelationView:
    def __init__(self, data: Mapping[str, Any]) -> None:
        raw = dict(data)
        self.relation_id = str(raw.get("relation_id") or "")
        self.type = str(raw.get("type") or "")
        self.source_ids = [str(item) for item in raw.get("source_ids") or [] if isinstance(item, (str, int))]
        self.target_id = str(raw.get("target_id") or "")
        self.verification_status = _verification(raw, formal=True)
        self._payload = {
            **raw,
            "relation_id": self.relation_id,
            "type": self.type,
            "source_ids": list(self.source_ids),
            "target_id": self.target_id,
            "verification_status": self.verification_status,
        }

    def model_dump(self, mode: str = "python", **_: Any) -> dict[str, Any]:
        return copy.deepcopy(self._payload)


class _AnnotationView:
    def __init__(self, data: Mapping[str, Any]) -> None:
        raw = dict(data)
        self.candidate_id = raw.get("candidate_id")
        self.target_ids = [str(item) for item in raw.get("target_ids") or [] if isinstance(item, (str, int))]
        self.verification_status = _verification(raw, formal=True)
        self._payload = {
            **raw,
            "target_ids": list(self.target_ids),
            "verification_status": self.verification_status,
        }

    def model_dump(self, mode: str = "python", **_: Any) -> dict[str, Any]:
        return copy.deepcopy(self._payload)


class _KnowledgeView(BaseModel):
    """只读适配对象：字段与查询函数用到的最小 ``KnowledgeBase`` 接口一致。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    kb_version: str
    status: str = "verified"
    points: list[Any] = Field(default_factory=list)
    relations: list[Any] = Field(default_factory=list)
    annotations: list[Any] = Field(default_factory=list)
    merge_candidates: list[Any] = Field(default_factory=list)


def _mapping_list(payload: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    value = payload.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise DomainError("KB_SNAPSHOT_INVALID", f"正式知识正文的 {key} 不是数组")
    return [item for item in value if isinstance(item, Mapping)]


def _knowledge_from_formal(
    version: models.Version, payload: Mapping[str, Any], ctx: ServerContext
) -> _KnowledgeView:
    if version.kind != "knowledge" or version.dataset_id != ctx.dataset_id:
        raise DomainError("CONTEXT_MISMATCH", "正式知识版本不属于当前数据集")
    declared_dataset = str(payload.get("dataset_id") or "")
    if declared_dataset and declared_dataset != ctx.dataset_id:
        raise DomainError("CONTEXT_MISMATCH", "正式知识正文声明的数据集与会话不一致")
    declared_knowledge = str(payload.get("knowledge_version") or "")
    if declared_knowledge and declared_knowledge != version.version_id:
        raise DomainError("CONTEXT_MISMATCH", "正式知识正文版本与正式指针不一致")
    declared_catalog = str(payload.get("catalog_version") or "")
    if declared_catalog != ctx.catalog_version:
        raise DomainError("CONTEXT_MISMATCH", "正式知识正文绑定的目录与会话目录不一致")
    return _KnowledgeView(
        kb_version=version.version_id,
        status="verified",
        points=[_PointView(item) for item in _mapping_list(payload, "points")],
        relations=[_RelationView(item) for item in _mapping_list(payload, "relations")],
        annotations=[_AnnotationView(item) for item in _mapping_list(payload, "annotations")],
        merge_candidates=[],
    )


def _formal_from_db(ctx: ServerContext) -> _KnowledgeView | None:
    """读取当前正式知识正文；数据库没有正式版本时返回 None 交给文件回退。"""

    db = ctx.extras.get(DB_SESSION_KEY)
    if db is None:
        return None
    dataset = db.get(models.Dataset, ctx.dataset_id)
    if dataset is None or not dataset.family_id:
        return None
    # 生产发布使用不带 target_key 的正式头；历史/测试也可能按目录版本登记，
    # 两种键都认，但正文仍必须自己声明同一个 catalog_version。
    keys = (
        repo.knowledge_key(dataset.family_id, dataset.id, ctx.catalog_version),
        repo.FormalKey(family_id=dataset.family_id, dataset_id=dataset.id, kind="knowledge"),
    )
    versions: list[models.Version] = []
    seen: set[str] = set()
    for key in keys:
        version = repo.read_formal(db, key)
        if version is not None and version.version_id not in seen:
            seen.add(version.version_id)
            versions.append(version)
    for version in versions:
        payload = dict(version.payload_json or {})
        if str(payload.get("catalog_version") or "") != ctx.catalog_version:
            continue
        return _knowledge_from_formal(version, payload, ctx)
    if versions:
        raise DomainError("CONTEXT_MISMATCH", "正式知识版本绑定的目录与会话目录不一致")
    unpublished = db.scalar(
        select(models.Version.version_id)
        .where(
            models.Version.kind == "knowledge",
            models.Version.dataset_id == ctx.dataset_id,
        )
        .limit(1)
    )
    if unpublished is not None:
        raise DomainError("KB_NOT_PUBLISHED", "当前目录没有已发布知识库")
    return None


def _evidence_refs(tool_name: str, result: Any, catalog_version: str) -> list[dict[str, str]]:
    if isinstance(result, list):
        items = result
    elif isinstance(result, dict) and isinstance(result.get("items"), list):
        items = result["items"]
    elif isinstance(result, dict):
        items = [result]
    else:
        items = []

    refs = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if tool_name in {"locate_knowledge", "get_knowledge_detail"}:
            object_id = item.get("kp_id")
        elif tool_name == "list_knowledge_candidates":
            object_id = item.get("candidate_id") or item.get("relation_id") or item.get("kp_id")
        elif tool_name == "get_knowledge_relations":
            object_id = item.get("relation_id")
        else:
            object_id = None
        if object_id:
            refs.append({"kind": "curriculum", "id": str(object_id), "catalog_version": catalog_version})
    return refs


def _schema(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "function", "function": {"name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required}}}


def build_knowledge_registry(ctx: ServerContext, role: str, task: TaskSpec, data_root: Path, catalog: Catalog) -> ToolRegistry:
    if role not in {"teacher", "student"} or role not in task.roles:
        raise DomainError("FORBIDDEN", "当前角色不能访问该知识任务")
    if not ctx.dataset_id or not ctx.catalog_version:
        raise DomainError("CONTEXT_MISMATCH", "知识工具必须绑定数据集和目录版本")
    if catalog.version != ctx.catalog_version:
        raise DomainError("CONTEXT_MISMATCH", "课程目录与会话绑定版本不一致")
    kb = _formal_from_db(ctx)
    if kb is None:
        # 旧测试与旧部署仍只写本地快照；正式契约下这是回退路径，不是主路径。
        store = KnowledgeStore(data_root)
        kb, manifest = store.load_version(ctx.dataset_id, ctx.catalog_version)
        if manifest.dataset_id != ctx.dataset_id or manifest.catalog_version != ctx.catalog_version:
            raise DomainError("CONTEXT_MISMATCH", "知识库快照不属于当前数据集和目录")
    if kb.status != "verified" and role == "student":
        raise DomainError("KB_NOT_PUBLISHED", "当前目录没有已发布知识库")

    def invoke(fn):
        def call(args):
            args.pop("_call_id", None)
            if "types" in args:
                types = args["types"]
                if not isinstance(types, list) or any(not isinstance(value, str) for value in types):
                    raise DomainError("INVALID_ARGUMENT", "types 必须是字符串数组")
                args["types"] = set(types)
            result = fn(
                kb, catalog, role=role, kb_version=kb.kb_version,
                catalog_version=ctx.catalog_version, **args,
            )
            name = fn.__name__
            return {"status": "succeeded", "data": result,
                    "error": None, "evidence_refs": _evidence_refs(name, result, ctx.catalog_version)}
        return call
    tools: list[RegisteredTool] = []
    allowed = set(task.tools)
    definitions = [
        RegisteredTool(_schema("locate_knowledge", "按知识点名称或别名定位知识与教材页码", {"keyword": {"type": "string"}}, ["keyword"]), invoke(locate_knowledge)),
        RegisteredTool(_schema("get_knowledge_detail", "读取知识点短摘要和来源定位", {"kp_id": {"type": "string"}}, ["kp_id"]), invoke(get_knowledge_detail)),
        RegisteredTool(_schema("get_knowledge_relations", "读取已核对知识关系", {"kp_id": {"type": "string"}, "direction": {"type": "string", "enum": ["neighbors", "upstream", "downstream"]}, "depth": {"type": "integer"}, "types": {"type": "array", "items": {"type": "string", "enum": ["has_property", "derives_from", "composed_of", "special_case"]}}}, ["kp_id"]), invoke(get_knowledge_relations)),
        RegisteredTool(_schema("list_knowledge_candidates", "教师只读查看待核对知识候选", {"status": {"type": "string", "enum": ["unverified", "candidate", "verified", "rejected"]}, "category": {"type": "string", "enum": ["annotation", "relation", "knowledge_point", "merge"]}, "page": {"type": "integer"}, "page_size": {"type": "integer"}}, []), invoke(list_knowledge_candidates)),
    ]
    for tool in definitions:
        if tool.schema["function"]["name"] in allowed and (role == "teacher" or tool.schema["function"]["name"] != "list_knowledge_candidates"):
            tools.append(tool)
    return ToolRegistry(tools)
