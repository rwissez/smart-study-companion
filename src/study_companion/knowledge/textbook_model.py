"""A 教材语义提取与逐点复核（02 §6）。

程序负责切块、校验和去重，模型只负责"从这一块里读出哪些知识点"；
每个候选都要再独立复核一次，通过才进入正式候选，否则留待教师审核。
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

from study_companion.model_service import ModelService, ModelTaskRequest

MAX_CHUNK_PAGES = 4
MAX_CHUNK_CHARS = 10000
MAX_CHUNKS_PER_BUILD = int(os.getenv("A_TEXTBOOK_CHUNK_LIMIT", "6"))
MAX_REVIEW_OBJECTS = 12
ALLOWED_KINDS = {"concept", "procedure", "principle", "fact"}
KIND_ALIASES = {
    "概念": "concept", "定义": "concept", "名词": "concept",
    "性质": "principle", "定理": "principle", "法则": "principle", "规律": "principle", "原理": "principle",
    "方法": "procedure", "步骤": "procedure", "运算": "procedure", "过程": "procedure", "技能": "procedure",
    "事实": "fact", "记号": "fact", "约定": "fact",
    "theorem": "principle", "rule": "principle", "method": "procedure",
}


class PointSource(BaseModel):
    file_id: str = ""
    pdf_page: int = Field(ge=1)
    quote: str = Field(default="", max_length=160)


class ModelPoint(BaseModel):
    local_key: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=40)
    aliases: list[str] = Field(default_factory=list, max_length=5)
    kind: str = "concept"
    definition: str = Field(min_length=1, max_length=200)
    scope_role: Literal["core", "prerequisite"] = "core"
    indicator_candidate_ids: list[str] = Field(default_factory=list, max_length=4)
    topic_candidate_ids: list[str] = Field(default_factory=list, max_length=4)
    sources: list[PointSource] = Field(min_length=1, max_length=3)
    reason: str = Field(default="", max_length=200)


class ModelPointBatch(BaseModel):
    points: list[ModelPoint] = Field(default_factory=list, max_length=8)
    notes: str = Field(default="", max_length=300)


class PointDecision(BaseModel):
    index: int = Field(ge=0)
    decision: Literal["pass", "repair", "reject", "uncertain"]
    reason: str = ""


class PointReview(BaseModel):
    decisions: list[PointDecision] = Field(default_factory=list, max_length=16)


@dataclass
class TextbookPoint:
    """与既有 KnowledgeCandidate 同形的模型候选；额外带映射与审核状态。"""

    candidate_id: str
    name: str
    definition: str
    snippet: str
    chapter_id: str
    page: int
    char_start: int
    char_end: int
    method: str
    review_status: str
    content_hash: str
    file_id: str = ""
    indicator_ids: list[str] = field(default_factory=list)
    topic_ids: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    kind: str = "concept"
    scope_role: str = "core"
    reason: str = ""
    review_note: str = ""


class ScopeDecision(BaseModel):
    chapter_id: str = Field(min_length=1, max_length=64)
    role: Literal["core", "prerequisite", "irrelevant", "unknown"] = "unknown"
    reason: str = Field(default="", max_length=160)


class ScopeScreen(BaseModel):
    decisions: list[ScopeDecision] = Field(default_factory=list, max_length=40)
    notes: str = Field(default="", max_length=300)


@dataclass
class Chunk:
    chapter_id: str
    title: str
    start_page: int
    end_page: int
    text: str
    pages: list[int]
    score: int = 0


def _normalise(text: str) -> str:
    return re.sub(r"\s+", "", text or "").lower()


def _hash(value: Any) -> str:
    import json

    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:32]


def build_chunks(document: Any, scope: Any) -> list[Chunk]:
    """按范围计划的 core/prerequisite 章节切块；每块最多 4 页 / 10000 字符。"""

    page_text = {
        int(getattr(page, "page", 0)): (getattr(page, "text", "") or "")
        for page in getattr(document, "pages", []) or []
        if getattr(page, "quality", "") == "usable"
    }
    chunks: list[Chunk] = []
    ordered_decisions = sorted(
        getattr(scope, "decisions", []) or [],
        key=lambda item: {"core": 0, "prerequisite": 1, "unknown": 2}.get(item.role, 3),
    )
    for decision in ordered_decisions:
        if decision.role not in {"core", "prerequisite", "unknown"}:
            continue
        chapter = next(
            (item for item in scope.chapters if item.chapter_id == decision.chapter_id), None
        )
        if chapter is None:
            continue
        pages = [
            page
            for page in range(chapter.start_page, chapter.end_page + 1)
            if page_text.get(page, "").strip()
        ]
        current: list[int] = []
        current_chars = 0
        for page in pages:
            text = page_text[page]
            if current and (len(current) >= MAX_CHUNK_PAGES or current_chars + len(text) > MAX_CHUNK_CHARS):
                chunks.append(
                    Chunk(
                        chapter_id=chapter.chapter_id,
                        title=chapter.title,
                        start_page=current[0],
                        end_page=current[-1],
                        text="\n".join(
                            f"[第{number}页]\n{page_text[number]}" for number in current
                        ),
                        pages=list(current),
                    )
                )
                current = []
                current_chars = 0
            current.append(page)
            current_chars += len(text)
        if current:
            chunks.append(
                Chunk(
                    chapter_id=chapter.chapter_id,
                    title=chapter.title,
                    start_page=current[0],
                    end_page=current[-1],
                    text="\n".join(f"[第{number}页]\n{page_text[number]}" for number in current),
                    pages=list(current),
                )
            )
    return chunks[:MAX_CHUNKS_PER_BUILD]



def _relevance_terms(catalog: Any) -> list[str]:
    terms: list[str] = []
    for topic in getattr(catalog, "topics", []) or []:
        for attr in ("title", "goal"):
            value = getattr(topic, attr, "") or ""
            terms.extend(part for part in re.split(r"[，。；、\s]+", value) if len(part) >= 3)
    for indicator in getattr(catalog, "indicators", []) or []:
        value = getattr(indicator, "description", "") or ""
        terms.extend(part for part in re.split(r"[，。；、\s]+", value) if len(part) >= 4)
    for keyword in ("一元一次方程", "二元一次方程组", "不等式", "整式", "代数式", "方程", "等式"):
        terms.append(keyword)
    return sorted({item for item in terms if len(item) >= 2})


def build_page_chunks(document: Any, catalog: Any, limit: int = MAX_CHUNKS_PER_BUILD) -> list[Chunk]:
    """按 4 页窗口切块并按课程术语相关度排序，避免坏章节切分把相关页挤掉。"""

    pages = [
        page
        for page in getattr(document, "pages", []) or []
        if getattr(page, "quality", "") == "usable" and (getattr(page, "text", "") or "").strip()
    ]
    terms = _relevance_terms(catalog)
    windows: list[Chunk] = []
    for index in range(0, len(pages), MAX_CHUNK_PAGES):
        window = pages[index : index + MAX_CHUNK_PAGES]
        if not window:
            continue
        text = "\n".join(
            f"[第{getattr(page, 'page', 0)}页]\n{getattr(page, 'text', '')}" for page in window
        )
        score = sum(text.count(term) * len(term) for term in terms)
        windows.append(
            Chunk(
                chapter_id=f"pages-{getattr(window[0], 'page', 0)}-{getattr(window[-1], 'page', 0)}",
                title=f"教材第{getattr(window[0], 'page', 0)}—{getattr(window[-1], 'page', 0)}页",
                start_page=getattr(window[0], "page", 1),
                end_page=getattr(window[-1], "page", 1),
                text=text,
                pages=[getattr(page, "page", 0) for page in window],
                score=score,
            )
        )
    windows.sort(key=lambda item: (-item.score, item.start_page))
    return windows[:limit]

def _catalog_terms(catalog: Any) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    topics: dict[str, str] = {}
    indicators: list[dict[str, str]] = []
    for indicator in getattr(catalog, "indicators", []) or []:
        if getattr(indicator, "topic_id", None):
            topics[indicator.topic_id] = getattr(indicator, "topic_id")
        indicators.append(
            {
                "indicator_id": indicator.indicator_id,
                "topic_id": indicator.topic_id,
                "description": indicator.description,
            }
        )
    return (
        [{"topic_id": key} for key in sorted(topics)],
        indicators,
    )



def _screen_scope(*, service: ModelService, document: Any, scope: Any, catalog: Any) -> tuple[Any, list[str]]:
    """模型逐章筛查相关性；规则没匹配到的章节也交给模型判断（02 §6.1）。"""

    chapters = list(getattr(scope, "chapters", []) or [])
    if not chapters:
        return scope, []
    page_text = {
        int(getattr(page, "page", 0)): (getattr(page, "text", "") or "")
        for page in getattr(document, "pages", []) or []
    }
    payload_chapters = [
        {
            "chapter_id": chapter.chapter_id,
            "title": chapter.title,
            "start_page": chapter.start_page,
            "excerpt": page_text.get(chapter.start_page, "")[:200],
        }
        for chapter in chapters[:40]
    ]
    topics, indicators = _catalog_terms(catalog)
    request = ModelTaskRequest(
        task_key="a.screen_scope.v1",
        prompt_version="1.0",
        namespace="a",
        input_payload={
            "course_topics": topics,
            "indicators": indicators,
            "chapters": payload_chapters,
        },
        allow_external=False,
    )
    result = service.generate_structured(request, ScopeScreen)
    if not result.succeeded or result.value is None:
        return scope, ["教材范围筛查未完成，沿用规则范围"]
    role_by_id = {
        item.chapter_id: item.role
        for item in result.value.decisions
        if item.chapter_id in {chapter.chapter_id for chapter in chapters}
    }
    for decision in getattr(scope, "decisions", []) or []:
        if decision.chapter_id in role_by_id:
            decision.role = role_by_id[decision.chapter_id]
    notes = ["教材范围由模型逐章筛查"] if role_by_id else ["模型未给出章节范围判断，沿用规则范围"]
    return scope, notes

def extract_points_with_model(
    *,
    service: ModelService,
    document: Any,
    scope: Any,
    catalog: Any,
    protected_texts: list[str],
    rule_candidates: list[Any] | None = None,
    max_chunks: int | None = None,
) -> tuple[list[TextbookPoint], dict[str, int], list[str]]:
    """返回 (候选, 页面统计, 限制)。模型不可用时返回空，由调用方走规则候选。"""

    limitations: list[str] = []
    if not service.configured:
        return [], {}, ["模型未配置，教材仅使用规则候选"]
    scope, screen_notes = _screen_scope(
        service=service,
        document=document,
        scope=scope,
        catalog=catalog,
    )
    limitations.extend(screen_notes)
    structural = [
        decision
        for decision in getattr(scope, "decisions", []) or []
        if decision.role in {"core", "prerequisite"}
    ]
    chunks = (
        build_page_chunks(document, catalog, limit=max_chunks or MAX_CHUNKS_PER_BUILD)
        if not structural or len(scope.chapters) > 8
        else build_chunks(document, scope)
    )
    if not chunks:
        return [], {}, ["没有可用的教材页面，未做语义提取"]

    topics, indicators = _catalog_terms(catalog)
    existing_names = {
        _normalise(getattr(item, "name", "") or "") for item in (rule_candidates or [])
    }
    collected: list[TextbookPoint] = []
    processed = 0
    for chunk in chunks:
        hints = [
            {"name": item.name, "definition": item.definition[:120]}
            for item in (rule_candidates or [])
            if getattr(item, "chapter_id", "") == chunk.chapter_id
        ][:6]
        request = ModelTaskRequest(
            task_key="a.extract_points.v1",
            prompt_version="1.0",
            namespace="a",
            input_payload={
                "chapter_title": chunk.title,
                "page_range": [chunk.start_page, chunk.end_page],
                "course_topics": topics,
                "indicators": indicators,
                "rule_candidates": hints,
                "chunk_text": chunk.text,
            },
            allow_external=False,
        )
        result = service.generate_structured(request, ModelPointBatch)
        if not result.succeeded or result.value is None:
            code = (result.error or {}).get("code", "MODEL_BAD_RESPONSE")
            limitations.append(f"教材块 {chunk.start_page}-{chunk.end_page} 提取失败（{code}）")
            continue
        processed += 1
        for point in result.value.points:
            name = point.name.strip()
            raw_kind = point.kind.strip()
            kind = KIND_ALIASES.get(raw_kind, raw_kind)
            if kind not in ALLOWED_KINDS:
                if any(word in raw_kind for word in ("性质", "定理", "法则", "规律", "原理")):
                    kind = "principle"
                elif any(word in raw_kind for word in ("方法", "步骤", "运算", "过程", "技能")):
                    kind = "procedure"
                elif any(word in raw_kind for word in ("概念", "定义", "名词")):
                    kind = "concept"
                elif any(word in raw_kind for word in ("事实", "记号", "约定")):
                    kind = "fact"
            if not name or kind not in ALLOWED_KINDS:
                limitations.append(f"教材候选 {name or '未命名'} 的类型不合法，已丢弃")
                continue
            if _normalise(name) in existing_names:
                continue
            if any(
                protected
                and len(protected.strip()) >= 8
                and protected.strip() in f"{point.definition}{point.name}"
                for protected in protected_texts
            ):
                limitations.append(f"教材候选 {name} 疑似包含题目原文，已丢弃")
                continue
            source_page = point.sources[0].pdf_page
            if not (chunk.start_page <= source_page <= chunk.end_page):
                limitations.append(f"教材候选 {name} 的页码不在块内，已丢弃")
                continue
            indicator_ids = [
                item for item in point.indicator_candidate_ids
                if any(known["indicator_id"] == item for known in indicators)
            ]
            topic_ids = [item for item in point.topic_candidate_ids if item in {t["topic_id"] for t in topics}]
            existing_names.add(_normalise(name))
            collected.append(
                TextbookPoint(
                    candidate_id=f"tp-{_hash({'name': name, 'page': source_page, 'chapter': chunk.chapter_id})}",
                    name=name[:40],
                    definition=point.definition.strip()[:200],
                    snippet=(point.sources[0].quote or point.definition).strip()[:80],
                    chapter_id=chunk.chapter_id,
                    page=source_page,
                    char_start=0,
                    char_end=0,
                    method="model:a.extract_points.v1",
                    review_status="needs_review",
                    content_hash=_hash(
                        {"name": name, "definition": point.definition, "page": source_page}
                    ),
                    file_id=point.sources[0].file_id,
                    indicator_ids=indicator_ids,
                    topic_ids=topic_ids,
                    aliases=point.aliases,
                    kind=kind,
                    scope_role=point.scope_role,
                    reason=point.reason,
                )
            )

    if not collected:
        limitations.append("教材块没有产生可用候选")
        return [], {"pages_processed": processed, "chunks": len(chunks)}, limitations

    review_subset = collected[:MAX_REVIEW_OBJECTS]
    request = ModelTaskRequest(
        task_key="a.review_point.v1",
        prompt_version="1.0",
        namespace="a",
        input_payload={
            "points": [
                {
                    "name": item.name,
                    "definition": item.definition,
                    "kind": item.kind,
                    "page": item.page,
                    "quote": item.snippet,
                }
                for item in review_subset
            ],
            "indicators": indicators,
        },
        allow_external=False,
    )
    review = service.generate_structured(request, PointReview)
    if not review.succeeded or review.value is None:
        limitations.append("教材候选独立复核未完成，全部转入待审核")
    else:
        passed = {
            item.index
            for item in review.value.decisions
            if item.decision == "pass"
        }
        rejected = {
            item.index: item.reason
            for item in review.value.decisions
            if item.decision in {"reject", "uncertain"}
        }
        for index, item in enumerate(review_subset):
            if index in passed:
                if not item.indicator_ids and not item.topic_ids:
                    item.review_status = "needs_review"
                    item.review_note = "未能关联到课程指标或主题，保留待教师审核"
                    limitations.append(f"教材候选 {item.name} 未关联课程范围，保留待审")
                else:
                    item.review_status = "validated"
                    item.review_note = "独立复核通过"
            else:
                item.review_status = "needs_review"
                item.review_note = rejected.get(index, "复核未通过或未给出结论")
                limitations.append(f"教材候选 {item.name} 未通过复核：{item.review_note[:80]}")

    stats = {
        "pages_processed": processed,
        "chunks": len(chunks),
        "candidates": len(collected),
        "validated": sum(1 for item in collected if item.review_status == "validated"),
    }
    return collected, stats, limitations
