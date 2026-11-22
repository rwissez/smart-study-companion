# A P0 端到端接线 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不修改 B 分析算法和 C 推荐算法的前提下，让教师能够上传六张 CSV、创建分析运行、查询结果与证据、生成并保存建议，并用真实目录完成一条可演示链路。

**Architecture:** API 只负责鉴权、输入输出和状态码；新增 `library` 层负责数据集、目录、运行与建议的持久化和重建；新增 `curriculum` 层从两张目录 CSV 与覆盖层构建 `Catalog`。运行仍同步执行，但 HTTP 保留 202 和任务形状。分析与推荐继续作为纯函数，只依赖 `contracts`。

**Tech Stack:** Python 3.12、FastAPI、Pydantic v2、SQLAlchemy 2、Alembic、SQLite、pytest、uv。

**Spec:** `docs/team/17_项目进展核查与下一步规划_20261122.md`，接口细节以 `docs/team/08_接口协议.md` 为准，通用约束以 `docs/team/11_统一实施规范.md` 为准。

## Global Constraints

- 不向仓库提交真实赛题 CSV、学生数据、PDF、密钥或本地数据库。
- `analysis/` 和 `recommendation/` 仍是纯函数层，A 只调用，不修改其内部算法。
- 已冻结契约只允许增加可选字段；P0 优先不改现有契约。
- 上传只接受 multipart 字段 `requirements`、`materials`、`students`、`nodes`、`records`、`dictionary`，不接受本地路径。
- 每次上传产生新的不可变 `dataset_id`；每次目录编辑产生新的 `catalog_version`。
- 运行同步完成，但 `POST /api/v1/runs` 返回 202，并在返回时具有最终 `succeeded` 或 `failed` 状态。
- 学生只能读取本人结果和建议；参考答案、评分点、教师修订理由不得泄露。
- 无证据、证据冲突或来源未核实时如实返回限制，不制造确定性结论。
- 本阶段不实现远程模型、工具循环、教师修订历史和可视化目录编辑；这些属于 A-L1 至 A-L5。

## Review Focus

- 六个 CSV 中任一缺失、空文件、编码错误或缺必需列时，必须回滚整个事务并返回 422。
- 同一输入重复上传必须产生两个独立数据集，不覆盖旧数据或旧运行。
- `dataset_id`、`run_id`、`catalog_version` 任一不匹配时必须拒绝，不能返回旧结果。
- 学生令牌访问他人结果、班级结果、质量问题或教师材料时必须返回 403 或不泄露存在性。
- 建议引用 `unverified` 材料时必须保存为 `needs_review`，且原因进入 `validation_issues`。

---

## File Map

- `src/study_companion/api/uploads.py`：把六个 `UploadFile` 解码为 `RawTables`，不做业务计算。
- `src/study_companion/api/routes/datasets.py`：上传、学生列表、质量问题接口。
- `src/study_companion/api/routes/runs.py`：创建运行、查询状态、学生/班级结果和证据。
- `src/study_companion/api/routes/recommendations.py`：规则建议生成与读取。
- `src/study_companion/library/datasets.py`：数据集落库与 `PreparedDataset` 重建。
- `src/study_companion/library/catalogs.py`：目录落库与 `Catalog` 重建。
- `src/study_companion/library/runs.py`：调用 `analyze_dataset` 并保存/读取运行结果。
- `src/study_companion/library/recommendations.py`：调用 C 固定签名、校验并保存建议。
- `src/study_companion/curriculum/builder.py`：从 requirements/materials CSV 构建基础 `Catalog`。
- `src/study_companion/curriculum/overlay.py`：将团队核对覆盖层应用为新目录版本。
- `migrations/versions/0002_recommendations.py`：建议结果表；修订历史另留给 A-L3。
- `tests/contracts/test_import_boundaries.py`：强制模块依赖方向。
- `tests/api/test_dataset_upload.py`、`test_runs.py`、`test_recommendations.py`：HTTP 验收。
- `tests/library/`、`tests/curriculum/`：仓储往返和目录构建测试。
- `tests/e2e/test_p0_vertical_slice.py`：上传到建议的端到端测试。

