from __future__ import annotations

import json

from fastapi.testclient import TestClient
from sqlalchemy import select

from study_companion.agent.types import ModelTurn, ToolCall
from study_companion.contracts import RecommendationBundle
from study_companion.db import models
from study_companion.db import version_repository as repo


DATASET = "synthetic-ds"
CATALOG = "synthetic-v1"
RUN = "synthetic-run"
NODE = "N01"
PLAN = "plan-v1"


def _teacher_token(client: TestClient, dataset_id: str) -> str:
    response = client.post(
        "/api/v1/demo-sessions",
        json={"role": "teacher", "dataset_id": dataset_id},
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]["session_token"]


class TwoStepProvider:
    """按系统提示中的任务段返回文本；不调用任何真实模型。"""

    name = "fake_two_step"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def generate(self, messages, tools=None, response_schema=None) -> ModelTurn:
        system = messages[0]["content"]
        if "task-plan-revision-v1" in system:
            self.calls.append("plan_revision")
            assert any(
                tool["function"]["name"] == "prepare_plan_revision" for tool in tools or []
            )
            return ModelTurn(text="已按前序学情整理修订意图；正式发布仍需教师确认。")
        assert "task-student-progress-v1" in system
        self.calls.append("student_progress")
        return ModelTurn(text="S01 在本节点表现为有理数计算仍需练习。")


def test_chat_runs_teacher_composite_request_as_two_ordered_steps(
    client: TestClient, seeded: dict, monkeypatch
) -> None:
    provider = TwoStepProvider()
    monkeypatch.setattr("study_companion.api.routes.chat.build_provider", lambda _settings: provider)
    token = _teacher_token(client, seeded["dataset_id"])

    response = client.post(
        "/api/v1/chat",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "run_id": "synthetic-run",
            "student_id": "S01",
            "node_id": "N01",
            "message": "先看学生 S01 的学情，再按结果生成方案候选",
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert provider.calls == ["student_progress", "plan_revision"]
    assert data["task_type"] == "multi_step"
    assert data["route_source"] == "rule"
    assert data["route"]["step_count"] == 2
    assert [item["status"] for item in data["steps"]] == ["succeeded", "succeeded"]
    assert [item["step_key"] for item in data["steps"]] == ["s1", "s2"]
    assert data["steps"][1]["depends_on"] == ["s1"]
    assert data["stopped_reason"] == "model_finished"
    assert "有理数计算仍需练习" in data["text"]
    assert "正式发布仍需教师确认" in data["text"]
    assert "task-student-progress-v1" in data["prompt_version"]
    assert "task-plan-revision-v1" in data["prompt_version"]


def _frames(response) -> list[tuple[str, str]]:
    frames: list[tuple[str, str]] = []
    event = "message"
    data: list[str] = []
    for line in response.iter_lines():
        if line == "":
            if data:
                frames.append((event, "\n".join(data)))
            event, data = "message", []
            continue
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].strip())
    if data:
        frames.append((event, "\n".join(data)))
    return frames


def test_stream_exposes_per_step_statuses(
    client: TestClient, seeded: dict, monkeypatch
) -> None:
    provider = TwoStepProvider()
    monkeypatch.setattr("study_companion.api.routes.chat.build_provider", lambda _settings: provider)
    token = _teacher_token(client, seeded["dataset_id"])

    with client.stream(
        "POST",
        "/api/v1/chat/stream",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "run_id": RUN,
            "student_id": "S01",
            "node_id": NODE,
            "message": "先看学生 S01 的学情，再按结果生成方案候选",
        },
    ) as response:
        assert response.status_code == 200, response.read()
        frames = _frames(response)

    step_frames = [json.loads(data) for event, data in frames if event == "step"]
    by_key: dict[str, list[str]] = {}
    for item in step_frames:
        if item.get("kind") == "step":
            by_key.setdefault(item["step_key"], []).append(item["status"])
    assert by_key == {
        "s1": ["pending", "running", "succeeded"],
        "s2": ["pending", "running", "succeeded"],
    }

    done = json.loads([data for event, data in frames if event == "done"][0])
    assert done["status"] == "completed"
    assert [item["status"] for item in done["steps"]] == ["succeeded", "succeeded"]


def _ensure_family(session, dataset_id: str) -> str:
    dataset = session.get(models.Dataset, dataset_id)
    if dataset.family_id:
        return dataset.family_id
    family_id = "fam-multi-step"
    session.add(
        models.DatasetFamily(
            family_id=family_id,
            label="多步骤测试系列",
            data_origin="official",
            created_at=repo.now_iso(),
            active_dataset_id=None,
            generation=0,
        )
    )
    session.flush()
    dataset.family_id = family_id
    session.flush()
    return family_id


