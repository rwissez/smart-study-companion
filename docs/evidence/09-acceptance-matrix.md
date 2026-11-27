# 09 验收对照（DSH 实际执行记录）

本文件只记录**实际执行过**的结果。未执行一律写 `not_run`，未通过写 `failed`。
环境与命令见每行"证据"；完整环境基线见 [ENV-01-baseline.md](ENV-01-baseline.md)。

- 分支：`codex/final-integration`
- 后端测试：`uv run pytest -q` → **329 passed, 1 warning**（2026-11-27，Windows 11 / Python 3.12.16）
- 前端：见文末"前端状态"，未通过前不写通过

## 1. 公共、权限、数据库、任务

| 编号 | 结果 | 实际证据 | 说明 |
|---|---|---|---|
| ENV-01 | **passed**（synthetic） | ENV-01-baseline.md；`uv sync --locked --python 3.12`；`uv run pytest -q` | 42 packages resolved / 41 checked；322→329 passed |
| API-01 | **partial** | `contracts/` 拆分与 `schema_version=1.1` 已落地；`tests/contracts/test_models.py` 通过 | OpenAPI→TS 自动生成与漂移检查**未接入**（前端类型手写并对照 docs/api-surface.md） |
| API-02 | **passed**（synthetic） | `tests/test_pipeline_e2e.py::test_import_runs_pipeline_and_publishes_plans`、`::test_class_profile_keeps_roster_denominator` | 指标按 (学生,节点,指标) 发布；名册分母含无记录学生 |
| API-03 | **partial** | `tests/test_pipeline_e2e.py::test_student_learning_loop_grades_and_records_events`（幂等重放）、`test_exports.py::test_export_rejects_unpublished_scope`（`published_only=false` → 422） | 教师 preview→publish 已有实现与硬门；`publish=true` 只进入预览，不直发；**未写**对应的 API-03 专用用例文件 |
| API-04 | **partial** | 幂等：`tests/test_pipeline_e2e.py`（同键提交返回原回执、submission_no 不变） | 版本冲突 409 的显式并发用例 `not_run` |
| SEC-01 | **passed**（synthetic） | `test_student_cannot_read_another_student`（403 FORBIDDEN）、`test_exports.py::test_export_rejects_..._unauthorized_student`（学生导出 403） | 教师跨数据集/班级的范围裁剪在路由层实现 |
| SEC-02 | **partial** | `test_student_learning_loop_...`：未提交前 `GET /materials/{id}` 无答案/评分细则字段 | 聊天与微课堂通道的泄答拦截**未测**；`check_hint` 仅用于教师编辑硬门 |
| DB-01 | **passed** | `alembic heads` → `0006_delivery_backfill`；空库、`0002_learning_observations`、`0003_idempotency` 三条升级路径均 `integrity_check=ok`、`foreign_key_check` 空、62 张表 | 见 P0/P1 提交与 `_local/check_db.py` 输出 |
| DB-02 | **passed**（synthetic） | 导入：内容寻址存证 + 快照去重（`library/datasets.save_dataset` 同 family 同 snapshot_hash 返回已有 id）；`test_pipeline_e2e` 导入成功 | 跨 family 不合并的逻辑已实现，**未写**专门用例 |
| DB-03 | **partial** | `student_update` 任务只发布受影响学生与班级版本（`_carry_over_states` 保留已完成/进行中活动） | 未写"另一学生版本不变"的断言用例 |
| DB-04 | **partial** | CAS 发布：`version_repository.publish` 以 generation+version 做条件更新，零行抛 409 | 并发注入用例 `not_run` |
| JOB-01 | **passed**（synthetic） | `tests/test_pipeline_e2e.py`：四步任务 `load/knowledge/analysis/plans` 全 succeeded；`jobs.py` 事件分页 | 重启恢复只在 `recover_expired` 层实现，**未做**进程重启实测 |
| JOB-02 | **partial** | `test_job_retry_requires_failed_job`（非失败任务重试 → 409 JOB_RETRY_NOT_AVAILABLE） | "最终失败任务一次手动重试成功"的正向路径**未实测** |
| JOB-03 | **partial** | 组完成后写 `group_completed` 并入 outbox → `student_update`；事件账本 4 类事件实测 | 网络中断/乱序用例 `not_run` |

## 2. A / B / C / Agent

