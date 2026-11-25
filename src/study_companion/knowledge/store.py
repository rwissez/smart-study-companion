"""Immutable local JSON snapshots for dataset/catalog-scoped knowledge bases."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from study_companion.contracts import DomainError
from study_companion.knowledge.models import KnowledgeBase
from study_companion.knowledge.extract import PyPdfExtractor, RapidOcrExtractor

_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class SourceManifestEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    relative_path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    page_count: int = Field(ge=1)
    extraction_method: str
    extraction_version: str


class KnowledgeManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: str
    catalog_version: str
    kb_version: str
    parent_version: str | None = None
    status: str = "draft"
    created_at: str
    source_files: list[SourceManifestEntry] = Field(default_factory=list)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _contained_path(data_root: Path, relative_path: str) -> Path:
    root = data_root.resolve()
    rel = Path(relative_path)
    if rel.is_absolute() or any(part in {"..", ""} for part in rel.parts):
        raise DomainError("INVALID_SOURCE_PATH", "来源路径必须是数据目录内的规范相对路径")
    resolved = (root / rel).resolve()
    if not resolved.is_relative_to(root):
        raise DomainError("INVALID_SOURCE_PATH", "来源路径不能越出数据目录")
    return resolved


def source_manifest_entry(
    data_root: Path,
    source_path: Path,
    *,
    source_id: str,
    page_count: int,
    extraction_method: str,
    extraction_version: str,
) -> SourceManifestEntry:
    root = data_root.resolve()
    resolved = source_path.resolve(strict=True)
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise DomainError("INVALID_SOURCE_PATH", "来源文件必须位于数据目录内")
    return SourceManifestEntry(
        source_id=source_id,
        relative_path=resolved.relative_to(root).as_posix(),
        sha256=sha256_file(resolved),
        page_count=page_count,
        extraction_method=extraction_method,
        extraction_version=extraction_version,
    )


class KnowledgeStore:
    """Versioned filesystem store; version directories are never overwritten."""

    def __init__(self, data_root: Path) -> None:
        self.data_root = data_root.resolve()
        self.root = self.data_root / "knowledge"

    def _library(self, dataset_id: str, catalog_version: str) -> Path:
        self._validate_id(dataset_id, "dataset_id")
        self._validate_id(catalog_version, "catalog_version")
        return self.root / dataset_id / catalog_version

    @staticmethod
    def _validate_id(value: str, name: str) -> None:
        if not _VERSION_RE.fullmatch(value):
            raise DomainError("INVALID_KB_VERSION", f"{name} 格式无效")

    def resolve_source(self, entry: SourceManifestEntry, *, verify_hash: bool = True) -> Path:
        path = _contained_path(self.data_root, entry.relative_path)
        if not path.is_file():
            raise DomainError("SOURCE_NOT_FOUND", "知识库来源文件不存在")
        if verify_hash and sha256_file(path) != entry.sha256:
            raise DomainError("SOURCE_HASH_MISMATCH", "知识库来源文件已变化，拒绝验证旧证据")
        return path

    def current_version(self, dataset_id: str, catalog_version: str) -> str | None:
        pointer = self._library(dataset_id, catalog_version) / "current.json"
        if not pointer.exists():
            return None
        try:
            data = json.loads(pointer.read_text(encoding="utf-8"))
            version = data["kb_version"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise DomainError("KB_POINTER_INVALID", "知识库版本指针无效") from exc
        self._validate_id(version, "kb_version")
        return version

    def load_version(
        self,
        dataset_id: str,
        catalog_version: str,
        kb_version: str | None = None,
        *,
        verify_sources: bool = False,
    ) -> tuple[KnowledgeBase, KnowledgeManifest]:
        version = kb_version or self.current_version(dataset_id, catalog_version)
        if version is None:
            raise DomainError("KB_NOT_FOUND", "当前目录版本没有绑定知识库")
        self._validate_id(version, "kb_version")
        version_dir = self._library(dataset_id, catalog_version) / version
        manifest_path = version_dir / "manifest.json"
        kb_path = version_dir / "knowledge_base.json"
        try:
            manifest = KnowledgeManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
            raw = json.loads(kb_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise DomainError("KB_NOT_FOUND", "知识库版本不存在") from exc
        except (ValueError, OSError) as exc:
            raise DomainError("KB_SNAPSHOT_INVALID", "知识库快照格式无效") from exc
        if (manifest.dataset_id, manifest.catalog_version, manifest.kb_version) != (
            dataset_id, catalog_version, version
        ):
            raise DomainError("KB_CONTEXT_MISMATCH", "知识库版本不属于当前数据集和目录")
        kb = KnowledgeBase.model_validate(raw)
        if kb.kb_version != version:
            raise DomainError("KB_CONTEXT_MISMATCH", "知识库快照版本与 manifest 不一致")
        if verify_sources:
            for source in manifest.source_files:
                self.resolve_source(source)
        return kb, manifest

    def save_draft(
        self,
        dataset_id: str,
        catalog_version: str,
        kb: KnowledgeBase,
        *,
        parent_version: str | None = None,
        source_files: list[SourceManifestEntry] | None = None,
        make_current: bool = True,
        status: str = "draft",
    ) -> KnowledgeBase:
        """Persist only a draft snapshot; verified snapshots use publication."""
        if status != "draft" or kb.status != "draft":
            raise DomainError("DRAFT_ONLY", "save_draft 不能写入 verified 快照")
        return self._save_snapshot(
            dataset_id, catalog_version, kb, parent_version=parent_version,
            source_files=source_files, make_current=make_current, status="draft",
        )

    def _save_snapshot(
        self,
        dataset_id: str,
        catalog_version: str,
        kb: KnowledgeBase,
        *,
        parent_version: str | None,
        source_files: list[SourceManifestEntry] | None,
        make_current: bool,
        status: str,
    ) -> KnowledgeBase:
        library = self._library(dataset_id, catalog_version)
        self._validate_id(kb.kb_version, "kb_version")
        final_dir = library / kb.kb_version
        if final_dir.exists():
            raise DomainError("KB_VERSION_EXISTS", "知识库版本已存在，不能覆盖")
        if parent_version:
            base, _ = self.load_version(dataset_id, catalog_version, parent_version)
            if parent_version != base.kb_version:
                raise DomainError("KB_CONTEXT_MISMATCH", "父版本与快照不一致")
        created_at = kb.created_at or datetime.now(UTC).isoformat()
        kb_snapshot = kb.model_copy(update={
            "parent_version": parent_version, "status": status, "created_at": created_at,
        })
        manifest = KnowledgeManifest(
            dataset_id=dataset_id, catalog_version=catalog_version,
            kb_version=kb_snapshot.kb_version, parent_version=parent_version,
            status=status, created_at=created_at, source_files=source_files or [],
        )
        library.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".kb-stage-", dir=library))
        try:
            (staging / "knowledge_base.json").write_text(
                json.dumps(kb_snapshot.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8", newline="\n",
            )
            (staging / "manifest.json").write_text(
                manifest.model_dump_json(indent=2), encoding="utf-8", newline="\n",
            )
            try:
                final_dir.mkdir()
            except FileExistsError as exc:
                raise DomainError("KB_VERSION_EXISTS", "知识库版本已存在，不能覆盖") from exc
            try:
                for file in staging.iterdir():
                    os.replace(file, final_dir / file.name)
                if make_current:
                    self._atomic_json(library / "current.json", {"kb_version": kb_snapshot.kb_version})
            except Exception:
                shutil.rmtree(final_dir, ignore_errors=True)
                raise
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        return kb_snapshot

    def publish_verified(
        self,
        dataset_id: str,
        catalog_version: str,
        draft_version: str,
        *,
        catalog=None,
        page_text: dict[object, str] | None = None,
        new_version: str | None = None,
    ) -> KnowledgeBase:
        """Publish a reviewed draft as a new immutable verified snapshot.

        Publication never mutates the draft.  Every point, annotation, and relation
        must already be human-reviewed and all references must validate against the
        bound catalog/source pages before a new current version is written.
        """
        from study_companion.knowledge.relations import validate_candidates
        from study_companion.knowledge.targets import validate_target_candidates

        draft, manifest = self.load_version(dataset_id, catalog_version, draft_version, verify_sources=True)
        if draft.status != "draft" or manifest.status != "draft":
            raise DomainError("KB_NOT_DRAFT", "只有 draft 知识库可以发布")
        active_points = [point for point in draft.points if point.verification_status != "rejected"]
        active_annotations = [annotation for annotation in draft.annotations if annotation.candidate_id and annotation.verification_status != "rejected"]
        active_relations = [relation for relation in draft.relations if relation.verification_status != "rejected"]
        if any(point.verification_status != "verified" for point in active_points):
            raise DomainError("UNVERIFIED_KNOWLEDGE", "仍有未核对知识点，不能发布")
        if any(point.definition_locator.verification_status != "verified" for point in active_points):
            raise DomainError("UNVERIFIED_LOCATOR", "仍有未核对来源页，不能发布")
        if any(annotation.verification_status != "verified" for annotation in active_annotations):
            raise DomainError("UNVERIFIED_ANNOTATION", "仍有未核对归属候选，不能发布")
        if any(relation.verification_status != "verified" for relation in active_relations):
            raise DomainError("UNVERIFIED_RELATION", "仍有未核对关系，不能发布")
        if any(candidate.verification_status == "unverified" for candidate in draft.merge_candidates):
            raise DomainError("UNVERIFIED_MERGE", "仍有未核对合并候选，不能发布")
        if catalog is None or catalog.version != catalog_version:
            raise DomainError("CATALOG_REQUIRED", "发布必须提供匹配的 Catalog 快照")
        active_kb = draft.model_copy(update={"points": active_points, "annotations": active_annotations, "relations": active_relations})
        bound_text = self._manifest_page_text(manifest, active_kb)
        manifest_sources = {
            alias
            for entry in manifest.source_files
            for alias in (entry.source_id, entry.relative_path)
        }
        for point in active_points:
            locator = point.definition_locator
            if locator.source_file not in manifest_sources:
                raise DomainError("SOURCE_NOT_FOUND", f"知识点来源未绑定 manifest：{point.kp_id}")
            source_text = bound_text.get((locator.source_file, locator.file_page))
            if source_text is None:
                raise DomainError("SOURCE_PAGE_NOT_FOUND", f"知识点来源页不存在：{point.kp_id}")
            if point.snippet and point.snippet.replace(" ", "") not in source_text.replace(" ", ""):
                raise DomainError("EVIDENCE_NOT_IN_TEXT", f"知识点来源证据无效：{point.kp_id}")
        accepted, rejected = validate_target_candidates(active_kb, catalog, active_annotations, bound_text)
        if rejected or len(accepted) != len(active_annotations):
            raise DomainError("TARGET_VALIDATION_FAILED", "; ".join(f"{r.candidate_id}:{r.code}" for r in rejected))
        accepted_relations, rejected_relations = validate_candidates(active_kb.model_copy(update={"relations": []}), active_relations, bound_text)
        if rejected_relations or len(accepted_relations) != len(active_relations):
            raise DomainError("RELATION_VALIDATION_FAILED", "; ".join(f"{r.relation_id}:{r.code}" for r in rejected_relations))
        version = new_version or f"{draft_version}-verified"
        published = active_kb.model_copy(update={"kb_version": version, "parent_version": draft_version, "status": "verified"})
        return self._save_snapshot(
            dataset_id, catalog_version, published, parent_version=draft_version,
            source_files=manifest.source_files, make_current=True, status="verified",
        )

    def review_candidates(
        self,
        dataset_id: str,
        catalog_version: str,
        base_version: str,
        decisions,
        reviewer: str,
        *,
        new_version: str,
        catalog=None,
    ) -> KnowledgeBase:
        """Apply pure review decisions and persist the resulting immutable draft."""
        from study_companion.knowledge.review import review_candidates as apply_review
        if catalog is None or catalog.version != catalog_version:
            raise DomainError("CATALOG_REQUIRED", "审核必须提供匹配的 Catalog 快照")
        draft, manifest = self.load_version(dataset_id, catalog_version, base_version, verify_sources=True)
        page_text = self._manifest_page_text(manifest, draft)
        reviewed = apply_review(
            base_version, decisions, reviewer, draft, new_version=new_version,
            page_text=page_text, catalog=catalog,
        )
        from study_companion.knowledge.targets import validate_target_candidates
        active = [
            a for a in reviewed.annotations
            if a.candidate_id and a.verification_status == "verified"
            and any(d.candidate_id == a.candidate_id for d in decisions)
        ]
        _, rejected = validate_target_candidates(reviewed, catalog, active, page_text)
        if rejected:
            raise DomainError("TARGET_VALIDATION_FAILED", "; ".join(f"{r.candidate_id}:{r.code}" for r in rejected))
        return self.save_draft(
            dataset_id, catalog_version, reviewed, parent_version=base_version,
            source_files=manifest.source_files, make_current=True,
        )

    def import_legacy_json(
        self, dataset_id: str, catalog_version: str, legacy_path: Path, *,
        kb_version: str, source_files: list[SourceManifestEntry], make_current: bool = True,
    ) -> KnowledgeBase:
        """Explicitly bind a local legacy snapshot as a new unreviewed draft."""
        try:
            source_path = legacy_path.resolve(strict=True)
            if not source_path.is_relative_to(self.data_root) or not source_path.is_file():
                raise DomainError("INVALID_SOURCE_PATH", "Legacy JSON must be inside the data directory")
            kb = KnowledgeBase.model_validate_json(source_path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            raise DomainError("KB_SNAPSHOT_INVALID", "Legacy knowledge JSON is invalid") from exc
        kb.kb_version = kb_version
        kb.parent_version = None
        kb.status = "draft"
        kb.created_at = None
        for point in kb.points:
            point.verification_status = "unverified"
            point.reviewed_by = point.reviewed_at = None
            for locator in [point.definition_locator, *point.source_locators]:
                locator.verification_status = "unverified"
        for annotation in kb.annotations:
            annotation.verification_status = "unverified"
            annotation.reviewed_by = annotation.reviewed_at = None
        for relation in kb.relations:
            relation.verification_status = "candidate"
            relation.reviewed_by = relation.reviewed_at = None
        manifest = KnowledgeManifest(dataset_id=dataset_id, catalog_version=catalog_version,
                                     kb_version=kb_version, created_at=datetime.now(UTC).isoformat(),
                                     source_files=source_files)
        self._manifest_page_text(manifest, kb)
        return self.save_draft(dataset_id, catalog_version, kb, source_files=source_files,
                               make_current=make_current)

    def _manifest_page_text(self, manifest: KnowledgeManifest, kb: KnowledgeBase) -> dict[object, str]:
        """Reread only referenced pages from hash-checked manifest sources."""
        pages: dict[object, str] = {}
        references: set[tuple[str, int]] = set()
        for point in kb.points:
            for loc in [point.definition_locator, *point.source_locators]:
                references.add((loc.source_file, loc.file_page))
        for relation in kb.relations:
            if relation.source_file and relation.evidence_file_page is not None:
                references.add((relation.source_file, relation.evidence_file_page))
        for annotation in kb.annotations:
            if annotation.source_file and annotation.file_page is not None:
                references.add((annotation.source_file, annotation.file_page))
        remaining = set(references)
        seen_aliases: set[str] = set()
        for entry in manifest.source_files:
            aliases = {entry.source_id, entry.relative_path}
            if seen_aliases & aliases:
                raise DomainError("SOURCE_ID_DUPLICATE", "manifest 来源 ID 或路径重复")
            seen_aliases.update(aliases)
            path = self.resolve_source(entry, verify_hash=True)
            requested = {page for source, page in references if source in aliases}
            if any(page < 1 or page > entry.page_count for page in requested):
                raise DomainError("SOURCE_PAGE_NOT_FOUND", "Referenced page is outside the manifest")
            remaining = {(source, page) for source, page in remaining if source not in aliases}
            if not requested:
                continue
            if path.suffix.lower() == ".pdf":
                try:
                    if entry.extraction_method not in {"text", "ocr"}:
                        raise ValueError("Unsupported PDF extraction method")
                    extractor = PyPdfExtractor() if entry.extraction_method == "text" else RapidOcrExtractor()
                    records = extractor.extract(path, pages=sorted(requested))
                except Exception as exc:
                    raise DomainError("SOURCE_READ_FAILED", f"无法读取来源文件：{entry.source_id}") from exc
                for record in records:
                    for alias in aliases:
                        pages[(alias, record.file_page)] = record.text
            elif path.suffix.lower() in {".txt", ".md"} and entry.page_count == 1:
                try:
                    text = path.read_text(encoding="utf-8")
                except (OSError, UnicodeError) as exc:
                    raise DomainError("SOURCE_READ_FAILED", f"无法读取来源文件：{entry.source_id}") from exc
                for alias in aliases:
                    pages[(alias, 1)] = text
            else:
                raise DomainError("SOURCE_READ_FAILED", "Unsupported source format or page count")
        if remaining:
            raise DomainError("SOURCE_NOT_FOUND", "Referenced source is not bound to the manifest")
        return pages

    @staticmethod
    def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".pointer-", dir=path.parent)
        temp = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)