def _plan_bundle() -> RecommendationBundle:
    return RecommendationBundle.model_validate(
        {
            "run_id": RUN,
            "dataset_id": DATASET,
            "catalog_version": CATALOG,
            "student_id": "S01",
            "node_id": NODE,
            "analysis_version": "ban-1",
            "publication_status": "published",
            "recommendations": [
                {
                    "recommendation_id": "rec-1",
                    "indicator_id": "I01",
                    "finding_ids": ["F02"],
                    "goal": "稳定首次作答正确率",
                    "reason": "修订后能得满分，但首次作答仍有失分",
                    "student_intro": "今天先做两道小题，做完再检查一遍",
                    "group_id": "grp-1",
                    "review_status": "validated",
                    "generation_method": "rule",
                    "activities": [
                        {
                            "order": 1,
                            "activity_id": "act-1",
                            "group_id": "grp-1",
                            "material_id": "Q01",
                            "purpose": "巩固",
                            "observation_points": ["首次是否完整"],
                            "estimate_minutes": 4,
                        },
                        {
                            "order": 2,
                            "activity_id": "act-2",
                            "group_id": "grp-1",
                            "material_id": "Q02",
                            "purpose": "变式",
                            "observation_points": ["是否主动检查"],
                            "estimate_minutes": 4,
                        },
                    ],
                    "adjustment_conditions": [
                        {"observable_condition": "两道题都独立完成", "next_action": "进入迁移练习"}
                    ],
                }
            ],
            "validation_issues": [],
        }
    )


class CandidateProvider:
    """第二步真实调用 prepare_plan_revision，其余文本均为脚本。"""

    name = "fake_candidate"

    def __init__(self) -> None:
        self.plan_rounds = 0

    def generate(self, messages, tools=None, response_schema=None) -> ModelTurn:
        has_prepare = any(
            tool["function"]["name"] == "prepare_plan_revision" for tool in tools or []
        )
        if not has_prepare:
            return ModelTurn(text="S01 在本节点表现为有理数计算仍需练习。")
        self.plan_rounds += 1
        if self.plan_rounds == 1:
            return ModelTurn(
                tool_calls=[
                    ToolCall(
                        "candidate-1",
                        "prepare_plan_revision",
                        {
                            "student_id": "S01",
                            "intent": {
                                "goal": "把题量减到一道",
                                "reason": "学生一次能专注的时间有限",
                                "requested_changes": [
                                    {
                                        "kind": "reduce_questions",
                                        "group_id": "grp-1",
                                        "max_questions": 1,
                                    }
                                ],
                            },
                        },
                    )
                ]
            )
        return ModelTurn(text="候选已生成，等待教师确认后才能发布。")


def _publish_plan(session, seeded: dict) -> str:
    family_id = _ensure_family(session, seeded["dataset_id"])
    key = repo.FormalKey(
        family_id=family_id, dataset_id=DATASET, kind="plan", node_key=NODE, target_key="S01"
    )
    version = repo.create_version(
        session,
        kind="plan",
        key=key,
        payload=_plan_bundle().model_dump(mode="json"),
        rule_version="c-rules-v1",
        input_digest="plan-input",
        version_id=PLAN,
        run_id=RUN,
    )
    repo.publish(session, key=key, version=version, path="system_auto", basis="automatic_review")
    session.commit()
    return family_id


def test_teacher_propose_step_creates_candidate_without_publishing(
    client: TestClient, seeded: dict, session, monkeypatch
) -> None:
    family_id = _publish_plan(session, seeded)

    provider = CandidateProvider()
    monkeypatch.setattr("study_companion.api.routes.chat.build_provider", lambda _settings: provider)
    token = _teacher_token(client, seeded["dataset_id"])
    response = client.post(
        "/api/v1/chat",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "run_id": RUN,
            "student_id": "S01",
            "node_id": NODE,
            "message": "先看学生 S01 的学情，再按结果生成方案候选",
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert [item["status"] for item in data["steps"]] == ["succeeded", "succeeded"]
    assert data["status"] == "needs_confirmation"
    assert data["stopped_reason"] == "needs_confirmation"
    assert data["proposals"] and data["proposals"][0]["proposal_id"].startswith("prop-")
    assert "尚未发布" in data["text"]
    assert any(item["tool_name"] == "prepare_plan_revision" for item in data["tool_trace"])

    # 候选不移动正式指针：正式方案仍是 PLAN，也没有新增正式方案版本。
    session.expire_all()
    head = session.get(models.FormalHead, (family_id, DATASET, "plan", "", NODE, "S01"))
    assert head.version_id == PLAN
    assert len(session.scalars(select(models.Proposal)).all()) == 1


def test_single_task_propose_also_stops_at_candidate(
    client: TestClient, seeded: dict, session, monkeypatch
) -> None:
    family_id = _publish_plan(session, seeded)
    provider = CandidateProvider()
    monkeypatch.setattr("study_companion.api.routes.chat.build_provider", lambda _settings: provider)
    token = _teacher_token(client, seeded["dataset_id"])

    response = client.post(
        "/api/v1/chat",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "run_id": RUN,
            "student_id": "S01",
            "node_id": NODE,
            "task_type": "study_plan",
            "message": "帮 S01 调整一下学习方案",
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["task_type"] == "study_plan"
    assert data["route_source"] == "explicit"
    assert data["status"] == "needs_confirmation"
    assert data["stopped_reason"] == "needs_confirmation"
    assert data["proposals"] and data["proposals"][0]["proposal_id"].startswith("prop-")
    assert "尚未发布" in data["text"]
    assert any(segment["kind"] == "operation" for segment in data["segments"])

    session.expire_all()
    head = session.get(models.FormalHead, (family_id, DATASET, "plan", "", NODE, "S01"))
    assert head.version_id == PLAN
    assert len(session.scalars(select(models.Proposal)).all()) == 1
