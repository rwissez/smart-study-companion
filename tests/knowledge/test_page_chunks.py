"""A 教材页窗口按课程术语排序（02 §6.1）。"""

from __future__ import annotations

from study_companion.knowledge.textbook_model import build_page_chunks


class Page:
    def __init__(self, number: int, text: str) -> None:
        self.page = number
        self.text = text
        self.quality = "usable"


class Document:
    def __init__(self, pages: list[Page]) -> None:
        self.pages = pages
        self.page_count = len(pages)


class Topic:
    def __init__(self, title: str) -> None:
        self.topic_id = "T1"
        self.title = title
        self.goal = ""


class Indicator:
    indicator_id = "I1"
    topic_id = "T1"
    description = "解一元一次不等式并在数轴上表示解集"


class Catalog:
    def __init__(self) -> None:
        self.topics = [Topic("不等式与不等式组")]
        self.indicators = [Indicator()]


def test_relevant_window_is_ranked_first() -> None:
    pages = [
        Page(1, "几何图形初步：线段、射线、直线"),
        Page(2, "角的度量与比较"),
        Page(3, "相交线与平行线"),
        Page(4, "三角形的内角和"),
        Page(5, "一元一次不等式：解不等式并在数轴上表示解集"),
        Page(6, "不等式的性质与解集"),
    ]
    chunks = build_page_chunks(Document(pages), Catalog(), limit=2)
    assert chunks[0].start_page == 5
    assert "不等式" in chunks[0].text
