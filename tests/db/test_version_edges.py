"""正式成果依赖边（06 §3.1、§4.3）：DAG、知识边、班级成员边与缺依赖限制。

这些用例直接对仓储与 ``persist_analysis_bundle`` 断言，覆盖：
* 自环 / 重复边 / 成环边被拒绝；
* 知识版本的目录边幂等补齐；
* 学情版本登记 ``knowledge`` 边、班级版本登记 ``member_analysis`` 边；
* 没有知识依赖时写明确限制，引用了不存在的知识版本时失败。
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from study_companion.contracts import (
    AnalysisBundle,
    AnalysisConfig,
    Catalog,
    ClassResult,
    DomainError,
    Metric,
    MetricScope,
    StudentResult,
)
from study_companion.db import models, version_repository as repo
from study_companion.pipeline import persist_analysis_bundle


@dataclass(frozen=True)
class Graph:
    family_id: str = "fam-edges"
    dataset_id: str = "ds-edges"
    run_id: str = "run-edges"


@pytest.fixture
def graph(session) -> Graph:
    """最小正式作用域：家族、数据集、目录行与分析运行。"""

    session.add(
        models.DatasetFamily(
            family_id="fam-edges",
            label="依赖边测试",
            data_origin="official",
            created_at=repo.now_iso(),
        )
    )
    session.flush()
    session.add(
        models.Dataset(
            id="ds-edges",
            created_at=repo.now_iso(),
            status="ready",
            manifest_json={},
            family_id="fam-edges",
            state="ready",
        )
    )
    session.flush()
    session.add(
        models.Catalog(
            version="catalog-x",
            dataset_id="ds-edges",
            content_hash="0" * 64,
            created_at=repo.now_iso(),
            payload_json={},
        )
    )
    session.flush()
    # AnalysisRun 由 persist_analysis_bundle 自己创建，测试不再预置，避免掩盖
    # “运行未记录知识版本”的问题。
    return Graph()


def _version(session, graph: Graph, kind: str, target: str, **kwargs):
    payload = kwargs.pop("payload", {"marker": target})
    return repo.create_version(
        session,
        kind=kind,
        key=repo.FormalKey(
            family_id=graph.family_id,
            dataset_id=graph.dataset_id,
            kind=kind,
            target_key=target,
        ),
        payload=payload,
        run_id=graph.run_id if kind in {"student_analysis", "class_analysis"} else None,
        **kwargs,
    )


def _published_catalog(session, graph: Graph):
    version = _version(session, graph, "catalog", "", payload={"catalog_version": "catalog-x"})
    repo.publish(
        session,
        key=repo.catalog_key(graph.family_id, graph.dataset_id),
        version=version,
        path="system_auto",
        basis="automatic_review",
    )
    return version


def _published_knowledge(session, graph: Graph, catalog):
    version = _version(session, graph, "knowledge", "kn-test", payload={"points": []})
    repo.register_catalog_edge(session, version, catalog_version=catalog)
    repo.publish(
        session,
        key=repo.knowledge_key(graph.family_id, graph.dataset_id, "catalog-x"),
        version=version,
        path="system_auto",
        basis="automatic_review",
    )
    return version


def _add_student(session, graph: Graph, student_id: str, class_id: str) -> str:
    student_uid = f"su-{student_id}"
    session.add(
        models.StudentIdentity(
            student_uid=student_uid,
            family_id=graph.family_id,
            external_id=student_id,
            created_at=repo.now_iso(),
        )
    )
    session.flush()
    session.add(
        models.Student(
            dataset_id=graph.dataset_id,
            student_id=student_id,
            class_id=class_id,
            student_uid=student_uid,
        )
    )
    session.flush()
    return student_uid


def _student_result(graph: Graph, student_id: str, class_id: str, *, observed: bool) -> StudentResult:
    metrics = []
    if observed:
        metrics.append(
            Metric(
                metric_id=f"metric-{student_id}",
                name="valid_score_ratio",
                value=1.0,
                numerator=1,
                denominator=1,
                unit="ratio",
                scope=MetricScope(
                    student_id=student_id, node_id="N01", indicator_id="I1"
                ),
                evidence_refs=[],
            )
        )
    return StudentResult(
        run_id=graph.run_id,
        dataset_id=graph.dataset_id,
        catalog_version="catalog-x",
        student_id=student_id,
        class_id=class_id,
        node_id="N01",
        metrics=metrics,
        findings=[],
        limitations=[],
    )


def _bundle(
    graph: Graph,
    students: list[StudentResult],
    classes: list[ClassResult],
    *,
    knowledge_version: str | None,
) -> AnalysisBundle:
    return AnalysisBundle(
        run_id=graph.run_id,
        dataset_id=graph.dataset_id,
        catalog_version="catalog-x",
        rule_version="b-rules-1",
        config=AnalysisConfig(),
        student_results=students,
        class_results=classes,
        quality_summary={},
        knowledge_version=knowledge_version,
    )


def _class_result(graph: Graph, class_id: str, roster: int, observed: int) -> ClassResult:
    return ClassResult(
        run_id=graph.run_id,
        dataset_id=graph.dataset_id,
        catalog_version="catalog-x",
        class_id=class_id,
        node_id="N01",
        roster_count=roster,
        observed_student_count=observed,
        metrics=[],
        findings=[],
        limitations=[],
    )


def _roles(session, version) -> set[str]:
    return {edge.role for edge in repo.dependencies(session, version)}


def test_register_edge_rejects_self_duplicate_and_cycle(session, graph: Graph) -> None:
    first = _version(session, graph, "lesson", "lesson-a")
    second = _version(session, graph, "lesson", "lesson-b")

    with pytest.raises(DomainError) as self_loop:
        repo.register_edge(session, first, first, role="content")
    assert self_loop.value.code == "UNPROCESSABLE"

    repo.register_edge(session, first, second, role="content")
    repo.register_edge(session, first, second, role="content")
    assert len(repo.dependencies(session, first)) == 1, "重复边必须幂等"

    with pytest.raises(DomainError) as cycle:
        repo.register_edge(session, second, first, role="content")
    assert cycle.value.code == "UNPROCESSABLE"


def test_knowledge_version_gets_catalog_edge_automatically(session, graph: Graph) -> None:
    """重建/导入都经 ``create_version``，知识版本必须自动带上目录边。"""

    catalog = _published_catalog(session, graph)
    knowledge = _version(session, graph, "knowledge", "kn-fill", payload={"points": []})

    edges = repo.dependencies(session, knowledge)
    assert [edge.role for edge in edges] == ["catalog"]
    assert edges[0].dep_version_id == catalog.version_id
    # 重复补齐不新增边。
    assert repo.register_catalog_edge(session, knowledge) is catalog
    assert len(repo.dependencies(session, knowledge)) == 1


def test_persist_analysis_registers_knowledge_and_member_edges(session, graph: Graph) -> None:
    catalog = _published_catalog(session, graph)
    knowledge = _published_knowledge(session, graph, catalog)
    _add_student(session, graph, "S01", "DC01")
    _add_student(session, graph, "S02", "DC01")
    students = [
        _student_result(graph, "S01", "DC01", observed=True),
        _student_result(graph, "S02", "DC01", observed=False),
    ]
    classes = [_class_result(graph, "DC01", roster=2, observed=1)]
    result = persist_analysis_bundle(
        session,
        _bundle(graph, students, classes, knowledge_version=knowledge.version_id),
        family_id=graph.family_id,
        catalog=Catalog(
            version="catalog-x", topics=[], indicators=[], materials=[], links=[]
        ),
        job_id=None,
        event_cutoff=0,
        included_event_ids=[],
    )
    # 返回值含 2 个学生版本 + 1 个班级版本。
    assert len(result["analysis_versions"]) == 3

    student_versions = [
        session.get(models.Version, students[0].analysis_version),
        session.get(models.Version, students[1].analysis_version),
    ]
    for version in student_versions:
        assert _roles(session, version) == {"catalog", "knowledge"}
        assert version.payload_json["analysis_input"]["knowledge_version"] == knowledge.version_id

    run = session.get(models.AnalysisRun, graph.run_id)
    assert run.knowledge_version == knowledge.version_id

    class_version = session.get(models.Version, classes[0].analysis_version)
    assert _roles(session, class_version) == {"catalog", "member_analysis"}
    member_edges = [edge for edge in repo.dependencies(session, class_version) if edge.role == "member_analysis"]
    assert {edge.dep_version_id for edge in member_edges} == {
        students[0].analysis_version,
        students[1].analysis_version,
    }
    members = {
        row.student_uid: row
        for row in session.query(models.ClassMember)
        .filter(models.ClassMember.class_version_id == class_version.version_id)
        .all()
    }
    assert set(members) == {"su-S01", "su-S02"}
    assert members["su-S01"].member_state == "available"
    assert members["su-S02"].member_state == "no_observation"
    member_payload = class_version.payload_json["member_analysis_versions"]
    assert {item["analysis_version"] for item in member_payload} == {
        students[0].analysis_version,
        students[1].analysis_version,
    }


def test_class_member_edges_fall_back_to_formal_head(session, graph: Graph) -> None:
    """局部更新只重发受影响学生时，班级版本仍指向其他成员当前正式学情。"""

    catalog = _published_catalog(session, graph)
    knowledge = _published_knowledge(session, graph, catalog)
    _add_student(session, graph, "S01", "DC01")
    _add_student(session, graph, "S02", "DC01")
    catalog_contract = Catalog(
        version="catalog-x", topics=[], indicators=[], materials=[], links=[]
    )
    first_students = [
        _student_result(graph, "S01", "DC01", observed=True),
        _student_result(graph, "S02", "DC01", observed=True),
    ]
    persist_analysis_bundle(
        session,
        _bundle(graph, first_students, [], knowledge_version=knowledge.version_id),
        family_id=graph.family_id,
        catalog=catalog_contract,
        job_id=None,
        event_cutoff=0,
        included_event_ids=[],
    )
    unaffected_version = first_students[1].analysis_version

    # 局部更新是新的分析运行（新 run_id），只重发受影响学生。
    second = Graph(run_id="run-edges-2")
    updated_student = _student_result(second, "S01", "DC01", observed=True)
    updated_class = _class_result(second, "DC01", roster=2, observed=2)
    persist_analysis_bundle(
        session,
        _bundle(second, [updated_student], [updated_class], knowledge_version=knowledge.version_id),
        family_id=second.family_id,
        catalog=catalog_contract,
        job_id=None,
        event_cutoff=0,
        included_event_ids=[],
    )

    class_version = session.get(models.Version, updated_class.analysis_version)
    member_edges = {
        edge.dep_version_id
        for edge in repo.dependencies(session, class_version)
        if edge.role == "member_analysis"
    }
    assert member_edges == {updated_student.analysis_version, unaffected_version}
    member_rows = {
        row.student_uid: row.student_version_id
        for row in session.query(models.ClassMember)
        .filter(models.ClassMember.class_version_id == class_version.version_id)
        .all()
    }
    assert member_rows == {
        "su-S01": updated_student.analysis_version,
        "su-S02": unaffected_version,
    }


def test_persist_analysis_without_knowledge_records_limitation(session, graph: Graph) -> None:
    _published_catalog(session, graph)
    _add_student(session, graph, "S01", "DC01")
    students = [_student_result(graph, "S01", "DC01", observed=True)]
    reason = "知识体系未通过自动校验，未发布为正式知识版本"
    persist_analysis_bundle(
        session,
        _bundle(graph, students, [], knowledge_version=None),
        family_id=graph.family_id,
        catalog=Catalog(
            version="catalog-x", topics=[], indicators=[], materials=[], links=[]
        ),
        job_id=None,
        event_cutoff=0,
        included_event_ids=[],
        knowledge_unavailable_reason=reason,
    )
    version = session.get(models.Version, students[0].analysis_version)
    assert _roles(session, version) == {"catalog"}
    limitations = version.payload_json["limitations"]
    assert any("未登记知识版本依赖" in item and reason in item for item in limitations)
    assert version.payload_json["analysis_input"]["knowledge_version"] is None


def test_persist_analysis_rejects_unknown_knowledge_version(session, graph: Graph) -> None:
    _add_student(session, graph, "S01", "DC01")
    students = [_student_result(graph, "S01", "DC01", observed=True)]
    with pytest.raises(DomainError) as missing:
        persist_analysis_bundle(
            session,
            _bundle(graph, students, [], knowledge_version="kn-does-not-exist"),
            family_id=graph.family_id,
            catalog=Catalog(
                version="catalog-x", topics=[], indicators=[], materials=[], links=[]
            ),
            job_id=None,
            event_cutoff=0,
            included_event_ids=[],
        )
    assert missing.value.code == "RESULT_NOT_READY"