### Task 1: 依赖边界、仓库清理与上传基础

**Files:**
- Create: `tests/contracts/test_import_boundaries.py`
- Create: `src/study_companion/api/uploads.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `README.md`
- Remove: repository-root `NUL` if present locally; never add it to Git
- Test: `tests/api/test_upload_parser.py`

**Interfaces:**
- Consumes: six UTF-8/UTF-8-BOM CSV uploads.
- Produces: `async def raw_tables_from_uploads(...) -> RawTables`.

- [ ] **Step 1: Write the import-boundary tests**

Assert that files under `analysis/` and `recommendation/` do not import `db`、`api`、`agent`、`library`；`contracts` does not import any other `study_companion` package；`library`/`curriculum` do not import `analysis` or `recommendation` internals.

- [ ] **Step 2: Run the boundary test and verify the current tree passes**

Run: `uv run pytest tests/contracts/test_import_boundaries.py -q`

Expected: PASS before feature code is added; later tasks keep it green.

- [ ] **Step 3: Write failing CSV parser tests**

Cover UTF-8 BOM, preserved Chinese column names, generated `source_row_number`, missing multipart field, empty file, malformed CSV, and non-UTF-8 input. Missing/invalid uploads must raise `DomainError("UNPROCESSABLE", ...)`.

- [ ] **Step 4: Add multipart support and implement the parser**

Add `python-multipart` to runtime dependencies. Implement:

```python
async def raw_tables_from_uploads(
    requirements: UploadFile,
    materials: UploadFile,
    students: UploadFile,
    nodes: UploadFile,
    records: UploadFile,
    dictionary: UploadFile,
) -> RawTables: ...
```

Every row receives a one-based string `source_row_number`; decoded cells remain `str | None`.

- [ ] **Step 5: Verify and commit**

Run: `uv lock && uv sync --frozen --all-groups && uv run pytest tests/contracts tests/api/test_upload_parser.py -q`

Commit: `git commit -m "添加上传解析与模块边界检查"`

### Task 2: A-1 数据集上传、基础目录与事务落库

**Files:**
- Create: `src/study_companion/library/__init__.py`
- Create: `src/study_companion/library/datasets.py`
- Create: `src/study_companion/library/catalogs.py`
- Create: `src/study_companion/curriculum/__init__.py`
- Create: `src/study_companion/curriculum/builder.py`
- Create: `src/study_companion/api/routes/datasets.py`
- Modify: `src/study_companion/main.py`
- Modify: `src/study_companion/cli.py`（只把已有落库逻辑复用到 library，不保留第二套实现）
- Test: `tests/library/test_datasets.py`
- Test: `tests/curriculum/test_builder.py`
- Test: `tests/api/test_dataset_upload.py`

**Interfaces:**
- Consumes: `RawTables` and `ImportConfig`; B's `prepare_dataset(tables, config)`.
- Produces: `Catalog build_base_catalog(RawTables, dataset_id: str)`, `void save_dataset(Session, RawTables, PreparedDataset, Catalog)`, `PreparedDataset load_prepared_dataset(Session, dataset_id: str)`.

- [ ] **Step 1: Write failing base-catalog tests**

Assert that requirements rows become `Topic`/`Indicator`, materials rows become `Material`, unknown optional columns do not crash, all uploaded sources default to `unverified`, and the version is deterministic for the same `dataset_id` plus content.

- [ ] **Step 2: Implement `build_base_catalog`**

Use documented Chinese column aliases already accepted by B where possible. Do not invent textbook page verification; absent or uncertain locations remain `unverified`.

- [ ] **Step 3: Write failing repository round-trip tests**

Persist and reload all students, nodes, raw rows, selected events, quality issues and catalog objects. Assert `PreparedDataset.model_dump()` and `Catalog.model_dump()` round-trip without loss.

- [ ] **Step 4: Extract shared persistence from `seed_demo`**

Implement:

```python
def save_dataset(
    session: Session,
    raw_tables: RawTables,
    prepared: PreparedDataset,
    catalog: Catalog,
) -> None: ...

