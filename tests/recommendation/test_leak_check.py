"""泄答校验测试（15 号文档 §六 指定的用例清单）。

正例：提示写了答案、完整解题过程、参考答案的同义改写 → 必须判泄漏
反例：短答案（是/否/无解/0/1）出现在正常中文提示中 → 不得误判
边界：参考答案含空格与全角符号、提示用 ≤ 而参考答案用 <=
"""

from study_companion.recommendation.leak_check import check_hint, normalize


# ---- normalize 基础行为 ----

def test_normalize_fullwidth_and_symbols():
    assert normalize("ｘ＝９") == "x=9"
    assert normalize("x ≤ 3") == "x<=3"
    assert normalize("× ÷") == "*/"


def test_normalize_prefixes_and_punctuation():
    assert normalize("解：答：x=9。") == "x=9"
    assert normalize("∴ 两边减3") == "两边减3"


def test_normalize_leading_zeros():
    assert normalize("09") == "9"
    assert normalize("10") == "10"
    assert normalize("0.5") == "0.5"


# ---- 正例：必须判泄漏 ----

def test_hint_contains_reference_answer_value():
    # 提示里直接写了答案值（= 之后出现）
    assert check_hint("x=9", None, "解：两边减3得 x=9") is not None


def test_hint_contains_full_solution_process():
    # 子串包含：提示整段照抄解析
    reference = "2x+3<11，两边同时减3得2x<8，再两边除以2即x<4"
    assert check_hint(reference, None, f"思路：{reference}") is not None


def test_hint_paraphrase_high_gram_overlap():
    # 同义改写（局部换词），3-gram 重合率 >= 0.8 判泄漏
    reference = "2x+3<11，两边同时减3得2x<8，再两边除以2即x<4"
    hint = "2x+3<11，两边同时减3得2x<8，然后两边除以2即x<4"
    assert check_hint(reference, None, hint) is not None


def test_hint_paraphrase_with_fullwidth_symbols():
    # 同义改写 + 全角符号，归一化后命中（整段末尾出现答案）
    assert check_hint("x=9", None, "所以 ｘ＝９") is not None


def test_hint_leaks_reference_explanation():
    explanation = "两边减3后除以2，得x=9"
    assert check_hint("9", explanation, "可以直接两边减3后除以2，得x=9") is not None


def test_hint_inequality_answer_after_so():
    # 短答案「无解」出现在「所以」之后 → 泄漏
    assert check_hint("无解", None, "提示：所以无解") is not None


# ---- 反例：不得误判 ----

def test_short_answer_shi_not_misjudged():
    assert check_hint("是", None, "先判断不等式是否成立") is None


def test_short_answer_fou_not_misjudged():
    assert check_hint("否", None, "完成后再对照检查答案是否正确") is None


def test_short_answer_wujie_not_misjudged():
    # 「无解释」中含「无解」二字，但不在答案位
    assert check_hint("无解", None, "这里无解释必要") is None


def test_short_answer_zero_not_misjudged():
    assert check_hint("0", None, "两边同时加0可以吗") is None


def test_short_answer_one_not_misjudged():
    assert check_hint("1", None, "第1步先移项") is None


def test_answer_embedded_in_longer_number():
    # 答案 9 嵌在 19 里，不是独立出现
    assert check_hint("9", None, "先算出19再比较") is None


def test_no_reference_no_leak():
    assert check_hint(None, None, "两边同时减3试试") is None


# ---- 边界 ----

def test_reference_with_spaces_and_fullwidth():
    # 参考答案含空格与全角字符，归一化吸收后命中
    assert check_hint("x = ９", None, "解：得 x=9") is not None


def test_le_symbol_vs_leq_reference():
    # 提示用 ≤、参考答案用 <=，归一化统一后命中
    assert check_hint("x<=3", None, "当 x≤3 时成立") is not None


def test_long_answer_low_overlap_not_leak():
    # 长参考答案与提示只有零星重合 → 不判泄漏
    reference = "2x+3<11，两边同时减3得2x<8，再两边除以2即x<4"
    hint = "先把不等式整理成标准形式，再判断不等号方向是否变化"
    assert check_hint(reference, None, hint) is None
