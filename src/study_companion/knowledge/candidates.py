"""规则定义句 → 知识候选，去重与增量 ChangeSet（02 §6.2—6.3、§11）。

全部是纯函数，不写数据库、不调用模型：

* 定义句规则：``X是…``、``X称为…``、``把…叫做…``、``若…则…``（以及 ``统称为``）。
  名称 2—20 字、定义 10—160 字、短摘 ≤80 字。规则取不出的句子直接跳过，
  不猜名称、不补内容。
* 候选一律 ``review_status="needs_review"``：规则候选不是正式知识，必须经审核。
* 去重：名称归一（NFKC、去空白、大小写折叠；繁简不转换）后同名合并；定义的
  字符 3-gram Jaccard ≥ 0.85 视为重复。同名但定义差异大者按 02 §6.2
  「命名相同但定义域、条件不同者分开」保留为两个候选，并登记冲突供复核。
* ``change_set`` 只比较两次候选列表，产出 ``{added, removed, renamed, changed}``，
  每项带 ``candidate_id`` 与原因；不加版本号、不落库。

工程初始值（可配置）：``SIMILARITY_THRESHOLD = 0.85``、``NGRAM_SIZE = 3``、
``MIN_NAME_CHARS = 2``、``MAX_NAME_CHARS = 20``、``MIN_DEFINITION_CHARS = 10``、
``MAX_DEFINITION_CHARS = 160``、``MAX_SNIPPET_CHARS = 80``。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Iterable

from study_companion.contracts.base import canonical_json
from study_companion.knowledge.dedup import normalize_definition, normalize_name
from study_companion.knowledge.scope_plan import (
    Chapter,
    ScopePlan,
    is_structural_heading,
)

# 工程初始阈值，不是教学准确率保证。
SIMILARITY_THRESHOLD = 0.85
NGRAM_SIZE = 3
MIN_NAME_CHARS = 2
MAX_NAME_CHARS = 20
MIN_DEFINITION_CHARS = 10
MAX_DEFINITION_CHARS = 160
MAX_SNIPPET_CHARS = 80

REVIEW_STATUS = "needs_review"

_CJK = "\u4e00-\u9fff"
_NAME = rf"[{_CJK}A-Za-z0-9·]"
_TERMINATORS = "。！？；!?;"
# 名称后面允许直接跟标点或结束；后面仍可有补充说明，定义取整句。
_AFTER_NAME = r"(?=[，。；！？、：:!?;]|$)"

# 「X是…」：名称必须在句首，避免把句中任意短语当被定义项。
_IS_RE = re.compile(rf"^(?P<name>{_NAME}{{2,20}})是(?P<body>.+)$")
# 「把/将…叫做…」：被定义的是标记之后的名称。
_CALL_AFTER_RE = re.compile(
    rf"^(?:把|将).{{1,30}}?(?:统称为|合称为|简称|叫做|叫作|称作|称为)"
    rf"(?P<name>{_NAME}{{2,20}}){_AFTER_NAME}"
)
# 「…统称为X」：被定义的是标记之后的名称。
_COLLECT_RE = re.compile(
    rf"^.{{1,30}}?(?:统称为|合称为|简称)(?P<name>{_NAME}{{2,20}}){_AFTER_NAME}"
)
# 「…叫做X」：中文教材通常把被定义项放在标记之后，前文可以是一段描述。
_GENERIC_AFTER_RE = re.compile(
    rf"^.{{0,40}}?(?:叫做|叫作|称作|称为)(?P<name>{_NAME}{{2,20}}){_AFTER_NAME}"
)
# 「X称为…」：被定义的是标记之前的名称（含「的」的短语会被 _valid_name 拒绝）。
_CALL_BEFORE_RE = re.compile(
    rf"^(?P<name>{_NAME}{{2,20}}?)(?:叫做|叫作|称作|称为)"
    rf"(?P<body>.{{2,{MAX_DEFINITION_CHARS}}})$"
)
# 「若…则…」：条件句本身不是名称，名称只能从标记词里确定性取出。
_CONDITION_RE = re.compile(r"^(?:若|如果|假如).{1,90}?(?:则|那么|就)")
_CONDITION_CALLED_RE = re.compile(
    rf"(?:把|将).{{0,30}}?(?:统称为|合称为|简称|叫做|叫作|称作|称为)"
    rf"(?P<name>{_NAME}{{2,20}})"
)
_CONDITION_MUTUAL_RE = re.compile(r"互为(?P<name>[\u4e00-\u9fff]{2,8})")

# 以这些词开头的"名称"是短语或指代，不是知识点名称。
_BAD_NAME_PREFIXES: tuple[str, ...] = (
    "这",
    "那",
    "该",
    "其",
    "它",
    "此",
    "上述",
    "以上",
    "下列",
    "如下",
    "我们",
    "他们",
    "其中",
    "因为",
    "所以",
    "因此",
    "但是",
    "而且",
    "并且",
    "如果",
    "若",
    "当",
    "在",
    "对",
    "对于",
    "所有",
    "每个",
    "任何",
    "一个",
    "一种",
    "有些",
    "有的",
)

METHOD_COPULA = "rule:def_copula"
METHOD_CALLED = "rule:def_called"
METHOD_COLLECTIVE = "rule:def_collective"
METHOD_CONDITIONAL = "rule:def_conditional"


@dataclass
class KnowledgeCandidate:
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
    # 合并后的来源页码（去重会并入多个页码）；单条候选初始为 [page]。
    source_pages: list[int] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# 分句：按行成组，行内再按终止符切句
# --------------------------------------------------------------------------- #


def _line_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start = 0
    for index, char in enumerate(text):
        if char == "\n":
            spans.append((start, index))
            start = index + 1
    spans.append((start, len(text)))
    return spans


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    """返回句子在章节文本中的 [start, end)。

    行尾没有终止符时与下一行合并成一组，处理 PDF 把一句定义折成两行的情况；
    章/节/数字编号标题单独成界，不会被粘进定义句。
    """

    groups: list[tuple[int, int]] = []
    buffer_start: int | None = None
    buffer_end: int | None = None

    def flush() -> None:
        nonlocal buffer_start, buffer_end
        if buffer_start is not None and buffer_end is not None:
            groups.append((buffer_start, buffer_end))
        buffer_start = buffer_end = None

    for start, end in _line_spans(text):
        line = text[start:end]
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        if is_structural_heading(stripped):
            flush()
            continue
        if buffer_start is None:
            buffer_start = start
        buffer_end = end
        if stripped[-1] in _TERMINATORS:
            flush()
    flush()

    sentences: list[tuple[int, int]] = []
    for start, end in groups:
        cursor = start
        for index in range(start, end):
            if text[index] in _TERMINATORS:
                sentences.append((cursor, index + 1))
                cursor = index + 1
        if cursor < end:
            sentences.append((cursor, end))
    return sentences


def _trim(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _flatten(text: str) -> str:
    return " ".join(text.split())


def _valid_name(name: str) -> bool:
    name = name.strip().strip("“”\"'「」『』（）()《》")
    if not name or name.startswith(_BAD_NAME_PREFIXES):
        return False
    if not (MIN_NAME_CHARS <= len(name) <= MAX_NAME_CHARS):
        return False
    if "的" in name:
        return False
    if not any("\u4e00" <= char <= "\u9fff" or char.isalpha() for char in name):
        return False
    return True


def _valid_definition(definition: str) -> bool:
    return MIN_DEFINITION_CHARS <= len(definition) <= MAX_DEFINITION_CHARS


def _clean_name(name: str) -> str:
    return name.strip().strip("“”\"'「」『』（）()《》")


def _page_for(chapter: Chapter, offset: int) -> int:
    """字符偏移 → 文件页；落在页间分隔符时取前一页。"""

    page = chapter.start_page
    for span_page, start, end in chapter.page_spans:
        if start <= offset < end:
            return span_page
        if start <= offset:
            page = span_page
    return page


def _candidate_id(chapter_id: str, page: int, start: int, end: int, name: str) -> str:
    digest = sha256(
        canonical_json(
            {
                "chapter_id": chapter_id,
                "page": page,
                "char_start": start,
                "char_end": end,
                "name": normalize_name(name),
            }
        ).encode("utf-8")
    ).hexdigest()[:16]
    return f"kc-{digest}"


def _content_hash(
    *,
    name: str,
    definition: str,
    chapter_id: str,
    page: int,
    char_start: int,
    char_end: int,
    method: str,
    source_pages: Iterable[int],
) -> str:
    return sha256(
        canonical_json(
            {
                "name": name,
                "definition": definition,
                "chapter_id": chapter_id,
                "page": page,
                "char_start": char_start,
                "char_end": char_end,
                "method": method,
                "source_pages": sorted(set(source_pages)),
            }
        ).encode("utf-8")
    ).hexdigest()


def _build(
    *,
    name: str,
    definition: str,
    chapter: Chapter,
    page: int,
    start: int,
    end: int,
    method: str,
) -> KnowledgeCandidate | None:
    name = _clean_name(name)
    if not _valid_name(name) or not _valid_definition(definition):
        return None
    return KnowledgeCandidate(
        candidate_id=_candidate_id(chapter.chapter_id, page, start, end, name),
        name=name,
        definition=definition,
        snippet=definition[:MAX_SNIPPET_CHARS],
        chapter_id=chapter.chapter_id,
        page=page,
        char_start=start,
        char_end=end,
        method=method,
        review_status=REVIEW_STATUS,
        content_hash=_content_hash(
            name=name,
            definition=definition,
            chapter_id=chapter.chapter_id,
            page=page,
            char_start=start,
            char_end=end,
            method=method,
            source_pages=[page],
        ),
        source_pages=[page],
    )


def _match_rules(definition: str) -> tuple[str, str] | None:
    """返回 (name, method)；不匹配返回 None。顺序固定，先命中先用。"""

    called = _CALL_AFTER_RE.match(definition)
    if called:
        return called.group("name"), METHOD_CALLED
    collected = _COLLECT_RE.match(definition)
    if collected:
        return collected.group("name"), METHOD_COLLECTIVE
    copula = _IS_RE.match(definition)
    if copula:
        return copula.group("name"), METHOD_COPULA
    generic = _GENERIC_AFTER_RE.match(definition)
    if generic:
        return generic.group("name"), METHOD_CALLED
    before = _CALL_BEFORE_RE.match(definition)
    if before:
        return before.group("name"), METHOD_CALLED
    if _CONDITION_RE.match(definition):
        named = _CONDITION_CALLED_RE.search(definition)
        if named:
            return named.group("name"), METHOD_CONDITIONAL
        mutual = _CONDITION_MUTUAL_RE.search(definition)
        if mutual:
            return mutual.group("name"), METHOD_CONDITIONAL
    return None


def extract_definition_candidates(plan: ScopePlan) -> list[KnowledgeCandidate]:
    """按规则从句章节正文里抽候选；不判范围、不调模型、不写库。"""

    candidates: list[KnowledgeCandidate] = []
    for chapter in plan.chapters:
        if not chapter.text.strip():
            continue
        for start, end in _sentence_spans(chapter.text):
            start, end = _trim(chapter.text, start, end)
            if start >= end:
                continue
            definition = _flatten(chapter.text[start:end])
            matched = _match_rules(definition)
            if matched is None:
                continue
            name, method = matched
            page = _page_for(chapter, start)
            candidate = _build(
                name=name,
                definition=definition,
                chapter=chapter,
                page=page,
                start=start,
                end=end,
                method=method,
            )
            if candidate is not None:
                candidates.append(candidate)

    # 同一句可能被多条规则命中：按 (章节, 偏移, 归一化名称) 去重，保留先出现的。
    seen: set[tuple[str, int, int, str]] = set()
    unique: list[KnowledgeCandidate] = []
    for candidate in sorted(
        candidates, key=lambda item: (item.chapter_id, item.page, item.char_start, item.name)
    ):
        key = (
            candidate.chapter_id,
            candidate.char_start,
            candidate.char_end,
            normalize_name(candidate.name),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


# --------------------------------------------------------------------------- #
# 去重
# --------------------------------------------------------------------------- #


def _ngrams(definition: str) -> set[str]:
    text = normalize_definition(definition)
    if not text:
        return set()
    if len(text) <= NGRAM_SIZE:
        return {text}
    return {text[index : index + NGRAM_SIZE] for index in range(len(text) - NGRAM_SIZE + 1)}


def definition_similarity(left: str, right: str) -> float:
    """字符 3-gram Jaccard；空集为 0.0，只用于排序与重复判断，不作语义证明。"""

    left_grams = _ngrams(left)
    right_grams = _ngrams(right)
    if not left_grams or not right_grams:
        return 0.0
    intersection = len(left_grams & right_grams)
    union = len(left_grams | right_grams)
    return 0.0 if union == 0 else intersection / union


def _refresh(candidate: KnowledgeCandidate) -> KnowledgeCandidate:
    candidate.source_pages = sorted(set(candidate.source_pages))
    candidate.snippet = candidate.definition[:MAX_SNIPPET_CHARS]
    candidate.content_hash = _content_hash(
        name=candidate.name,
        definition=candidate.definition,
        chapter_id=candidate.chapter_id,
        page=candidate.page,
        char_start=candidate.char_start,
        char_end=candidate.char_end,
        method=candidate.method,
        source_pages=candidate.source_pages,
    )
    return candidate


def dedupe_candidates(
    candidates: list[KnowledgeCandidate],
) -> tuple[list[KnowledgeCandidate], list[dict]]:
    """同名合并 + 定义 3-gram Jaccard ≥0.85 判重，返回 (候选, 合并报告)。

    报告每项至少含 ``action``（merged / conflict）与 ``reason``；合并项保留
    被合并的候选 ID、名称和来源页码，教师能核对合并是否成立。
    """

    ordered = sorted(
        candidates,
        key=lambda item: (
            item.chapter_id,
            item.page,
            item.char_start,
            normalize_name(item.name),
            item.candidate_id,
        ),
    )
    kept: list[KnowledgeCandidate] = []
    reports: list[dict] = []

    for candidate in ordered:
        merged = False
        for survivor in kept:
            same_name = normalize_name(survivor.name) == normalize_name(candidate.name)
            similarity = definition_similarity(survivor.definition, candidate.definition)
            if same_name and similarity < SIMILARITY_THRESHOLD:
                reports.append(
                    {
                        "action": "conflict",
                        "kept_candidate_id": survivor.candidate_id,
                        "other_candidate_id": candidate.candidate_id,
                        "names": [survivor.name, candidate.name],
                        "pages": sorted({survivor.page, candidate.page}),
                        "similarity": round(similarity, 3),
                        "reason": (
                            "名称归一后相同，但定义 3-gram Jaccard "
                            f"{similarity:.2f} 低于 {SIMILARITY_THRESHOLD}，"
                            "按 02 §6.2「同名但定义域、条件不同者分开」保留为两个候选"
                        ),
                    }
                )
                continue
            if same_name or similarity >= SIMILARITY_THRESHOLD:
                # 合并：保留先出现的候选 ID 与位置，并集来源页，取更完整的定义。
                reports.append(
                    {
                        "action": "merged",
                        "kept_candidate_id": survivor.candidate_id,
                        "merged_candidate_id": candidate.candidate_id,
                        "names": list(dict.fromkeys([survivor.name, candidate.name])),
                        "pages": sorted(set(survivor.source_pages) | set(candidate.source_pages)),
                        "similarity": round(similarity, 3),
                        "basis": "same_name" if same_name else "definition_similarity",
                        "reason": (
                            "名称归一后相同，合并为同一候选"
                            if same_name
                            else f"定义 3-gram Jaccard {similarity:.2f} ≥ {SIMILARITY_THRESHOLD}，判为重复"
                        ),
                    }
                )
                survivor.source_pages = sorted(
                    set(survivor.source_pages) | set(candidate.source_pages)
                )
                if len(candidate.definition) > len(survivor.definition):
                    survivor.definition = candidate.definition
                survivor.source_pages = sorted(
                    set(survivor.source_pages) | {survivor.page}
                )
                _refresh(survivor)
                merged = True
                break
        if not merged:
            kept.append(candidate)

    for survivor in kept:
        _refresh(survivor)
    kept.sort(key=lambda item: (item.chapter_id, item.char_start, item.name))
    return kept, reports


# --------------------------------------------------------------------------- #
# 增量 ChangeSet
# --------------------------------------------------------------------------- #


def _location(candidate: KnowledgeCandidate) -> tuple[str, int, int, int]:
    return (candidate.chapter_id, candidate.page, candidate.char_start, candidate.char_end)


def _changed_fields(previous: KnowledgeCandidate, current: KnowledgeCandidate) -> list[str]:
    fields: list[str] = []
    if normalize_definition(previous.definition) != normalize_definition(current.definition):
        fields.append("definition")
    if previous.page != current.page:
        fields.append("page")
    if previous.chapter_id != current.chapter_id:
        fields.append("chapter_id")
    if (previous.char_start, previous.char_end) != (current.char_start, current.char_end):
        fields.append("char_span")
    if previous.method != current.method:
        fields.append("method")
    if previous.review_status != current.review_status:
        fields.append("review_status")
    if sorted(set(previous.source_pages)) != sorted(set(current.source_pages)):
        fields.append("source_pages")
    return fields


def _rename_entry(previous: KnowledgeCandidate, current: KnowledgeCandidate) -> dict:
    definition_changed = normalize_definition(previous.definition) != normalize_definition(
        current.definition
    )
    return {
        "candidate_id": current.candidate_id,
        "previous_candidate_id": previous.candidate_id,
        "from": previous.name,
        "to": current.name,
        "definition_changed": definition_changed,
        "reason": f"同一候选名称由「{previous.name}」改为「{current.name}」"
        + ("，定义同时变化" if definition_changed else "，定义未变（仅展示更新）"),
    }


def change_set(
    previous: list[KnowledgeCandidate], current: list[KnowledgeCandidate]
) -> dict:
    """比较两批候选，返回 {added, removed, renamed, changed}；不写数据库。"""

    previous_by_id = {item.candidate_id: item for item in previous}
    used_previous: set[str] = set()
    used_current: set[str] = set()
    renamed: list[dict] = []
    changed: list[dict] = []

    def compare(old: KnowledgeCandidate, new: KnowledgeCandidate) -> None:
        if normalize_name(old.name) != normalize_name(new.name):
            renamed.append(_rename_entry(old, new))
            return
        fields = _changed_fields(old, new)
        if fields:
            changed.append(
                {
                    "candidate_id": new.candidate_id,
                    "fields": fields,
                    "reason": "候选内容变化：" + "、".join(fields),
                }
            )

    # 第一轮：候选 ID 相同。
    for candidate in sorted(current, key=lambda item: item.candidate_id):
        old = previous_by_id.get(candidate.candidate_id)
        if old is None or candidate.candidate_id in used_current:
            continue
        used_previous.add(old.candidate_id)
        used_current.add(candidate.candidate_id)
        compare(old, candidate)

    # 第二轮：按来源位置同一处对齐（重命名或内容修订后 ID 会变）。
    remaining_previous = [item for item in previous if item.candidate_id not in used_previous]
    for candidate in sorted(current, key=lambda item: (item.chapter_id, item.char_start)):
        if candidate.candidate_id in used_current:
            continue
        match = next(
            (item for item in remaining_previous if _location(item) == _location(candidate)),
            None,
        )
        if match is None:
            continue
        used_previous.add(match.candidate_id)
        used_current.add(candidate.candidate_id)
        remaining_previous.remove(match)
        compare(match, candidate)

    # 第三轮：名称归一后同名（位置搬动或重新分页）。
    remaining_previous = [item for item in previous if item.candidate_id not in used_previous]
    for candidate in sorted(current, key=lambda item: item.candidate_id):
        if candidate.candidate_id in used_current:
            continue
        folded = normalize_name(candidate.name)
        match = next(
            (item for item in remaining_previous if normalize_name(item.name) == folded), None
        )
        if match is None:
            continue
        used_previous.add(match.candidate_id)
        used_current.add(candidate.candidate_id)
        remaining_previous.remove(match)
        if _location(match) != _location(candidate) and normalize_definition(
            match.definition
        ) == normalize_definition(candidate.definition):
            changed.append(
                {
                    "candidate_id": candidate.candidate_id,
                    "previous_candidate_id": match.candidate_id,
                    "fields": ["location"],
                    "reason": "同一名称与定义，来源页或字符位置变化（疑似重新分页或切块）",
                }
            )
        else:
            compare(match, candidate)

    added = [
        {
            "candidate_id": item.candidate_id,
            "name": item.name,
            "reason": f"新候选：「{item.name}」（{item.chapter_id} 第 {item.page} 页）",
        }
        for item in sorted(current, key=lambda item: item.candidate_id)
        if item.candidate_id not in used_current
    ]
    removed = [
        {
            "candidate_id": item.candidate_id,
            "name": item.name,
            "reason": f"候选消失：「{item.name}」（{item.chapter_id} 第 {item.page} 页）",
        }
        for item in sorted(previous, key=lambda item: item.candidate_id)
        if item.candidate_id not in used_previous
    ]
    return {
        "added": added,
        "removed": removed,
        "renamed": sorted(renamed, key=lambda item: item["candidate_id"]),
        "changed": sorted(changed, key=lambda item: item["candidate_id"]),
    }


__all__ = [
    "MAX_DEFINITION_CHARS",
    "MAX_NAME_CHARS",
    "MAX_SNIPPET_CHARS",
    "MIN_DEFINITION_CHARS",
    "MIN_NAME_CHARS",
    "NGRAM_SIZE",
    "SIMILARITY_THRESHOLD",
    "KnowledgeCandidate",
    "change_set",
    "dedupe_candidates",
    "definition_similarity",
    "extract_definition_candidates",
]
