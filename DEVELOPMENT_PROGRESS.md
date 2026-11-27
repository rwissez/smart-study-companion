# 湛知开发进度（DSH 执行记录）

本文件是开发记录，不是 Web 功能。与 `execution_state.json` 同步维护，只记录**实际发生**的事实；未执行的测试一律写 `not_run`，不用"应该通过"替代。

- 依据：`docs/formal-design/`（00—09、07 视觉规范、v0.55-draft、最终一致性报告与待确认清单）、仓库根 `AGENTS.md`
- 分支：`codex/final-integration`（从核定 main `49587a1` 派生）
- 前端：`frontend/`（独立工作流交付，见文末状态）
- 验收对照：[docs/evidence/09-acceptance-matrix.md](evidence/09-acceptance-matrix.md)
- HTTP 端到端证据：[docs/evidence/E2E-http.txt](evidence/E2E-http.txt)

## 阶段总览

| 阶段 | 状态 | 关键事实 |
|---|---|---|
| P0 基线 | **passed** | 远端 SHA 与 08 一致；补齐 Python 3.12 与 uv；`uv sync --locked` 成功；基线 322 passed |
| P1 公共基础 | **passed（后端）** | 契约 1.1、DB 62 表单 head、导入/任务/版本仓储/包络/权限落地；331→334 passed |
| P2 A | **partial** | 课程指标级知识点、关系、材料关联、覆盖报告、前置环隔离已实现并随导入发布；教材正文语义提取需模型 |
| P3 B | **passed（规则路径）** | 分指标统计、修订选择、选择题/填空题规则评分、班级画像与比较、局部更新与组完成触发 |
| P4 C | **partial** | 六策略映射、任务组、三级提示、微课堂（规则模板）、`system_auto`+`teacher_direct` 发布、适用性复查；`agent_revision` 与模型完整审核未接 |
| P5 Agent | **partial** | 对话持久化、只读工具循环（沿用 main）、未配置模型时规则摘要；写工具/确认卡/受助确认未接 |
| P6 Web | **passed** | 教师四页与学生三页、真实 API、49 张截图三档分辨率自检通过；方案编辑器为显式占位 |
| P7 交付 | **partial** | 导出 ZIP（16 文件、回读校验）、备份/校验/恢复、真实 HTTP 闭环；真实模型与赛方数据未验 |

## 各阶段实际测试

| 命令 | 结果 |
|---|---|
| `uv sync --locked --python 3.12` | 成功，42 resolved / 41 checked，锁文件已更新（新增 openpyxl、python-multipart） |
| `uv run alembic heads` | `0006_delivery_backfill`（单头） |
| 空库 / `0002_learning_observations` / `0003_idempotency` 三条升级 | 均成功；`integrity_check=ok`、`foreign_key_check` 空、62 张表 |
| `uv run pytest -q` | **337 passed, 1 warning** |
| `_local/http_e2e.py`（真实 HTTP） | 见 E2E-http.txt：导入→A→B→C→学生作答/提示/评分→事件→导出下载全部成功 |
| `_local/backup_smoke.py` + `tests/test_backup.py` | 备份→校验→恢复通过；篡改后校验失败 |
| 前端 typecheck / build / vitest / verify-pages | 退出码全 0；6 文件 20 用例通过；49 张截图；控制台 0 错误 |

## 真实修复记录（保留，不掩盖）

1. **`.gitignore` 编码**：PowerShell `Add-Content` 以 ANSI 写入中文目录名，hatchling 以 UTF-8 读取时报 `UnicodeDecodeError`，`uv sync` 失败。改为 UTF-8 重写后通过。
2. **`activity_state` 外键顺序**：先写状态行、后写版本行导致 `FOREIGN KEY constraint failed`；改为先落不可变版本再写活动/状态。
3. **`JobScope` 拒绝 `export_id`**：导出任务 scope 带额外键被严格契约拒绝；给 `JobScope` 显式允许附加键（任务作用域是开放结构）。
4. **`RecommendationBundle` 缺 `review_info`**：导出读方案版本时 `AttributeError`；补共享字段，正式读取可展示 `human_confirmed`。
5. **WAL 快照漏读（真实并发缺陷）**：池化连接残留的读事务使"刚创建的会话"查不到，表现为连续建会话时 5/10 次 403。修复：请求开始时结束残留事务、SQLite 关闭 `pool_pre_ping`。复测 10/10 成功。
6. **组完成永远不触发**：子方案中老活动的 `origin_plan_version` 指向父方案，按版本过滤导致永不"完成"；改为按 `plan_activities` 解析活动身份。
7. **班级比较误判不可比**：按 `metric_id` 对齐（含班级前缀）导致跨班永远匹配不上；改为按 (指标, 知识点, 单位) 对齐。
8. **PowerShell 代理干扰**：主机系统代理（127.0.0.1:7897）会让本机 HTTP 请求间歇性失败；HTTP 证据脚本改用 httpx 直连。

## 已知缺口（影响范围）

- **A 教材级知识体系**：需要模型/OCR 与受控材料；当前只有课程指标级知识点与已登记关联。
- **C-30 的 agent_revision 路径与模型完整审核**：教师直编（human_confirmed）与系统自动审核已实现。
- **Agent 写操作**：方案修改/恢复、确认卡、学生受助确认、异步回执未接入；只读查询可用。
- **微课堂模型生成**：当前为规则模板（已审核、可发布）；模型生成待接入。
- **真实模型与赛方数据验收**：本机无授权端点与数据包，相关用例保持 `not_run`。

## 变更日志

- 2026-11-27：初始化工作区 Git 仓库（以核定 main 为基点，保留 GitHub 远端），导入交接包文档，建立 `codex/final-integration`。
- 2026-11-27：P0 环境与基线；P1 契约/DB/导入/任务/版本仓储；P2 A 基础版；P3 B 与学习闭环；P4 C 六策略/微课堂/教师直编；P5 对话持久化；P7 导出与备份。
- 2026-11-27：真实 HTTP 端到端闭环通过并留存证据；修复第 5—8 项缺陷。
