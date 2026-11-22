"""S2 规则抽取：逐页登记"新出现"的知识点，并给出关系候选。

规则全部可复核：定义句式（叫作/叫做/称为/称作、组成……）和"X的性质N"标题。
结果一律 unverified/candidate，由人核对。模型不是必需的。
"""

from __future__ import annotations

import re

from study_companion.knowledge.extract import PageRecord
from study_companion.knowledge.models import (
    KnowledgeBase,
    KnowledgePoint,
    Locator,
    Relation,
)

DEF_RE = re.compile(r"(?:叫作|叫做|称为|称作)(?P<t>[^，,。.；;：:（(\s]{2,20})")
COMPOSE_RE = re.compile(r"组成(?:这个|该)?(?P<t>[^，,。.；;（(\s]{2,12})")
PROP_RE = re.compile(r"^(?P<t>[^\s，,。.：:（(]{1,12}的性质\s?\d+)(?:\s|$)", re.M)
ALIAS_RE = re.compile(r"^\s*[（(]\s*(?P<a>[A-Za-z][A-Za-z ]{1,30}?)\s*[)）]")
SENT_SPLIT_RE = re.compile(r"(?<!\d)\.|\.(?!\d)|[。；．]")
ANALOGY_RE = re.compile(r"(?:类似于|类比|与.{0,6}类似)(?P<ref>[^，,。.；;]{2,12})")

STRIP_PREFIXES = ("由它们所组成的", "由它们组成的", "这样的", "所谓", "一个", "一组", "这个", "做")


def _clean_term(term: str) -> str:
    term = term.strip("“”\"'「」 ")
    for p in STRIP_PREFIXES:
        if term.startswith(p) and len(term) > len(p) + 1:
            term = term[len(p):]
    return term


def _flat(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _sentences(text: str) -> list[str]:
    flat = text.replace("\n", "")
    return [s for s in SENT_SPLIT_RE.split(flat) if s.strip()]


class _Registry:
    """维护已登记术语，按教材顺序判定"新出现"。"""

    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.by_name: dict[str, KnowledgePoint] = {}
        self.points: list[KnowledgePoint] = []

    def add(self, name: str, kind: str, loc: Locator, snippet: str, alias: str | None = None) -> bool:
        key = _flat(name)
        if key in self.by_name:
            return False
        kp = KnowledgePoint(
            kp_id=f"KP-{self.prefix}-{len(self.points) + 1:04d}",
            name=name,
            aliases=[alias] if alias else [],
            kind=kind,  # type: ignore[arg-type]
            definition_locator=loc,
            snippet=snippet[:80],
        )
        self.by_name[key] = kp
        self.points.append(kp)
        return True


def extract_knowledge(
    pages: list[PageRecord],
    locators: dict[int, Locator],
    prefix: str,
    kb_version: str,
) -> KnowledgeBase:
    reg = _Registry(prefix)
    relations: list[Relation] = []

    for page in sorted(pages, key=lambda p: p.file_page):
        loc = locators[page.file_page]

        # 1. "X的性质N" 标题 -> 性质类知识点
        for m in PROP_RE.finditer(page.text):
            name = _clean_term(m.group("t"))
            line_end = page.text.find("\n", m.end())
            snippet = page.text[m.start(): line_end if line_end != -1 else None]
            reg.add(name, "property", loc, snippet)

        # 2. 定义句
        for sent in _sentences(page.text):
            for rx in (DEF_RE, COMPOSE_RE):
                m = rx.search(sent)
                if not m:
                    continue
                name = _clean_term(m.group("t"))
                if len(name) < 2 or name.startswith(("的", "是", "了")):
                    continue
                alias = None
                tail = sent[m.end():]
                am = ALIAS_RE.match(tail)
                if am:
                    alias = am.group("a").strip()
                added = reg.add(name, "concept", loc, sent[max(0, m.start() - 30): m.end() + 10], alias)
                if added:
                    kp = reg.by_name[_flat(name)]
                    ana = ANALOGY_RE.search(sent)
                    if ana:
                        ref = reg.by_name.get(_flat(ana.group("ref")))
                        if ref is not None and ref.kp_id != kp.kp_id:
                            relations.append(
                                Relation(
                                    relation_id=f"REL-{prefix}-{len(relations) + 1:04d}",
                                    type="derives_from",
                                    source_ids=[ref.kp_id],
                                    target_id=kp.kp_id,
                                    basis="textbook",
                                    evidence=ana.group(0),
                                    evidence_file_page=page.file_page,
                                    note="规则：定义句含类比提示词",
                                    proposer="rule",
                                )
                            )
                break

    # 3. 性质 -> 概念：名称形如"{概念}的性质N"，且概念已登记
    for kp in reg.points:
        if kp.kind != "property":
            continue
        concept_name = kp.name.split("的性质")[0]
        owner = reg.by_name.get(_flat(concept_name))
        if owner is not None:
            relations.append(
                Relation(
                    relation_id=f"REL-{prefix}-{len(relations) + 1:04d}",
                    type="has_property",
                    source_ids=[owner.kp_id],
                    target_id=kp.kp_id,
                    basis="textbook",
                    evidence=kp.name,
                    evidence_file_page=kp.definition_locator.file_page,
                    note="规则：名称为“X的性质N”且 X 已登记",
                    proposer="rule",
                )
            )

    return KnowledgeBase(kb_version=kb_version, points=reg.points, relations=relations)
