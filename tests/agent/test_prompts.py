from __future__ import annotations

from study_companion.agent.prompts import PromptRegistry
from study_companion.agent.tasks import TASKS


def test_every_task_has_versioned_prompt_file() -> None:
    registry = PromptRegistry()
    for task_type, task in TASKS.items():
        text, version = registry.load(f"tasks/{task.prompt_file}")
        assert text.strip()
        assert version.startswith("task-")
        assert f"prompt_version: {version}" in text


def test_task_prompt_composes_with_role_and_router_versions() -> None:
    prompt, version = PromptRegistry().compose(
        role="teacher",
        task="班级分析",
        task_type="class_analysis",
        router_prompt_version="router-v1",
        context="class_id=C01",
    )
    assert "当前任务：班级分析" in prompt
    assert "task-class-analysis-v1" in prompt
    assert "class_id=C01" in prompt
    assert version == "router-v1+task-class-analysis-v1+teacher-style-v1"


def test_legacy_prompt_composition_remains_compatible() -> None:
    _prompt, version = PromptRegistry().compose(role="student", task="旧任务")
    assert version == "student-style-v1"
