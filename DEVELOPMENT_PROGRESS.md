# 湛知开发进度（DSH 执行记录）

本文件是开发记录，不是 Web 功能。与 `execution_state.json` 同步维护，只记录**实际发生**的事实；未执行的测试一律写 `not_run`，不用"应该通过"替代。

- 依据：`docs/formal-design/`（00—09、07 视觉规范、v0.55-draft、最终一致性报告与待确认清单）、仓库根 `AGENTS.md`
- 分支：`codex/final-integration`（从核定 main `49587a1` 派生）
- 文档导入提交：`1d6db6f`

## P0 基线（passed）

| 项 | 实际结果 |
|---|---|
| 远端核对 | main `49587a1`、D `64bd53a` 与 08 记载一致；`codex/docs-sync-draft` 确实不在远端分支列表，故文档取交接包已核验快照 |
| 环境 | Python 3.12.16（uv 受管，主机原生仅 3.10.11）；uv 0.13.10（新装）；Node v26.1.0；npm 11.13.0；Windows 11 |
| 依赖 | `uv sync --locked --python 3.12` 成功，42 resolved / 41 checked，锁文件未改 |
| 后端基线 | `uv run pytest -q` → **322 passed, 1 warning**（13.28s） |
| 前端基线 | main 无 `frontend/`；D 分支才有前端，且默认 demo 数据源 |
| 证据 | [docs/evidence/ENV-01-baseline.md](evidence/ENV-01-baseline.md) |

真实失败记录：首次 `uv sync` 因 `.gitignore` 被 PowerShell 以 ANSI 写入中文目录名，hatchling 以 UTF-8 读取时报 `UnicodeDecodeError`。以 UTF-8 重写 `.gitignore` 后通过，未绕过构建校验。

## 阶段状态总览

| 阶段 | 状态 | 门禁 |
|---|---|---|
| P0 基线 | passed | ENV-01 passed |
| P1 公共基础 | pending | API-01~04、DB-01/02/04、SEC-01、JOB-01/02 全部 not_run |
| P2 A | pending | A-01~03 not_run |
| P3 B | pending | B-01~04、DB-03 not_run |
| P4 C | pending | C-01~04、SEC-02 not_run |
| P5 Agent | pending | AG-01~04、JOB-03 not_run |
| P6 Web | pending | UI-01~05 not_run |
| P7 交付 | pending | ZIP/RUN/E2E/PERF/REL 全部 not_run |

## 已知外部阻塞（不阻塞其余开发）

1. **真实模型**：本机无经授权模型端点与密钥。涉及"真实模型"的验收项保持 `not_run`，规则与合成路径照常实现。
2. **赛方真实数据**：工作区不含赛事原始数据包。真实 40 学生/2 班数据与三学生典型案例验收保持 `not_run`，同一代码路径用合成数据验证。

## 变更日志

- 2026-11-27：初始化工作区 Git 仓库（以核定 main 为基点，保留 GitHub 远端），导入交接包 `AGENTS.md` 与 `docs/formal-design/`；建立 `codex/final-integration` 分支；完成 P0 环境、依赖与基线测试核对。
