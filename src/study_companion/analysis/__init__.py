"""B模块：数据导入、质量检查与统计分析。

对外只暴露固定签名 prepare_dataset / analyze_dataset（签名以统一实施规范第四节为准）。
"""

from study_companion.analysis.prepare import prepare_dataset
from study_companion.analysis.service import analyze_dataset

__all__ = ["prepare_dataset", "analyze_dataset"]
