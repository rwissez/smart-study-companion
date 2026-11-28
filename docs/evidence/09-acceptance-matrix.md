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
| UI-01 | **passed**（visual_review 待人工） | `node scripts/verify-pages.mjs` 退出码 0；1920/1440/1366 三档 `scrollWidth <= innerWidth` 全通过；页面纯白、教师墨绿 #234C42、学生青蓝 #326B82、Logo 原 PNG（字节一致）。截图 `docs/evidence/ui/01`—`51` |
| UI-02 | **passed**（synthetic） | 班级总览、比较、名册、个人画像四页签读取真实 API；不可比时只并列原指标与原因，不画差值 |
| UI-03 | **partial** | 直编入口与后端 preview/publish 均已存在，页面仍为占位（禁用并注明"编辑界面待接入"）；任务进度/重试/取消可用 |
| UI-04 | **passed**（synthetic） | 微课堂六类组件 schema、渲染器运行时校验、非法组件回退 `fallback_text` 且禁止完成；三题型作答、逐级提示、受助确认、待评分不显示 0 分 |
| UI-05 | **passed**（synthetic） | 加载/空/错误/未就绪/历史状态齐备；`verify-pages` 检查控制台 0 错误、学生页无受保护字段 |
| 前端质量门 | **passed** | `npm run typecheck` 退出码 0；`npm run build` 成功（402.78 kB，gzip 121.71 kB）；`npm test -- --run` 6 文件 20 用例全过 |
| ZIP-01 | **passed**（synthetic） | `test_exports.py::test_export_zip_contains_required_files_and_hashes`：16 文件、逐项 byte 数与哈希一致 |
| ZIP-02 | **partial** | 缺项 → 422 EXPORT_INCOMPLETE；`published_only=false` → 422；学生 → 403；"草稿不入包"另需专门用例 |
| ZIP-03 | **not_run** | 需要赛方授权数据与教师选定三学生 |
| RUN-01 | **partial** | 版本/指针持久化，重启后按 `formal_heads` 读取；HTTP 闭环实测；未做进程重启实测 |
| RUN-02 | **passed**（synthetic） | `tests/test_backup.py`：备份→校验→恢复；篡改后校验失败 |
| RUN-03 | **passed**（synthetic） | 运行模式与身份独立；`verify-pages` 分别在 api 模式（5173）与 demo 模式（5174）跑通，demo 页面带"演示数据"角标，API 失败不回退 |
| E2E-01 | **partial** | 真实 HTTP：导入→A→B→C→学生读方案（证据 `E2E-http.txt`）；浏览器完整 E2E 由 `verify-pages` 覆盖页面级路径 |
| E2E-02 | **partial** | 教师直编 preview/publish 后端可用；页面未接，Agent 修改路径未接 |
| E2E-03 | **passed**（synthetic） | `tests/test_learning_loop.py`：微课堂读取/完成→作答→评分→组完成→局部 B→C 复查；事件、状态、版本可查 |
| E2E-04 | **not_run** | 需要注入模型超时/worker 中断的故障演练 |
| PERF-01 | **not_run** | 需要在比赛机、固定条件记录三次耗时 |
| REL-01 | **passed** | `REL-01-clean-install.txt`：隔离目录克隆 `50722be`，`uv sync --locked` → `alembic upgrade head` → 启动 → 导入 → 四步任务成功 → 读画像 200 → `npm ci` → `npm run build` 全部成功 |
| REL-02 | **not_run** | 需要授权数据、真实模型与现场演练 |

## 4. 明确的外部阻塞

1. **真实模型**：本机没有经授权的模型端点与密钥。所有"真实模型"项保持 `not_run`。
2. **赛方真实数据**：工作区不含赛事数据包。所有真实 40 学生/2 班与三学生案例项保持 `not_run`。

## 5. 前端状态

前端已交付并实测（提交 `50722be` 起）：

- `npm run typecheck` 退出码 0，`npm run build` 成功，`npm test -- --run` 6 文件 20 用例全过；
- `node scripts/verify-pages.mjs` 对真实 dev server + 真实后端跑通，退出码 0，写入 49 张
  API 模式截图（1920/1440/1366）与 2 张 demo 模式截图到 `docs/evidence/ui/`；
- 自检覆盖：无横向溢出、学生页无受保护字段、控制台 0 错误、后端/前端/浏览器缺失时非 0
  退出（不会假成功）。

已知前端限制（页面均显示真实原因，未伪造）：

1. 教师学习方案编辑器仅占位（后端 preview/publish 已就绪）；
2. 微课堂实际内容只有 `text` + `citation` 组件（规则模板能给出的就这些），
   `formula/derivation/contrast/number_line` 由 `LessonRenderer.test.tsx` 单测覆盖，
   缺真实六类数据——需要模型生成更丰富内容后才能有真实样例；
3. 知识体系 409 未就绪状态由代码路径保证，本机 seed 每次都会跑完 A，截图不可复现；
4. 证据 `visibility=redacted` 分支已实现但后端不产生该状态，无真实截图。
