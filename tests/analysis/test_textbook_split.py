"""textbook_split 单元测试：页码矫正、章节识别、条目产出结构。

核心数值手工推算；不依赖外部 PDF（正则/矫正逻辑纯函数测试），
端到端跑真切片由 CLI 手动验收（8份已验证）。
"""

from __future__ import annotations

from study_companion.analysis.textbook_split import (
    CHAPTER_RE,
    SECTION_RE,
    _chapter_key,
    check_continuity,
    extract_page_number,
    normalize_page_number,
)

# 1. 页码矫正：全角倒序数字 -> 正确印刷页（A的13号文档实测案例）
def test_normalize_reversed_fullwidth_page_number():
    assert normalize_page_number("３２１") == 123
    assert normalize_page_number("２３１") == 132
    assert normalize_page_number("５４１") == 145
    assert normalize_page_number("２１") == 12  # 两位数同样倒序


# 2. 半角数字页码兼容（万一某切片用半角）
def test_normalize_halfwidth_page_number():
    assert normalize_page_number("12") == 21  # 同样按倒序处理，保持规则一致
    assert normalize_page_number("7") == 7


# 3. 页脚页码行识别：纯数字行取最后一个，忽略版权行
def test_extract_page_number_takes_last_numeric_line():
    lines = ["第十一章　不等式", "正文内容", "３２１", "仅供个人学习使用，未经授权不得另做他用"]
    assert extract_page_number(lines) == "３２１"
    assert extract_page_number(["只有正文", "没有数字行"]) is None


# 4. 章/节标题识别
def test_heading_patterns():
    assert CHAPTER_RE.match("第十一章　不等式与不等式组")
    assert CHAPTER_RE.match("第十章　二元一次方程组")
    assert not CHAPTER_RE.match("正文不以第开头")
    assert SECTION_RE.match("１１．１ 不等式")
    assert SECTION_RE.match("11.1 不等式")  # 半角兼容
    assert SECTION_RE.match("１１．１．２ 不等式的性质")
    assert not SECTION_RE.match("（１）下列式子")


# 5. 章名空白归一：同一章的两种写法视为相同
def test_chapter_key_normalizes_whitespace():
    assert _chapter_key("第十二章数据的收集、整理与描述") == \
           _chapter_key("第十二章　数据的收集、整理与描述")
    assert _chapter_key("第五章　一元一次方程") != _chapter_key("第六章　几何图形初步")


# 6. 连续性校验：正常递增无提示，跳变生成待核对信息
def test_check_continuity():
    ok = check_continuity({1: 100, 2: 101, 3: 102})
    assert ok == []
    issues = check_continuity({1: 100, 2: 101, 3: 1, 4: 103})
    assert len(issues) == 2  # 101->1 和 1->103 两处跳变
    assert "文件页2->3" in issues[0]
    # 缺页码的页不产生误报
    assert check_continuity({1: 100, 2: None, 3: 102}) == []
