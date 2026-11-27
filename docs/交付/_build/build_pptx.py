"""生成《湛知演示_v1.0.pptx》答辩 PPT 初稿（14 页）。

按 docs/交付/演示脚本与PPT提纲.md 的 14 页提纲排版：
每页 = 标题 + 3—5 条要点 + 截图/图示占位框 + TODO 数字占位卡。
不含任何最终截图或伪造界面，占位框内注明待补编号。

用法（仓库根目录）：
    python "docs/交付/_build/build_pptx.py"
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.dml import MSO_LINE_DASH_STYLE
from pptx.enum.shapes import MSO_SHAPE, MSO_SHAPE_TYPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt
from pptx.oxml.xmlchemy import OxmlElement

ROOT = Path(__file__).resolve().parents[3]
OUT_DIR = ROOT / "docs" / "交付"
OUT = OUT_DIR / "湛知演示_v1.0.pptx"

# 品牌色
INK = "14241D"
GREEN = "0E5A43"
GREEN_DEEP = "0A392A"
MINT = "E9F3EE"
MINT_2 = "F6FAF8"
SAND = "F2EBD9"
GOLD = "B08A2E"
MUTED = "66756D"
LINE = "D8E3DC"
WHITE = "FFFFFF"
PLACEHOLDER_LINE = "A9BAB1"
PLACEHOLDER_TEXT = "9FB4A9"
DARK_MINT = "BFD8CB"
DARK_MUTED = "9FBFB0"

FONT_CN = "Microsoft YaHei"
FONT_MONO = "Consolas"

TOTAL = 14


def rgb(value: str) -> RGBColor:
    return RGBColor.from_string(value)


def set_run(run, text: str, size: float, color: str, bold: bool = False, font: str = FONT_CN, spacing: int | None = None) -> None:
    run.text = text
    run.font.name = font
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = rgb(color)
    rPr = run._r.get_or_add_rPr()
    if spacing is not None:
        rPr.set("spc", str(spacing))
    ea = rPr.find(qn("a:ea"))
    if ea is None:
        ea = OxmlElement("a:ea")
        rPr.append(ea)
    ea.set("typeface", FONT_CN)


def add_text(
    slide,
    x: float,
    y: float,
    w: float,
    h: float,
    name: str = "",
    anchor=MSO_ANCHOR.TOP,
    align=PP_ALIGN.LEFT,
):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    if name:
        box.name = name
    tf = box.text_frame
    tf.word_wrap = True
    tf.auto_size = None
    tf.vertical_anchor = anchor
    tf.margin_left = 0
    tf.margin_right = 0
    tf.margin_top = 0
    tf.margin_bottom = 0
    p = tf.paragraphs[0]
    p.alignment = align
    return box, tf


def add_line(slide, x: float, y: float, w: float, h: float, color: str, width: float = 0.75, name: str = "") -> None:
    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
    shape.name = name or "Rule"
    shape.shadow.inherit = False
    shape.fill.solid()
    shape.fill.fore_color.rgb = rgb(color)
    shape.line.fill.background()


def add_rect(
    slide,
    x: float,
    y: float,
    w: float,
    h: float,
    fill: str | None,
    line: str | None = None,
    line_width: float = 0.9,
    radius: float | None = None,
    dash: bool = False,
    name: str = "",
):
    shp_type = MSO_SHAPE.ROUNDED_RECTANGLE if radius is not None else MSO_SHAPE.RECTANGLE
    shape = slide.shapes.add_shape(shp_type, Inches(x), Inches(y), Inches(w), Inches(h))
    if name:
        shape.name = name
    shape.shadow.inherit = False
    if radius is not None:
        shape.adjustments[0] = radius
    if fill:
        shape.fill.solid()
        shape.fill.fore_color.rgb = rgb(fill)
    else:
        shape.fill.background()
    if line:
        shape.line.color.rgb = rgb(line)
        shape.line.width = Pt(line_width)
        if dash:
            shape.line.dash_style = MSO_LINE_DASH_STYLE.DASH
    else:
        shape.line.fill.background()
    return shape


def add_wordmark(slide) -> None:
    box, tf = add_text(slide, 11.55, 0.34, 1.15, 0.3, name="Wordmark", align=PP_ALIGN.RIGHT)
    p = tf.paragraphs[0]
    r = p.add_run()
    set_run(r, "湛知", 10.5, GREEN, bold=True, spacing=60)
    r2 = p.add_run()
    set_run(r2, "  ZHANZHI", 8, MUTED, spacing=20)


def add_footer(slide, page: int) -> None:
    add_line(slide, 0.72, 7.03, 11.9, 0.009, LINE)
    box, tf = add_text(slide, 0.72, 7.11, 9.2, 0.22, name="Footer")
    p = tf.paragraphs[0]
    r = p.add_run()
    set_run(r, "湛知 · 有依据的师生智慧学伴", 8.5, MUTED)
    r2 = p.add_run()
    set_run(r2, "   ｜   答辩 PPT 初稿 v0.1 · 占位待填", 8.5, PLACEHOLDER_TEXT)
    box2, tf2 = add_text(slide, 11.35, 7.11, 1.27, 0.22, name="PageNo", align=PP_ALIGN.RIGHT)
    p2 = tf2.paragraphs[0]
    r3 = p2.add_run()
    set_run(r3, f"{page:02d} / {TOTAL}", 8.5, GREEN, bold=True, font=FONT_MONO)


def add_screenshot_frame(slide, shots: str, title: str, note: str = "", diagram: bool = False) -> None:
    x, y, w, h = 6.95, 1.9, 5.68, 3.35
    frame = add_rect(slide, x, y, w, h, MINT_2, PLACEHOLDER_LINE, 1.1, radius=0.035, dash=True, name="ScreenshotPlaceholder")

    # 图片图标：两张叠放的矩形，只作占位符号
    add_rect(slide, x + 4.72, y + 0.28, 0.5, 0.38, "E3EFE8", "C9D8D0", 0.75, radius=0.08, name="PlaceholderIcon")
    add_rect(slide, x + 4.85, y + 0.4, 0.5, 0.38, "E3EFE8", "C9D8D0", 0.75, radius=0.08, name="PlaceholderIcon")

    pill = add_rect(slide, x + 0.24, y + 0.22, 1.18, 0.3, MINT, None, radius=0.25, name="PlaceholderPill")
    tf = pill.text_frame
    tf.word_wrap = False
    tf.margin_left = 0
    tf.margin_right = 0
    tf.margin_top = 0
    tf.margin_bottom = 0
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    r = p.add_run()
    set_run(r, "图示占位" if diagram else "截图 / 图示占位", 8.5, GREEN, bold=True)

    box, tf = add_text(slide, x + 0.28, y + 0.7, 5.05, 0.55, name="ShotId")
    p = tf.paragraphs[0]
    r = p.add_run()
    id_size = 22 if (diagram or len(shots) > 16) else 26
    set_run(r, shots, id_size, PLACEHOLDER_TEXT, bold=True, font=FONT_MONO, spacing=20)

    box2, tf2 = add_text(slide, x + 0.28, y + 1.38, 5.05, 0.95, name="ShotDesc")
    p2 = tf2.paragraphs[0]
    p2.line_spacing = 1.25
    r2 = p2.add_run()
    set_run(r2, title, 11, MUTED)

    if note:
        box3, tf3 = add_text(slide, x + 0.28, y + 2.35, 5.05, 0.55, name="ShotNote")
        p3 = tf3.paragraphs[0]
        p3.line_spacing = 1.2
        r3 = p3.add_run()
        set_run(r3, note, 8.5, PLACEHOLDER_TEXT)

    box4, tf4 = add_text(slide, x + 0.28, y + 2.97, 5.05, 0.24, name="ShotTodo")
    p4 = tf4.paragraphs[0]
    r4 = p4.add_run()
    bottom = "本页为示意图 · 不需要截图 · TODO-14" if diagram else "1920×1080 · 待补最终截图 · 不采用原型图或合成界面 · TODO-14"
    set_run(r4, bottom, 8, "B3C2BA")


def add_todo_card(slide, items: list[tuple[str, str]]) -> None:
    x, y, w, h = 6.95, 5.42, 5.68, 1.12
    add_rect(slide, x, y, w, h, WHITE, LINE, 0.75, radius=0.04, name="TodoCard")
    add_rect(slide, x, y + 0.12, 0.045, h - 0.24, GOLD, None, name="TodoAccent")
    box, tf = add_text(slide, x + 0.2, y + 0.11, 2.4, 0.22, name="TodoLabel")
    p = tf.paragraphs[0]
    r = p.add_run()
    set_run(r, "数据待填", 9, GREEN, bold=True, spacing=60)

    box2, tf2 = add_text(slide, x + 0.2, y + 0.36, w - 0.38, h - 0.42, name="TodoItems")
    first = True
    for code, label in items:
        p2 = tf2.paragraphs[0] if first else tf2.add_paragraph()
        first = False
        p2.line_spacing = 1.08
        p2.space_after = Pt(2)
        r_code = p2.add_run()
        set_run(r_code, code, 9, GREEN, bold=True, font=FONT_MONO)
        r_label = p2.add_run()
        set_run(r_label, "  " + label, 9, MUTED)

    if len(items) <= 2:
        box3, tf3 = add_text(slide, x + 0.2, y + 0.91, w - 0.38, 0.16, name="TodoNote")
        p3 = tf3.paragraphs[0]
        r3 = p3.add_run()
        set_run(r3, "替换后删除 TODO 标记，保留来源注释", 7.5, "A6B4AC")


def add_bullets(slide, items: list[str], size: float = 14.5) -> None:
    box, tf = add_text(slide, 0.72, 2.02, 5.9, 3.18, name="Bullets")
    first = True
    for text in items:
        p = tf.paragraphs[0] if first else tf.add_paragraph()
        first = False
        p.line_spacing = 1.18
        p.space_after = Pt(8)
        pPr = p._p.get_or_add_pPr()
        pPr.set("marL", str(int(Inches(0.27))))
        pPr.set("indent", str(-int(Inches(0.27))))
        bullet = p.add_run()
        set_run(bullet, "▪  ", size, GREEN, bold=True)
        if "：" in text:
            head, tail = text.split("：", 1)
            r_head = p.add_run()
            set_run(r_head, head + "：", size, GREEN, bold=True)
            r_tail = p.add_run()
            set_run(r_tail, tail, size, INK)
        else:
            r_body = p.add_run()
            set_run(r_body, text, size, INK)


def add_content_slide(prs, page: int, section: str, title: str, claim: str, bullets: list[str], shots: str, shot_title: str, shot_note: str, todos: list[tuple[str, str]], notes: str, bullet_size: float = 14.5, diagram: bool = False):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = rgb(WHITE)

    # 左侧品牌竖条
    add_rect(slide, 0, 0, 0.12, 7.5, GREEN, None, name="AccentBar")
    add_rect(slide, 0, 0, 0.12, 1.05, GOLD, None, name="AccentCap")
    add_wordmark(slide)

    box, tf = add_text(slide, 0.72, 0.34, 8.6, 0.3, name="Section")
    p = tf.paragraphs[0]
    r = p.add_run()
    set_run(r, section, 9.5, GOLD, bold=True, spacing=100)

    box2, tf2 = add_text(slide, 0.7, 0.64, 11.9, 0.75, name="Title")
    p2 = tf2.paragraphs[0]
    p2.line_spacing = 0.98
    r2 = p2.add_run()
    set_run(r2, title, 28, INK, bold=True)

    box3, tf3 = add_text(slide, 0.72, 1.4, 11.8, 0.32, name="Claim")
    p3 = tf3.paragraphs[0]
    r3 = p3.add_run()
    set_run(r3, claim, 12, GREEN)

    add_line(slide, 0.72, 1.8, 11.9, 0.009, LINE)
    add_bullets(slide, bullets, bullet_size)
    add_screenshot_frame(slide, shots, shot_title, shot_note, diagram=diagram)
    add_todo_card(slide, todos)
    add_footer(slide, page)

    slide.notes_slide.notes_text_frame.text = notes
    return slide


def add_cover(prs) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = rgb(GREEN_DEEP)

    # 装饰弧线
    oval1 = slide.shapes.add_shape(MSO_SHAPE.OVAL, Inches(9.6), Inches(-1.7), Inches(6.2), Inches(6.2))
    oval1.name = "Decor"
    oval1.shadow.inherit = False
    oval1.fill.background()
    oval1.line.color.rgb = rgb("17503C")
    oval1.line.width = Pt(1.1)
    oval2 = slide.shapes.add_shape(MSO_SHAPE.OVAL, Inches(10.9), Inches(4.4), Inches(3.4), Inches(3.4))
    oval2.name = "Decor"
    oval2.shadow.inherit = False
    oval2.fill.background()
    oval2.line.color.rgb = rgb("17503C")
    oval2.line.width = Pt(1.1)

    # 顶部标识与初稿徽标
    box, tf = add_text(slide, 0.72, 0.5, 6.5, 0.3, name="Kicker")
    p = tf.paragraphs[0]
    r = p.add_run()
    set_run(r, "智慧学伴赛题 · 答辩演示", 11, GOLD, bold=True, spacing=120)

    badge = add_rect(slide, 11.55, 0.46, 1.1, 0.3, None, GOLD, 0.9, radius=0.3, name="DraftBadge")
    tf_b = badge.text_frame
    tf_b.word_wrap = False
    tf_b.margin_left = 0
    tf_b.margin_right = 0
    tf_b.margin_top = 0
    tf_b.margin_bottom = 0
    tf_b.vertical_anchor = MSO_ANCHOR.MIDDLE
    pb = tf_b.paragraphs[0]
    pb.alignment = PP_ALIGN.CENTER
    rb = pb.add_run()
    set_run(rb, "初稿 · 占位待填", 8.5, GOLD, bold=True, spacing=30)

    add_line(slide, 0.72, 1.55, 0.95, 0.035, GOLD)
    box, tf = add_text(slide, 0.68, 1.72, 6.9, 1.0, name="CoverTitle")
    p = tf.paragraphs[0]
    r = p.add_run()
    set_run(r, "湛知", 58, WHITE, bold=True, spacing=300)

    box, tf = add_text(slide, 0.72, 2.84, 6.9, 0.5, name="CoverSubtitle")
    p = tf.paragraphs[0]
    r = p.add_run()
    set_run(r, "有依据的师生智慧学伴", 24, DARK_MINT, bold=True, spacing=60)

    box, tf = add_text(slide, 0.72, 3.5, 7.0, 0.35, name="CoverClaim")
    p = tf.paragraphs[0]
    r = p.add_run()
    set_run(r, "从课程要求到学情证据，再到可审核的学习方案", 12.5, "A9C6B8")

    chips = [
        ("01", "理解学科", "课程要求 → 可诊断的知识点与关系"),
        ("02", "理解学生", "一次作答 → 带证据的学情"),
        ("03", "理解教学", "学情 → 可执行、可审核的方案"),
    ]
    y = 4.38
    for num, label, desc in chips:
        box, tf = add_text(slide, 0.72, y, 0.45, 0.32, name="ChipNum")
        p = tf.paragraphs[0]
        r = p.add_run()
        set_run(r, num, 10, GOLD, bold=True, font=FONT_MONO)
        box, tf = add_text(slide, 1.18, y - 0.03, 1.15, 0.36, name="ChipLabel")
        p = tf.paragraphs[0]
        r = p.add_run()
        set_run(r, label, 13.5, WHITE, bold=True)
        box, tf = add_text(slide, 2.45, y, 4.55, 0.36, name="ChipDesc")
        p = tf.paragraphs[0]
        r = p.add_run()
        set_run(r, desc, 10, "A9C6B8")
        y += 0.56

    # 右侧截图占位
    x, sy, w, h = 8.35, 1.5, 4.35, 4.95
    add_rect(slide, x, sy, w, h, "0F4634", "5C8A75", 1.1, radius=0.035, dash=True, name="CoverShotPlaceholder")
    pill = add_rect(slide, x + 0.24, sy + 0.22, 1.18, 0.3, "1B5C45", None, radius=0.25, name="CoverShotPill")
    tf = pill.text_frame
    tf.word_wrap = False
    tf.margin_left = 0
    tf.margin_right = 0
    tf.margin_top = 0
    tf.margin_bottom = 0
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    r = p.add_run()
    set_run(r, "封面底图占位", 8.5, DARK_MINT, bold=True)

    box, tf = add_text(slide, x + 0.28, sy + 0.78, 3.7, 0.6, name="CoverShotId")
    p = tf.paragraphs[0]
    r = p.add_run()
    set_run(r, "SH-01", 28, "7FA393", bold=True, font=FONT_MONO, spacing=20)
    box, tf = add_text(slide, x + 0.28, sy + 1.55, 3.7, 0.7, name="CoverShotDesc")
    p = tf.paragraphs[0]
    p.line_spacing = 1.25
    r = p.add_run()
    set_run(r, "身份页全景，压暗作底；待补最终截图", 10.5, DARK_MINT)
    box, tf = add_text(slide, x + 0.28, sy + 4.3, 3.7, 0.3, name="CoverShotTodo")
    p = tf.paragraphs[0]
    r = p.add_run()
    set_run(r, "TODO-14 · 1920×1080", 8.5, "7FA393", font=FONT_MONO)

    box, tf = add_text(slide, 0.72, 6.35, 7.3, 0.28, name="CoverMeta")
    p = tf.paragraphs[0]
    r = p.add_run()
    set_run(r, "赛题：智慧学伴——理解学科、理解学生、理解教学", 9, "9FBFB0")
    box, tf = add_text(slide, 0.72, 6.66, 7.3, 0.28, name="CoverMeta2")
    p = tf.paragraphs[0]
    r = p.add_run()
    set_run(r, "队伍 / 日期：待填　｜　TODO-02 学生与班级规模　｜　TODO-12 模型与超时", 9, "9FBFB0")
    box, tf = add_text(slide, 11.4, 7.06, 1.22, 0.22, name="PageNo", align=PP_ALIGN.RIGHT)
    p = tf.paragraphs[0]
    r = p.add_run()
    set_run(r, f"01 / {TOTAL}", 8.5, DARK_MINT, bold=True, font=FONT_MONO)

    slide.notes_slide.notes_text_frame.text = (
        "对应演示脚本 S0 片头与 S1 开场：先说三个问题，再给产品名与一句话定位。"
        "封面底图用 SH-01 身份页全景压暗，录制后替换，不要用原型图。"
    )


SLIDES = [
    dict(
        section="01 · 问题",
        title="三个问题，一条数据链",
        claim="三个问题，不做三套事实。",
        bullets=[
            "理解学科：课程要求 → 可诊断的知识点与关系",
            "理解学生：一次作答 → 带证据的学情，而不是一个分数",
            "理解教学：学情 → 可执行、可审核、可调整的方案",
            "我们的回答：三件事共用同一套正式版本，不各建一份事实",
            "数据链：导入 → A → B → C → 导出，同一版本、同一证据",
        ],
        shots="图示 · 数据链",
        shot_title="三列问题 → 一条横向数据链（导入 → A → B → C → 导出），下方标“同一版本、同一证据”。",
        shot_note="建议画成五段横向流程：导入、A、B、C、导出。",
        todos=[("TODO-02", "学生数 / 班级数"), ("TODO-13", "六类版本 ID")],
        notes="对应 S1 开场。先立三个问题，再说明三件事共用同一套正式版本；具体规模与版本号录制前填。",
        diagram=True,
    ),
    dict(
        section="02 · 产品",
        title="产品全景与边界",
        claim="先把边界说清楚，再谈能力。",
        bullets=[
            "形态：本地双端 Web，FastAPI + SQLite + React 单体",
            "教师端四个一级页面，学生端三个一级页面",
            "真实接口与演示数据严格隔离，页面失败不回退样例",
            "本地模拟身份不等于预设数据；本次演示走真实接口",
            "边界：不做账号体系、云部署与模型训练",
        ],
        shots="SH-01",
        shot_title="身份页全景 + 双端页面地图：教师四个入口，学生三个入口。",
        shot_note="页面地图可用右侧形状补画；身份页截图按当前数据重拍。",
        todos=[("TODO-01", "数据集名称与 ID"), ("TODO-14", "身份页截图")],
        notes="对应 S1。边界先讲，避免把本地模拟身份误解成演示样例数据。",
        bullet_size=14,
    ),
    dict(
        section="03 · 理解学科",
        title="理解学科：A 课程知识",
        claim="每个知识点都能回到教材与题目。",
        bullets=[
            "教材 PDF 文本层 → 范围计划 → 知识点候选 → 独立复核 → 合并",
            "关系有方向、有类型、有证据；前置与逻辑关系分开",
            "题目、评分点与知识点关联，保留出处与定位",
            "未通过复核的候选不进正式版本，留在教师审核队列",
            "诚实边界：关系推断受 180 秒预算截断，本轮 37 点 / 11 关系 / 58 标注",
        ],
        shots="SH-20 / SH-21",
        shot_title="课程体系关系图 + 定义与来源面板；知识审核队列。",
        shot_note="两张图上下叠放，或只放关系图，审核队列用文字说明。",
        todos=[("TODO-03", "知识点 / 关系 / 标注数"), ("TODO-07", "材料数 / 难度 / 来源")],
        notes="对应 P4。先讲构建路径，再讲证据与审核；关系数受预算截断要主动说明。",
        bullet_size=14,
    ),
    dict(
        section="04 · 理解学生",
        title="理解学生：班级总览",
        claim="分母是名册，缺失不画 0。",
        bullets=[
            "三项数字：名册人数、有效观测、需关注；分母是名册",
            "雷达只用同指标、同口径、同范围；缺失不画 0",
            "共性问题、表现分布、关注学生均来自 B 正式版本",
            "班级比较的可比性与差值由服务端判定",
            "诚实边界：需关注人数与关注列表必须一致",
        ],
        shots="SH-02 / SH-03",
        shot_title="班级总览 DC01 · 第 1 次观察；班级比较 DC01 vs DC02。",
        shot_note="雷达带 0—100 刻度；缺失指标不画 0 点。",
        todos=[("TODO-02", "DC01 / DC02 人数"), ("TODO-10", "三项数字与分子分母")],
        notes="对应 S2 前半段。三项数字的分母、班级比较的可比性，是这一段的两句硬话。",
    ),
    dict(
        section="05 · 证据",
        title="理解学生：个人画像与证据",
        claim="结论和证据分开摆。",
        bullets=[
            "个人雷达 + 诊断发现 + 策略信号；证据状态与表现分开",
            "每条发现可打开证据：原题、实际作答、评分点、数据质量、限制",
            "作答记录分页读取，不把有限样本冒充全部记录",
            "诚实边界：364 条 findings 中仅 11 条带模型错因解释",
        ],
        shots="SH-04 / SH-05",
        shot_title="学生画像 D01 概览；证据抽屉（记录类），展开原题、作答与评分点。",
        shot_note="若视频公开发布，按赛方数据范围决定是否遮姓名与手写原文。",
        todos=[("TODO-04", "学情份数 / findings / 解释数"), ("TODO-10", "D01 指标分子分母")],
        notes="对应 S2 后半段与 S3 结束后的核对。强调证据抽屉里的版本定位，不展示整份 JSON。",
        bullet_size=14,
    ),
    dict(
        section="06 · Agent",
        title="Agent：把提问接到正式数据",
        claim="回答里的每个数字都要能对上。",
        bullets=[
            "任务识别 → 受控工具 → 权限与范围校验 → 回答与证据",
            "流式输出、真实工具进度、历史会话恢复",
            "事实绑定：数字来自本轮工具结果，引用编号必须合法",
            "对不上的句子删掉，文末转写限制",
            "写操作只生成候选，必须经确认；教师改方案仍走 C 的完整审核",
        ],
        shots="SH-06 / SH-07",
        shot_title="AI 对话流式输出 + 工具时间线；回答完成后的工具进度展开。",
        shot_note="工具进度是服务端执行事件，不剪成“模型思考过程”。",
        todos=[("TODO-12", "模型提供方 / 模型 id / 超时"), ("TODO-11", "待审核候选与提醒数")],
        notes="对应 S3。流式、工具进度、事实绑定三件事按顺序讲；模型超时用规则摘要兜底也要说明。",
    ),
    dict(
        section="07 · 理解教学",
        title="理解教学：方案与三条审核路径",
        claim="生成、审核、发布是三件事。",
        bullets=[
            "六类策略 → 任务组 → 活动与材料 → 审核 → 发布",
            "三条路径：系统自动审核、教师直编预览确认、Agent 候选",
            "硬门：材料存在、答案保护、已完成活动不可移除、版本基线",
            "教师直编：检查草稿 → 确认发布（human_confirmed）",
            "诚实边界：154 组任务组的目标与预期结果仍同文",
        ],
        shots="SH-08 / SH-09 / SH-10",
        shot_title="任务组页；检查通过、等待确认；方案已发布（human_confirmed）。",
        shot_note="三张图可按“编辑 → 检查 → 发布”横向排列，或只放后两张。",
        todos=[("TODO-05", "方案数 / 任务组数 / 同文数"), ("TODO-11", "待审核候选数")],
        notes="对应 S4。先讲三条路径，再走教师直编的两步；发布结果要能看到 human_confirmed。",
        bullet_size=14,
    ),
    dict(
        section="08 · 学生端",
        title="学生端：从计划到微课堂",
        claim="学生只看本人数据。",
        bullets=[
            "个人画像只读本人数据，无班级排名",
            "学习计划按任务组顺序，不按题型重排；依赖未满足则锁定",
            "微课堂固定组件：文字、公式、推导、对照、数轴、引用",
            "安全渲染：不执行脚本、不加载外链、未知组件安全跳过",
            "诚实边界：本轮微课堂全部由规则模板生成，教材原文引用未登记",
        ],
        shots="SH-11 / SH-12 / SH-13",
        shot_title="学生个人画像；学习计划；微课堂（组件齐全）。",
        shot_note="微课堂顶部保留“只读 · 完成只记录阅读事件”的说明。",
        todos=[("TODO-06", "微课堂份数与生成方式"), ("TODO-07", "材料数 / 难度 / 来源")],
        notes="对应 S5。强调只读、固定组件、安全渲染；规则模板生成要主动说明，不说成模型现场生成。",
        bullet_size=14,
    ),
    dict(
        section="09 · 闭环",
        title="学习闭环：作答、提示、评分、回流",
        claim="一个答案只重算该重算的部分。",
        bullets=[
            "选择 / 填空 / 解答三题型；解答题保留过程与最终答案",
            "三级提示逐级释放；获取帮助会把本次作答记为受助",
            "评分以服务端为准；待评分不显示 0 分",
            "任务组完成触发局部 B → C → 审核发布，其他学生不受影响",
            "每一步都落学习事件账本，前端不自行判定完成",
        ],
        shots="SH-14 / SH-15",
        shot_title="在线作答 + 分级提示；微课堂已完成。",
        shot_note="闭环时序可补画：作答 → 事件 → B → C → 新方案。",
        todos=[("TODO-04", "学情规模与回流范围"), ("TODO-10", "D01 指标分子分母")],
        notes="对应 S5 与 S4 的交界。重点讲事件账本、待评分不给 0 分、局部重算。",
    ),
    dict(
        section="10 · 数据与版本",
        title="数据与版本：为什么每个结论都能核",
        claim="版本号只是名字，依赖边才是内容。",
        bullets=[
            "不可变版本 + 正式发布指针 + 发布 CAS",
            "依赖边：知识 → 目录、学情 → 知识、方案 → 学情、微课堂 → 知识",
            "证据索引统一解析；历史版本可回看，不覆盖",
            "导出冻结闭包，不混入后续改动",
            "版本是依赖边加条件更新，版本号只是对外名字",
        ],
        shots="图示 · 版本链",
        shot_title="六类版本与依赖边示意；一次发布的版本链：冻结 → 校验 → 切换正式指针。",
        shot_note="建议画成从左到右的版本链，标出 CAS 切换点。",
        todos=[("TODO-13", "catalog / knowledge / student / class / plans / content 版本 ID"), ("TODO-09", "导出文件数与状态")],
        notes="对应 P11。用一次发布的版本链讲清 CAS 与依赖边；版本号本身不承担语义。",
        bullet_size=14,
        diagram=True,
    ),
    dict(
        section="11 · 真实运行",
        title="真实数据运行：22 分钟与质量门",
        claim="失败也是验证结果。",
        bullets=[
            "干净库导入赛方六表 + 教材切片，四步任务全部成功",
            "40 学生、2 班、37 知识点、11 关系、58 标注",
            "80 份学情、78 个发布方案、154 个任务组",
            "质量门真实拦下 D14 两个方案：答案保护与证据不符",
            "这说明审核不是装饰：不合格就不发布",
        ],
        shots="SH-16",
        shot_title="任务链时间线（load / knowledge / analysis / plans 四段耗时）；质量报告页。",
        shot_note="预检缺项表可放在时间线下方，标出 PLAN_MISSING / D14。",
        todos=[("TODO-08", "导入总耗时与四段耗时"), ("TODO-02", "学生 / 班级 / 知识点 / 学情计数"), ("TODO-09", "本轮导出状态")],
        notes="对应 P12。先给总数，再讲质量门拦下 D14；不把失败藏起来，也不把模拟数据说成教学成效。",
        bullet_size=13.5,
    ),
    dict(
        section="12 · 成果导出",
        title="成果导出：冻结、可核对",
        claim="缺项就阻断，不打不完整的包。",
        bullets=[
            "预检先查覆盖与缺项，缺项不给生成按钮",
            "导出只打包正式版本闭包，不调用模型、不重算业务",
            "包内：README、manifest、课程知识、学情、方案、微课堂、报告",
            "manifest 两遍回读，通过才写 payload=passed；新包待复核",
            "诚实边界：本轮新 ZIP 尚未生成，导出层字段未验证",
        ],
        shots="SH-17 / SH-18 / SH-19",
        shot_title="导出预检通过；导出记录“校验通过”；ZIP 目录 + manifest.json。",
        shot_note="若预检仍被 D14 阻断，改用“质量门演示”，保留缺项表作证据。",
        todos=[("TODO-09", "文件数 / 大小 / 导出 ID"), ("TODO-15", "预检 coverage 与 warnings")],
        notes="对应 S6。预检未过就讲阻断，不放“生成”按钮；旧包数据不能冒充本轮新包。",
        bullet_size=13.5,
    ),
    dict(
        section="13 · 边界",
        title="边界、限制与下一步",
        claim="做不到的地方，页面上写着限制。",
        bullets=[
            "已验证：导入、A/B/C 正式版本、审核发布、学生作答闭环、导出预检与冻结逻辑",
            "部分完成：诊断解释覆盖、材料来源定位、目标与预期结果区分、教材原文引用",
            "未验证：新 ZIP 的 manifest 与 README 逐项复核、真实模型下的完整验收",
            "下一步：D14 重新发布后重跑导出与第三方复核；补教材原文来源；提高解释覆盖率",
            "原则：不把模拟结果说成教学成效，不把未跑过的测试写成通过",
        ],
        shots="图示 · 状态表",
        shot_title="三列状态表：已验证 / 部分完成 / 未验证；底部一句“页面上写着限制”。",
        shot_note="建议用三列色块：已验证 / 部分完成 / 未验证。",
        todos=[("TODO-09", "新 ZIP 生成状态"), ("TODO-14", "截图集与文件名")],
        notes="对应 S7。逐项说已验、部分、未验证；结尾回到“结论有版本、判断有证据、发布有审核”。",
        bullet_size=13,
        diagram=True,
    ),
]


def build() -> None:
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    prs.core_properties.title = "湛知——有依据的师生智慧学伴（答辩 PPT 初稿）"
    prs.core_properties.subject = "智慧学伴赛题答辩演示，14 页提纲初稿"
    prs.core_properties.author = ""
    prs.core_properties.comments = "按 docs/交付/演示脚本与PPT提纲.md 生成；截图与数字为占位，待最终材料替换。"

    add_cover(prs)
    for index, data in enumerate(SLIDES, start=2):
        add_content_slide(prs, index, **data)

    if OUT.exists():
        OUT.unlink()
    prs.save(OUT)
    print(f"OUTPUT={OUT}")
    print(f"slides={len(prs.slides)}")


def validate() -> None:
    prs = Presentation(OUT)
    assert len(prs.slides) == TOTAL, len(prs.slides)
    for i, slide in enumerate(prs.slides, start=1):
        assert slide.has_notes_slide, (i, "no notes")
        assert len(slide.notes_slide.notes_text_frame.text.strip()) > 10, (i, "empty notes")
        assert not [s for s in slide.shapes if s.shape_type == MSO_SHAPE_TYPE.PICTURE], (i, "picture present")
        by_name = {s.name: s for s in slide.shapes}
        todo_text = " ".join(s.text_frame.text for s in slide.shapes if s.has_text_frame)
        codes = sorted(set(re.findall(r"TODO-\d{2}", todo_text)))
        if i == 1:
            chips = [s for s in slide.shapes if s.name == "ChipLabel"]
            assert "CoverTitle" in by_name and "CoverShotPlaceholder" in by_name, (i, sorted(by_name))
            assert len(chips) == 3, len(chips)
            assert codes, (i, "no TODO")
            print(f"P{i:02d} cover_chips={len(chips)} codes={','.join(codes)} title={by_name['CoverTitle'].text_frame.text}")
            continue
        missing = [n for n in ("Title", "Bullets", "ScreenshotPlaceholder", "TodoCard") if n not in by_name]
        bullets = by_name["Bullets"].text_frame.paragraphs
        title = by_name["Title"].text_frame.text
        assert not missing, (i, missing)
        assert 3 <= len(bullets) <= 5, (i, len(bullets))
        assert codes, (i, "no TODO")
        print(f"P{i:02d} bullets={len(bullets)} codes={','.join(codes)} title={title}")
        # 边界检查（装饰图形允许出血）
        for shape in slide.shapes:
            if shape.name == "Decor":
                continue
            right = shape.left + shape.width
            bottom = shape.top + shape.height
            assert shape.left >= -1000 and shape.top >= -1000, (i, shape.name)
            assert right <= prs.slide_width + 1000 and bottom <= prs.slide_height + 1000, (i, shape.name, right, bottom)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    build()
    validate()
