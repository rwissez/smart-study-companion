"""来源核验用的文本规范化与精确匹配。

两条规则贯穿全模块：

* 材料行匹配只做 Unicode NFC、换行统一和首尾空白清理，不折叠内部空白，也不
  做标点、全半角或近似改写；差一个字符就不算命中。
* 教材 PDF 文本允许忽略全部排版空白（含换行、全角空格），因为 PDF 抽取会在
  字间、标点后插入空白。这个处理只消除排版差异，非空白字符仍须完全一致。
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

_WHITESPACE = re.compile(r"\s+", re.UNICODE)

_MATERIAL_TYPE_ALIASES = {
    "题目": "question",
    "学习卡": "card",
    "教材": "textbook",
    "question": "question",
    "card": "card",
    "textbook": "textbook",
}


def normalize_exact(value: Any) -> str:
    """精确比较键：NFC、统一换行、去首尾空白，保留内部空白与全部字符。"""

    if value is None:
        return ""
    text = unicodedata.normalize("NFC", str(value))
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def exact_key(value: Any) -> str:
    return normalize_exact(value)


def normalize_material_type(value: Any) -> str:
    """把原始表的中文材料类型或正式英文类型统一成正式值。"""

    raw = normalize_exact(value).lower()
    return _MATERIAL_TYPE_ALIASES.get(raw, raw)


def _compact_with_map(text: str) -> tuple[str, list[int]]:
    """去掉全部空白，并记录每个保留字符在原文的下标。"""

    chars: list[str] = []
    mapping: list[int] = []
    for index, char in enumerate(text):
        if char.isspace():
            continue
        chars.append(char)
        mapping.append(index)
    return "".join(chars), mapping


def compact_whitespace(value: Any) -> str:
    text = normalize_exact(value)
    compacted, _ = _compact_with_map(text)
    return compacted


def find_quote(page_text: str, content: str) -> str | None:
    """在页面文本中找逐字相同的片段，返回原文片段；找不到返回 ``None``。

    先做直接子串匹配；失败后再做“仅忽略排版空白”的匹配。两种方式都要求非空白
    字符序列完整相同，不做前缀、后缀、编辑距离或标点归一。
    """

    page = normalize_exact(page_text)
    needle = normalize_exact(content)
    if not page or not needle:
        return None
    if needle in page:
        return needle

    compact_page, mapping = _compact_with_map(page)
    compact_needle = compact_whitespace(needle)
    if not compact_needle:
        return None
    index = compact_page.find(compact_needle)
    if index < 0:
        return None
    start = mapping[index]
    end = mapping[index + len(compact_needle) - 1] + 1
    return page[start:end]


def file_name(value: Any) -> str:
    """取来源路径的文件名，兼容 Windows 与 POSIX 分隔符。"""

    text = normalize_exact(value).replace("\\", "/")
    return text.rsplit("/", 1)[-1]


def is_probably_chapter_label(content: Any) -> bool:
    """判断内容是否只是章节标题/目录式标签。

    仅用于审计说明，不参与是否写入页码的判定；核验只看逐字命中。
    """

    text = normalize_exact(content)
    return bool(
        re.fullmatch(
            r"(?:(?:七|八|九)年级[上下]册\s*)?"
            r"第[一二三四五六七八九十百零〇0-9]+章"
            r"(?:\s*[；;]\s*第[一二三四五六七八九十百零〇0-9]+章)?.*",
            text,
        )
    )
