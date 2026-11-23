"""Exercise upload, snapshot isolation and teacher-only roster access."""

TABLES = {
    "requirements": "主题编号,主题,指标编号,观察指标\nT1,主题,I1,指标\n",
    "materials": "材料编号,主题编号,材料类型,题干或内容,指标编号,满分\nQ1,T1,题目,题目内容,I1,2\n",
    "students": "学生编号,模拟班级编号,年级\nS1,C1,7\n",
    "nodes": "节点编号\nN1\n",
    "records": "记录编号,学生编号,材料编号,节点编号,评分状态,得分,满分\nR1,S1,Q1,N1,已评分,1,2\n",
    "dictionary": "字段名,含义\n学生编号,标识\n",
}


def files(tables=TABLES):
    return {
        name: (f"{name}.csv", value.encode("utf-8"), "text/csv")
        for name, value in tables.items()
    }


def token(client, role="teacher", dataset_id=None, student_id=None):
    response = client.post(
        "/api/v1/demo-sessions",
        json={"role": role, "dataset_id": dataset_id, "student_id": student_id},
    )
    assert response.status_code == 201
    return {"Authorization": "Bearer " + response.json()["data"]["session_token"]}


def test_upload_creates_independent_snapshots_and_protects_roster(client):
    teacher = token(client)
    first = client.post("/api/v1/datasets", headers=teacher, files=files())
    second = client.post("/api/v1/datasets", headers=teacher, files=files())
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    dataset = first.json()["data"]["dataset_id"]
    assert dataset != second.json()["data"]["dataset_id"]
    roster = client.get(f"/api/v1/datasets/{dataset}/students", headers=teacher)
    assert roster.json()["data"]["items"][0]["student_id"] == "S1"
    student = token(client, "student", dataset, "S1")
    assert (
        client.get(f"/api/v1/datasets/{dataset}/students", headers=student).status_code
        == 403
    )


def test_upload_invalid_table_does_not_create_dataset(client):
    teacher = token(client)
    bad = dict(TABLES, students="wrong\nvalue\n")
    response = client.post("/api/v1/datasets", headers=teacher, files=files(bad))
    assert response.status_code == 422
    assert client.get("/api/v1/datasets", headers=teacher).json()["data"]["total"] == 0
    missing = dict(TABLES)
    del missing["dictionary"]
    assert (
        client.post(
            "/api/v1/datasets", headers=teacher, files=files(missing)
        ).status_code
        == 422
    )
    assert client.get("/api/v1/datasets", headers=teacher).json()["data"]["total"] == 0


def test_upload_idempotency_reuses_snapshot_and_rejects_changed_files(client):
    teacher = {**token(client), "Idempotency-Key": "upload-retry"}
    first = client.post("/api/v1/datasets", headers=teacher, files=files())
    retry = client.post("/api/v1/datasets", headers=teacher, files=files())
    assert first.status_code == retry.status_code == 201
    assert first.json()["data"]["dataset_id"] == retry.json()["data"]["dataset_id"]
    different = dict(TABLES, students=TABLES["students"] + "S2,C1,7\n")
    assert (
        client.post(
            "/api/v1/datasets", headers=teacher, files=files(different)
        ).status_code
        == 409
    )


def test_analysis_run_is_scoped_and_uses_uploaded_results(client):
    teacher = token(client)
    uploaded = client.post("/api/v1/datasets", headers=teacher, files=files()).json()[
        "data"
    ]
    dataset = uploaded["dataset_id"]
    bound = token(client, "teacher", dataset)
    run_request = {
        "dataset_id": dataset,
        "catalog_version": uploaded["catalog_version"],
        "node_ids": ["N1"],
    }
    run_headers = {**bound, "Idempotency-Key": "run-retry"}
    response = client.post("/api/v1/runs", headers=run_headers, json=run_request)
    assert response.status_code == 202, response.text
    run = response.json()["data"]["run_id"]
    retry = client.post("/api/v1/runs", headers=run_headers, json=run_request)
    assert retry.json()["data"]["run_id"] == run
    changed = dict(run_request, rule_version="other")
    assert (
        client.post("/api/v1/runs", headers=run_headers, json=changed).status_code
        == 409
    )
    result = client.get(f"/api/v1/runs/{run}/students/S1?node_id=N1", headers=bound)
    assert result.status_code == 200, result.text
    assert result.json()["data"]["student_id"] == "S1"
    assert any(
        metric["numerator"] == 1 and metric["denominator"] == 2
        for metric in result.json()["data"]["metrics"]
    )
    student = token(client, "student", dataset, "S1")
    assert (
        client.get(
            f"/api/v1/runs/{run}/students/other?node_id=N1", headers=student
        ).status_code
        == 403
    )
    assert (
        client.get(
            f"/api/v1/runs/{run}/classes/C1?node_id=N1", headers=student
        ).status_code
        == 403
    )
    recommendation_headers = {**bound, "Idempotency-Key": "advice-retry"}
    created = client.post(
        f"/api/v1/runs/{run}/students/S1/recommendations",
        headers=recommendation_headers,
        json={"node_id": "N1", "mode": "rule"},
    )
    assert created.status_code == 202, created.text
    job = created.json()["data"]["job_id"]
    retry = client.post(
        f"/api/v1/runs/{run}/students/S1/recommendations",
        headers=recommendation_headers,
        json={"node_id": "N1", "mode": "rule"},
    )
    assert retry.json()["data"]["job_id"] == job
    assert (
        client.get(f"/api/v1/jobs/{job}", headers=bound).json()["data"]["status"]
        == "succeeded"
    )
    advice = client.get(
        f"/api/v1/runs/{run}/students/S1/recommendations?node_id=N1", headers=bound
    ).json()["data"]
    assert advice["recommendations"][0]["review_status"] == "needs_review"
    assert any(
        issue["code"] == "SOURCE_UNVERIFIED" for issue in advice["validation_issues"]
    )
    student_advice = client.get(
        f"/api/v1/runs/{run}/students/S1/recommendations?node_id=N1", headers=student
    ).json()["data"]
    assert student_advice["recommendations"] == []


def test_failed_analysis_is_recorded_without_results(client, monkeypatch):
    teacher = token(client)
    uploaded = client.post("/api/v1/datasets", headers=teacher, files=files()).json()[
        "data"
    ]
    bound = token(client, "teacher", uploaded["dataset_id"])

    def fail_analysis(*args, **kwargs):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(
        "study_companion.api.routes.runs.analyze_dataset", fail_analysis
    )
    response = client.post(
        "/api/v1/runs",
        headers=bound,
        json={
            "dataset_id": uploaded["dataset_id"],
            "catalog_version": uploaded["catalog_version"],
            "node_ids": ["N1"],
        },
    )
    assert response.status_code == 202
    data = response.json()["data"]
    assert data["status"] == "failed"
    details = client.get(f"/api/v1/runs/{data['run_id']}", headers=bound).json()["data"]
    assert details["status"] == "failed"
    assert details["error"]["code"] == "ANALYSIS_FAILED"
    assert (
        client.get(
            f"/api/v1/runs/{data['run_id']}/students/S1?node_id=N1", headers=bound
        ).status_code
        == 404
    )
