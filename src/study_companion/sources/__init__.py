"""材料来源精度：精确核验、知识点投影与只读审计。

本包不修改正式数据库或正式导出；调用方传入材料、原始材料行、教材页面文本，
得到逐条核验结论与可接入导出的 ``source_refs`` 字段。
"""

from __future__ import annotations

from .audit import AuditInputs, build_audit_report, load_database_inputs, run_audit
from .enrich import enrich_sources
from .knowledge import (
    KnowledgePointRef,
    KnowledgePointSource,
    KnowledgeProjection,
    project_knowledge_refs,
    project_knowledge_sources,
    summarize_knowledge_refs,
    summarize_knowledge_sources,
)
from .models import (
    EnrichedSource,
    EnrichmentReport,
    MaterialInput,
    RawMaterialRow,
    TextbookPage,
    VerificationStatus,
)

__all__ = [
    "AuditInputs",
    "EnrichedSource",
    "EnrichmentReport",
    "KnowledgePointRef",
    "KnowledgePointSource",
    "KnowledgeProjection",
    "MaterialInput",
    "RawMaterialRow",
    "TextbookPage",
    "VerificationStatus",
    "build_audit_report",
    "enrich_sources",
    "load_database_inputs",
    "project_knowledge_refs",
    "project_knowledge_sources",
    "run_audit",
    "summarize_knowledge_refs",
    "summarize_knowledge_sources",
]
