"""C 模块：材料检索、规则建议生成、证据校验。

对外只暴露固定签名（统一实施规范第四节）：
- search_materials(catalog, request) -> list[Material]
- recommend_rules(analysis, catalog, context, config) -> RecommendationBundle
- validate_recommendations(bundle, analysis, catalog) -> ValidationResult
"""

from study_companion.recommendation.service import (
    classify_finding,
    recommend_rules,
    search_materials,
    validate_recommendations,
)

__all__ = [
    "classify_finding",
    "recommend_rules",
    "search_materials",
    "validate_recommendations",
]