def load_prepared_dataset(session: Session, dataset_id: str) -> PreparedDataset: ...

def save_catalog(session: Session, dataset_id: str, catalog: Catalog) -> None: ...
def load_catalog(session: Session, version: str) -> Catalog: ...
```

`seed_demo` must call these functions instead of duplicating inserts.

- [ ] **Step 5: Write failing upload endpoint tests**

Test `POST /api/v1/datasets`: bootstrap teacher succeeds with 201; response contains `dataset_id`、`status`、`quality_summary`；missing required column returns 422；a forced failure leaves no partial dataset；same bytes uploaded twice create different IDs.

- [ ] **Step 6: Implement `POST /datasets`**

Call `raw_tables_from_uploads` → `prepare_dataset` → `build_base_catalog` → `save_dataset`. Use request-generated snapshot identity rather than B's deterministic fixture ID so repeated uploads remain separate snapshots. Register the router in `main.py`.

- [ ] **Step 7: Add read endpoints required by the UI**

Implement `GET /datasets/{dataset_id}/quality` and `GET /datasets/{dataset_id}/students` with pagination and `class_id` filtering, enforcing teacher/student scope.

- [ ] **Step 8: Verify and commit**

Run: `uv run pytest tests/library tests/curriculum tests/api/test_dataset_upload.py tests/api/test_demo_sessions.py -q`

Commit: `git commit -m "实现六表上传与数据集事务落库"`

### Task 3: A-2 分析运行编排与结果/证据接口

**Files:**
- Create: `src/study_companion/library/runs.py`
- Create: `src/study_companion/api/routes/runs.py`
- Modify: `src/study_companion/main.py`
- Test: `tests/library/test_runs.py`
- Test: `tests/api/test_runs.py`

**Interfaces:**
- Consumes: `load_prepared_dataset`、`load_catalog`、B's `analyze_dataset(data, catalog, context, config)`.
- Produces: `AnalysisBundle execute_analysis_run(...)` and HTTP endpoints from document 08.

- [ ] **Step 1: Write failing run-service tests**

Assert successful execution saves one `AnalysisRun` and all student/class results; exceptions set the run to `failed` with a safe error body; changing dataset or catalog version never returns an old result.

- [ ] **Step 2: Implement synchronous run orchestration**

Implement:

```python
def execute_analysis_run(
    session: Session,
    dataset_id: str,
    catalog_version: str,
    node_ids: list[str],
    rule_version: str,
    config: AnalysisConfig,
) -> AnalysisBundle: ...
```

Generate a new `run_id`, validate dataset/catalog ownership, call B once, filter persisted results to requested node IDs, and save explicit config/version fields.

- [ ] **Step 3: Write failing API tests**

Cover `POST /runs` (202), `GET /runs/{run_id}`, student result, class result, quality issues and evidence. Test record evidence resolves through `selected_source_row_id`; metric evidence resolves from the saved result. Test student self-only access.

- [ ] **Step 4: Implement run and evidence routes**

Implement the exact paths from `docs/team/08_接口协议.md`:

- `POST /api/v1/runs`
- `GET /api/v1/runs/{run_id}`
- `GET /api/v1/runs/{run_id}/students/{student_id}?node_id=...`
- `GET /api/v1/runs/{run_id}/classes/{class_id}?node_id=...`
- `GET /api/v1/runs/{run_id}/evidence/{kind}/{id}` for `record|metric`

Every run response includes `run_id`、`dataset_id`、`catalog_version`.

- [ ] **Step 5: Verify and commit**

Run: `uv run pytest tests/library/test_runs.py tests/api/test_runs.py tests/analysis -q`

Commit: `git commit -m "接入分析运行与证据查询接口"`

### Task 4: A-3 建议持久化与建议接口

**Files:**
- Create: `migrations/versions/0002_recommendations.py`
- Modify: `src/study_companion/db/models.py`
- Create: `src/study_companion/library/recommendations.py`
- Create: `src/study_companion/api/routes/recommendations.py`
- Modify: `src/study_companion/main.py`
- Test: `tests/db/test_recommendation_migration.py`
- Test: `tests/library/test_recommendations.py`
- Test: `tests/api/test_recommendations.py`

**Interfaces:**
- Consumes: saved `StudentResult`/`Catalog`/`RunContext`; C's `recommend_rules` and `validate_recommendations`.
- Produces: saved `RecommendationBundle`; teacher/student-safe read response.

- [ ] **Step 1: Write a failing migration test**

After `alembic upgrade head`, assert a `recommendations` table with `id`、`run_id`、`student_id`、`node_id`、`status`、`payload_json`、`validation_json`、`created_at` and a unique key per run/student/node. Do not create revision tables until A-L3.

- [ ] **Step 2: Implement migration and ORM model**

Use foreign key `run_id -> analysis_runs.id`. Store the validated contract payload rather than decomposing activities into premature tables.

- [ ] **Step 3: Write failing recommendation-service tests**

Assert generation uses the saved analysis without recomputing statistics; context mismatch is rejected; validation issues force affected recommendations to `needs_review`; repeated generation replaces only the same run/student/node record and does not mutate an older run.

- [ ] **Step 4: Implement recommendation orchestration**

Implement:

```python
def generate_rule_recommendations(
    session: Session,
    run_id: str,
    student_id: str,
    node_id: str,
    config: RecommendationConfig,
) -> RecommendationBundle: ...
```

Call `recommend_rules`, then `validate_recommendations`; merge issues into the saved bundle and downgrade invalid recommendations to `needs_review`.

- [ ] **Step 5: Add source-reliability regression tests at the A/C boundary**

Until C adds a first-class reliability rule, the orchestration layer must reject `validated` when every referenced material is `unverified`, append a `SOURCE_UNVERIFIED` issue, and preserve the material IDs. Mark this test as the compatibility gate C must keep when moving the rule into `validate_recommendations`.

- [ ] **Step 6: Implement recommendation endpoints**

- `POST /api/v1/runs/{run_id}/students/{student_id}/recommendations` with `node_id` and `mode="rule"`, returning 202 plus final status.
- `GET` on the same path returns the current bundle; student responses omit teacher-only audit data.
- `mode="model"` returns `MODEL_NOT_CONFIGURED` until A-L5.

- [ ] **Step 7: Verify and commit**

Run: `uv run alembic upgrade head && uv run pytest tests/db/test_recommendation_migration.py tests/library/test_recommendations.py tests/api/test_recommendations.py tests/recommendation -q`

Commit: `git commit -m "接入规则建议生成与持久化"`

### Task 5: A-4 真实目录覆盖层与核对产物

**Files:**
- Create: `src/study_companion/curriculum/overlay.py`
- Create: `tests/fixtures/catalog_overlay_synthetic.json`
- Create: `tests/curriculum/test_overlay.py`
- Create locally/ignored until data policy is confirmed: `data/catalogs/catalog-1/curriculum.json`
- Create locally/ignored until data policy is confirmed: `data/catalogs/catalog-1/materials_index.json`
- Create locally/ignored until data policy is confirmed: `data/catalogs/catalog-1/links.json`
- Create locally/ignored until data policy is confirmed: `data/catalogs/catalog-1/catalog_review.md`

**Interfaces:**
- Consumes: base `Catalog` and an overlay keyed by `indicator_id`/`material_id`.
- Produces: `Catalog apply_overlay(Catalog, Mapping[str, Any], new_version: str) -> Catalog`.

- [ ] **Step 1: Write failing overlay tests using synthetic IDs only**

Assert verified page locations replace unverified locations only in a new catalog version; unknown overlay IDs are reported but do not delete base entries; `team_inference` links remain explicitly labeled; original catalog is unchanged.

- [ ] **Step 2: Implement immutable overlay application**

Do not add review-person fields until the team decides `source_reviews` versus JSON. Keep the overlay file schema local to `curriculum`, not `contracts`.

- [ ] **Step 3: Generate B's textbook draft and perform A's manual review**

Run `study-companion split-textbook` on the approved local PDF inputs. Manually verify B02/B05/B06 page mappings and CR01 pages 62–63/55–56. Record reviewer, date and conclusion in `catalog_review.md`; never mark automatically generated rows `verified` without visual confirmation.

- [ ] **Step 4: Build and validate the real catalog locally**

Load the approved six tables, apply the reviewed overlay, validate with `Catalog.model_validate`, then run B analysis and C recommendations for the three reviewed representative students. Keep raw competition data ignored.

- [ ] **Step 5: Decide what can be committed**

If the captain confirms that IDs/relations/page numbers/short labels are allowed in the private repository, commit only those overlay artifacts. Otherwise leave them under ignored `data/catalogs/` and document the local provisioning step in README.

- [ ] **Step 6: Verify and commit code/tests only**

Run: `uv run pytest tests/curriculum tests/analysis tests/recommendation -q`

Commit: `git commit -m "实现目录覆盖层与核对流程"`

### Task 6: A-5 端到端验收、契约更新与文档

**Files:**
- Create: `tests/e2e/test_p0_vertical_slice.py`
- Modify: `README.md`
- Regenerate: `contracts/openapi.json`
- Regenerate: `contracts/schemas/*.json`
- Regenerate: `contracts/examples/synthetic_contracts.json`
- Optional after P0: create a separate A-L4 export plan; do not fold ZIP export into this gate.

**Interfaces:**
- Consumes: Tasks 1–5.
- Produces: a reproducible P0 demo path and frozen HTTP contract for D.

- [ ] **Step 1: Write the complete vertical-slice test**

The test must: create bootstrap teacher → upload six synthetic CSVs → create bound teacher → create run → query one student result → resolve one evidence reference → generate recommendations → query the saved bundle. Assert IDs and versions remain identical across every response.

- [ ] **Step 2: Add isolation/security cases**

Create two uploaded datasets and prove results never cross. Create a student session and prove it cannot access another student, class results, quality issues or reference answers.

- [ ] **Step 3: Update README with exact fresh-clone commands**

Document `uv sync --frozen --all-groups`, `alembic upgrade head`, server start, six-file upload names, demo identity flow, test command, synthetic/real-data boundary and the model-unconfigured behavior.

- [ ] **Step 4: Regenerate and verify contracts**

Run: `uv run study-companion export-contracts`

Assert OpenAPI contains all new routes and no local absolute path or secret.

- [ ] **Step 5: Run the final gate from a clean clone**

Run:

```text
uv sync --frozen --all-groups
uv run alembic upgrade head
uv run pytest -q
uv run study-companion seed-demo
```

Expected: all tests pass; migration and seed succeed; `/api/v1/health` returns 200 with `database_ok=true`.

- [ ] **Step 6: Commit**

Commit: `git commit -m "完成A的P0端到端接线验收"`

## Dependency Order and Cut Line

```text
Task 1 边界/上传解析
  └─ Task 2 上传落库 + 基础目录
       └─ Task 3 分析运行/结果/证据
            └─ Task 4 建议生成/保存
                 └─ Task 6 端到端验收

Task 5 真实目录核对在 Task 2 后可并行，但必须在真实案例演示前完成。
```

11 月 25 日前的最小成功线是 Task 1–4 + Task 6 的合成数据链路。若时间不足，保留 Task 5 的代码与合成覆盖层测试，把真实页码核对作为明确的人工待办；不要提前进入 A-L1 库编辑、A-L2 审计表、A-L3 修订历史或 A-L5 模型循环。

## Self-Review Result

- 覆盖文档17中 A-1 至 A-5；A-L1 至 A-L5 明确延后，A-L4 另建计划。
- B/C 内部目录没有列为修改文件；A 只通过固定签名编排。
- 上传失败回滚、快照隔离、范围鉴权、版本一致性和未核对来源均有对应测试。
- `source_reviews` 存储决策仍待团队确认，因此本计划不提前创建表或契约字段。
- 真实资料是否入库仍待队长确认，计划默认全部留在被忽略目录。