| 编号 | 结果 | 证据 | 说明 |
|---|---|---|---|
| A-01 | **not_run** | — | 需要真实 PDF/教材页面与模型或 OCR；本轮未提供受控材料 |
| A-02 | **partial** | 规则关系与前置环隔离在 `knowledge/service.py`；知识版本随导入发布（`versions.kind=knowledge`） | 关系类型/证据的模型复核未执行 |
| A-03 | **partial** | 知识版本不可变（版本行 + 发布记录）；`knowledge/reader` 按精确版本读取 | 纯改名兼容证明与影响集计算未实现 |
| B-01 | **passed**（synthetic） | `test_import_runs_pipeline_and_publishes_plans`：T01I1=5/5、T01I2=0/5（真实 0 分与缺失分开） | 分母/分子逐项核对 |
| B-02 | **partial** | 修订选择（R01 rev1 → R02 rev2 生效，SUPERSEDED_REVISION 记录）；选择题规则评分 | 解答题需模型，未配置时为 `needs_review`（有用例断言"不显示 0 分"） |
| B-03 | **partial** | 班级画像含 roster_count 与 observed_student_count；比较接口按同口径返回 `comparable` | 进展分类阈值未标定；不可比用例 `not_run` |
| B-04 | **partial** | `student_update` 局部更新并保留活动状态 | 未写"只更新受影响个人/班级"的断言 |
| C-01 | **partial** | 规则建议 + 任务组 + 材料准入（`validate_recommendations`、`publication.hard_checks`） | 六策略评分表（04 §4.2 权重）未实现为独立模块 |
| C-02 | **partial** | 三路径中 `system_auto` 与 `teacher_direct` 已实现；`agent_revision` 未接入 | 模型完整审核未接入 |
| C-03 | **not_run** | — | 适用性复查（plan_applicability）表已建，判定流程未实现 |
| C-04 | **partial** | 微课堂 schema（六类组件）+ 规则模板生成 + 校验/审核/完成事件 | 模型生成、教师草稿预览未接入 |
| AG-01 | **not_run** | — | 需要真实模型；工具注册与只读 B/A 工具沿用 main 并有既有单测 |
| AG-02 | **not_run** | — | 确认卡与批量修改未接入 |
| AG-03 | **partial** | 供应商适配器的结构校验/超时由 `model_service.generate_structured` 实施；既有 `tests/agent/*` 用 MockTransport | 真实模型异常矩阵 `not_run` |
| AG-04 | **not_run** | — | 学生受助确认未接入 |

## 3. Web / 导出 / 运维

| 编号 | 结果 | 说明 |
|---|---|---|
| UI-01~05 | 见前端提交与截图 | 前端由独立工作流交付；未通过前不写 passed |
| ZIP-01 | **passed**（synthetic） | `test_exports.py::test_export_zip_contains_required_files_and_hashes`：16 文件、逐项 byte 数与哈希一致 |
| ZIP-02 | **partial** | 缺项 → 422 EXPORT_INCOMPLETE；`published_only=false` → 422；学生 → 403 | "草稿不入包"另需专门用例 |
| ZIP-03 | **not_run** | 需要赛方授权数据与教师选定三学生 |
| RUN-01 | **partial** | 版本/指针持久化，重启后按 `formal_heads` 读取；未做进程重启实测 |
| RUN-02 | **passed**（synthetic） | `tests/test_backup.py`：备份→校验→恢复；篡改后校验失败 |
| RUN-03 | **partial** | 运行模式与身份是独立维度（`DEMO_MODE` vs `VITE_DATA_SOURCE`）；后端无 demo fixture 回退 | 前端 api 模式失败行为待前端联调确认 |
| E2E-01~04 | **partial** | 后端闭环已实测：导入→A→B→C→学生作答/提示/评分→事件→导出。**未**在真实浏览器+真实模型下跑完整 E2E |
| PERF-01 | **not_run** | 需要在比赛机、固定条件记录三次耗时 |
| REL-01 | **partial** | 干净目录 `uv sync --locked` + `alembic upgrade head` + 启动已验证；README 已按实际命令更新 |
| REL-02 | **not_run** | 需要授权数据、真实模型与现场演练 |

## 4. 明确的外部阻塞

1. **真实模型**：本机没有经授权的模型端点与密钥。所有"真实模型"项保持 `not_run`。
2. **赛方真实数据**：工作区不含赛事数据包。所有真实 40 学生/2 班与三学生案例项保持 `not_run`。

## 5. 前端状态

前端实现与截图由 `frontend/` 提交与 `docs/evidence/ui/` 记录；本文件在其通过
`npm run typecheck` / `npm run build` / `npm test` / 浏览器截图检查后更新为实际结论。
在本轮记录时点，前端尚未完成联调，故 UI-01~05 保持 `not_run`。
