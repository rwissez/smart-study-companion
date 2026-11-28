# ENV-01 复现基线证据

- 采集时间：2026-11-27（Asia/Shanghai）
- 采集人：DSH（DeepSeek Harness）
- 采集模式：synthetic（本机环境与合成测试，未含赛事真实数据）

## 仓库与分支

| 项 | 值 |
|---|---|
| 远端 | https://github.com/rwissez/smart-study-companion.git |
| 核定 main | 49587a15430e4674a1a699e108c91e27b7a537b6（与 08 文档一致） |
| D 集成分支 | 64bd53a64bc0df7220a197e1e3b7e69a17a908d7（与 08 文档一致） |
| 文档导入提交 | 1d6db6fc45e7f7d785f194a73b83b6bd5f67cefb |
| 开发分支 | codex/final-integration |
| 派生基点 | main @ 49587a1 |

启动时逐一核对的远端分支（git ls-remote --heads，2026-11-27）：

```
64bd53a64bc0df7220a197e1e3b7e69a17a908d7  codex/d-integration-review-20261123
eb685036ede8705f24b437472debc256c0f7bf38  codex/d-web-demo
a6e606f92f2718561633725a063f1ebafab1fd7d  docs/team-design
0d73a9ff3b5f8fa35d0d1b8ddac7faaf42b37321  feat/a0-b
5c0a0487d8686a4ee9e08263980698f5c9a7b817  feat/b-analysis
20325b6399b25187ba4935866d941f73acd32193  feat/b-robust-import
9104c0d7ead2d1db8dd8e2e784fdee7b3d096ee0  feat/b-strategy-signal
142c84428ab540d79c253352adc4ac8ccef24a6f  feat/c-copy-refinements
3f5c002ea6666f278763d2813e0af75701f18be9  feat/c-recommendations
a0f3d6582795789e69ec3b076d27742ae7afd189  feat/c-sync-1125
772c942af6d4de4363b1f89433cc01c5532b6fb6  feat/contracts-a0a
7a8fee4c3958d63cec8312b380bf5e00fc4b2773  feat/knowledge-graph-ingestion
49587a15430e4674a1a699e108c91e27b7a537b6  main
```

与 08 §2 记载一致：main 49587a1、D 64bd53a、feat/knowledge-graph-ingestion 7a8fee4、feat/b-strategy-signal 9104c0d。`codex/docs-sync-draft`（文档叙述分支）未出现在远端分支列表，与文档描述一致，因此文档直接采用本机交接包已核验快照。

## 工具链

| 工具 | 实际版本 | 备注 |
|---|---|---|
| Python | 3.12.16（uv managed，`cpython-3.12-windows-x86_64-none`） | 主机自带 3.10.11 不满足 `requires-python>=3.12`；用 `uv python install 3.12` 安装受管解释器 |
| uv | 0.13.10 | 主机原先未安装，经 `python -m pip install --user uv` 安装 |
| Node.js | v26.1.0 | 高于 `frontend/package.json` 的 `>=22.12` |
| npm | 11.13.0 | 用于前端锁文件安装 |
| Git | 2.53.0.windows.2 | 全局代理原指向失效端口 7890，改为系统实际代理 7897 后可访问 GitHub |
| 平台 | Windows-11-10.0.26300-SP0 | |

## 依赖安装

命令：`uv sync --locked --python 3.12`
结果：成功。Resolved 42 packages、Checked 41 packages，全部取自仓库 `uv.lock`，未改动锁文件。

首次执行失败一次，原因记录如下（真实失败，保留痕迹）：hatchling 构建时以 UTF-8 读取 `.gitignore`，而该文件曾被 PowerShell `Add-Content` 以系统 ANSI（936）写入中文目录名，产生 `UnicodeDecodeError: 'utf-8' codec can't decode byte 0xd7`。修复方式是用 UTF-8 重写 `.gitignore`，未绕过构建检查。

## 后端基线测试

命令：`uv run pytest -q`
结果：**322 passed, 1 warning**（13.28s）。警告为 `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated`，属既有依赖告警，非本次改动引入。

## 前端基线（P0 时点状态）

main 分支**不存在 `frontend/` 目录**；前端仅存在于 D 集成分支（`codex/d-integration-review-20261123`）的 `frontend/` 树。D 前端为演示数据（`VITE_DATA_SOURCE=demo` 为默认）驱动的既有基线。P0 记录该事实：P6 前不能声称已有可运行前端。

## 已识别的既有缺口（后续阶段任务）

1. 前端未并入 main，需按 08 选择性迁入 D 前端并改真实 API。
2. `api/envelope.py` 为 `schema_version=1.0`，目标 1.1。
3. 无 `imports`、`jobs`、`versions`、`formal_heads`、`learning_events`、`attempts`、`grades`、`conversations`、`exports` 等正式表；仅有 12 张骨架表 + `learning_observations`。
4. 无 Alembic 双头合并；仅 main 的 `0002_learning_observations`，D 的 `0002_local_results`/`0003_idempotency` 未在 main。
5. 无统一 `ModelService`、无 `recommendation` 六策略规划器与 C-30 三路发布、无微课堂、无导出冻结。
