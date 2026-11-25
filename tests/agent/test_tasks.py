from __future__ import annotations

from study_companion.agent.tasks import TASKS, tasks_for_role


def test_task_registry_contains_all_sixteen_design_tasks() -> None:
    expected = {
        "student_loss_explanation", "evidence_lookup", "data_quality", "student_progress",
        "class_analysis", "class_data_quality", "student_learning_advice", "study_plan",
        "class_strategy", "knowledge_lookup", "prerequisite_query", "material_search",
        "concept_tutoring", "relation_review", "observation_review", "out_of_scope",
    }
    assert set(TASKS) == expected
    assert len(TASKS) == 16


def test_pending_tasks_do_not_advertise_unregistered_tools() -> None:
    registered_tools = {
        "get_student_analysis", "get_answer_evidence", "get_quality_issues", "get_class_analysis",
        "locate_knowledge", "get_knowledge_detail", "get_knowledge_relations", "list_knowledge_candidates",
    }
    for task in TASKS.values():
        assert set(task.tools) <= registered_tools
        if task.status == "pending_tools":
            assert task.unavailable_reason


def test_role_task_sets_exclude_teacher_only_tasks_for_students() -> None:
    student_types = {task.task_type for task in tasks_for_role("student")}
    assert "class_analysis" not in student_types
    assert "class_data_quality" not in student_types
    assert "class_strategy" not in student_types
    assert "relation_review" not in student_types
    assert "concept_tutoring" in student_types
    assert "out_of_scope" in student_types
