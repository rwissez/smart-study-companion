"""Read-only knowledge query adapters for chat's server-bound context."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from study_companion.agent.loop import RegisteredTool, ToolRegistry
from study_companion.agent.tasks import TaskSpec
from study_companion.contracts import Catalog, DomainError
from study_companion.knowledge.query import get_knowledge_detail, get_knowledge_relations, list_knowledge_candidates, locate_knowledge
from study_companion.knowledge.store import KnowledgeStore


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
from study_companion.tools.b_analysis import ServerContext


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
