# 湛知（smart-study-companion）

面向教师与学生的本地智慧学伴：把课程要求、教材题库与真实作答，变成可追溯的学情分析与个性化学习方案，并让学生真的在线完成学习任务形成反馈闭环。

三项赛题任务分别对应：

1. **理解学科**：课程要求 → 主题/指标 → 知识点、关系、题目与评分点关联（A）。
2. **理解学生**：原始作答 → 清洗与评分 → 个人/班级统计、诊断、进展、比较（B）。
3. **理解教学**：学情依据 → 六类策略 → 任务组与材料 → 审核发布 → 学习事件回流（C）。

教师与学生各自有独立界面，Agent 通过受控工具读取同一套正式成果，不另建事实来源。

> 从零部署、导入独立评测数据的完整步骤见 [部署与数据准备](docs/交付/部署与数据准备.md)。本文只保留上手所需的最短路径。

## 快速开始

```powershell
# 1. 安装（仓库根目录）
uv sync --locked
cd frontend
npm install
cd ..

# 2. 配置
Copy-Item .env.example .env    # 然后填写模型三要素；不填模型也能先跑规则路径

# 3. 建库
uv run alembic upgrade head

# 4. 启动后端
uv run uvicorn study_companion.main:app --host 127.0.0.1 --port 8000
```

Linux 下把 `Copy-Item .env.example .env` 换成 `cp .env.example .env`，其余命令相同。

另开一个终端启动前端：

```powershell
cd frontend
npm run dev
```

打开 <http://127.0.0.1:5173>，在身份页选择教师或学生。Windows 也可以直接双击 `启动湛知.bat`：它会检查 8000/5173、先执行迁移、再启动两个服务并打开页面；`停止湛知.bat` 只停止本项目的进程。启动器的细节与常见问题见 [快捷启动](docs/快捷启动.md)。

- 健康检查：<http://127.0.0.1:8000/api/v1/health>
- 接口文档：<http://127.0.0.1:8000/api/docs>
- 前端接口清单（已冻结）：[docs/api-surface.md](docs/api-surface.md)

## 当前实现状态（如实）

| 能力 | 状态 | 说明 |
|---|---|---|
| 统一导入（CSV/XLSX/PDF 分类、内容寻址存证、待确认） | 已实现 | `POST /imports`，旧 `POST /datasets` 兼容 |
| 数据库统一迁移（main 与 D 双头合并为单 head） | 已实现 | `0001`—`0006`，空库与两个旧头升级均验证 |
| 正式版本与依赖（不可变版本、CAS 发布、依赖边与证据索引） | 已实现 | `versions` / `formal_heads` / `publications` / `artifact_edges` / `artifact_use` / `evidence_index` / `version_compatibility` |
| A：课程知识体系、教材文本层与质量报告 | 已实现（规则路径 + 模型路径） | 指标级知识点、关系、材料关联、覆盖与映射缺口、版本差异与影响集；教材 PDF 的文本层、章节与范围计划、定义句候选、页面级统计与页面来源走规则；配置模型后执行页面语义提取。真实教材端到端未验收 |
| B：学情统计、评分、进展、班级与诊断 | 已实现（规则路径 + 模型评分） | 分指标发布、选择/填空规则评分、进展四分类、掌握程度、错误类型、班级共性与重点关注学生、质量报告、教师评分修订；解答题按评分点两阶段模型评分，未配置模型时保持待复核 |
| C：选题、负担、策略试验与三条发布路径 | 已实现（规则路径 + 模型复核） | 候选评分/难度/指纹/负担/策略试验与刹车/质量报告/教师介入；`system_auto`、`teacher_direct`（`human_confirmed`）、`agent_revision` 均已通。配置模型后增加方案独立复核，每次任务有调用预算，失败时保留规则审核结果 |
| 微课堂（六类组件 schema + 完成事件） | 已实现（规则模板 + 模型生成） | 阅读只记接触、不计分；教师预览可读；配置模型后按知识点与错因生成，超出单次任务预算或模型失败时用规则模板，来源按实际生成方式标注 |
| 学习事件账本（幂等、序号、高水位、组完成触发） | 已实现 | 组完成→局部 B→C→重新发布 |
| 后台任务（入队去重、CAS 领取、租约恢复、取消、一次教师重试） | 已实现 | 单进程 worker，重启可恢复 |
| 导出（冻结闭包、16 文件 ZIP、预检、适用性、学习历史、授权下载） | 已实现 | `POST /runs/{run_id}/exports`、`GET /runs/{run_id}/exports/preflight` |
| 备份/校验/恢复 | 已实现 | `study-companion backup / verify-backup / restore` |
| Agent 对话（受控工具、确认卡、事实校验、工具轨迹） | 已实现（无模型时规则摘要） | 只读工具、候选生成→确认→`agent_revision` 发布、事实校验与 `tool_traces` 已通；语义回答需要模型 |
| Web 教师/学生页面 | 已实现 | `frontend/`：真实 API；方案编辑器、审核、质量、导出预检、确认卡均已对接。截图见 [docs/evidence/ui](docs/evidence/ui) 与 [docs/交付/screenshots](docs/交付/screenshots) |

> 未配置模型时，系统不会伪造模型成果：需要语义的任务返回明确的 `MODEL_NOT_CONFIGURED` 或标注「规则结果/待复核」。哪些功能降级见 [部署与数据准备 §3.4](docs/交付/部署与数据准备.md#34-不配置模型时哪些功能降级)。

## 环境要求

- Python **3.12**（`pyproject.toml` 限定 `>=3.12,<3.13`）、[uv](https://docs.astral.sh/uv/)
- Node.js **22.12 及以上**、npm
- Windows 10/11 或 Linux；macOS 同理。`启动湛知.bat` 只在 Windows 可用
- 默认 SQLite，数据在 `data/`，导出在 `outputs/`

系统 PATH 里的 `python` 不是 3.12 不影响使用；`uv sync` 会按项目要求选择 3.12。本机没有 3.12 时先执行：

```powershell
uv python install 3.12
```

## 配置 `.env`

必须从模板复制：

```powershell
Copy-Item .env.example .env
```

模型三要素是 `MODEL_PROVIDER`、`MODEL_BASE_URL`、`MODEL_NAME`，再加 `MODEL_API_KEY`。要用模型能力，这四项都必须填写。`MODEL_PROVIDER` 必须精确填 `openai_compatible`，接口地址填到 `/v1` 这一层。远端模型还要显式打开 `ALLOW_EXTERNAL_MODEL_INPUT`，本地 `127.0.0.1`/`localhost` 不需要。

也可以启动后在教师端「数据管理」→「模型与外发授权」里配置，设置写入 `data/model-settings.json`，覆盖 `.env` 且不需要重启。`.env` 改完则要重启后端。

只填三要素、不填密钥时，健康检查仍显示 `model_configured=true`，但调用会返回 `MODEL_NOT_CONFIGURED`。没有模型时导入、A 规则知识、B 统计与规则评分、C 规则方案、微课堂规则模板、正式导出都能用；Agent 语义对话与解答题模型评分不可用，部分对象可给规则摘要。

## 数据库：必须先迁移

空库不会自动建表。首次运行、每次升级代码后都要执行：

```powershell
uv run alembic upgrade head
uv run alembic heads
```

`alembic heads` 应为单头 `0006_delivery_backfill`。跳过迁移直接启动，业务接口会因缺表返回 500，后端日志出现 `no such table`。

`启动湛知.bat` 在需要启动后端时会先执行迁移，迁移失败则不启动后端。已有数据库升级前先备份，不要删除 `data/app.sqlite3` 重来。

## 准备数据并导入

仓库只带合成样例 `tests/fixtures/six_tables/`。真实数据、独立评测数据、教材与课标 PDF 都要自己准备；`.env`、`data/`、`outputs/`、`data/model-settings.json`、赛题数据都不随仓库下载。`data/` 在首次迁移时创建，`outputs/` 在首次导出时创建；里面的历史数据库、原始文件与导出包需要另行备份恢复。

六张逻辑表：课程要求、题目与材料、模拟学生、测验节点、作答记录、字段说明。程序按表头识别角色，不看文件名。字段口径、枚举、评分点格式与示例见 [部署与数据准备 §6](docs/交付/部署与数据准备.md#6-六张-csv-的字段口径)。

界面入口：身份页选「教师」→「数据管理」→「数据集」→ 导入方式选「新建独立数据系列」→ 选六张 CSV → 上传。任务链依次执行 `load` → `knowledge` → `analysis` → `plans`，四步都成功才算跑完。

命令行导入（PowerShell，已验证）：

```powershell
$session = Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8000/api/v1/demo-sessions `
  -ContentType 'application/json' `
  -Body (@{ role = 'teacher' } | ConvertTo-Json)
$token = $session.data.session_token

$form = @()
Get-ChildItem tests\fixtures\six_tables\*.csv | ForEach-Object {
  $form += @('-F', "files=@$($_.FullName)")
}
curl.exe -s -X POST http://127.0.0.1:8000/api/v1/imports `
  -H "Authorization: Bearer $token" @form
```

成功时返回 `dataset_id`、`dataset_family_id`、`job_id`、`input_status: "ready"`。不传 `metadata` 时按自动识别处理，同样新建独立 family；旧数据集不会被覆盖。同一份六表重复导入会产生两个数据集，导入前先确认要新建还是补充。

要用 `append_to_family` 往已有系列追加快照，必须同时传 `dataset_family_id` 与 `expected_parent_dataset_id`；界面的「补充到现有系列」目前不带这两个 ID，会被服务端拒绝。独立评测数据请用「新建独立数据系列」。

独立评测数据（10—20 名学生、100—400 条记录）的准备清单、编号闭环检查、模型配置与教材 PDF 的影响，见 [部署与数据准备 §7.4](docs/交付/部署与数据准备.md#74-独立评测数据准备清单)。

## 最短验证

四条命令覆盖后端、前端、导入与导出预检。详细版见 [部署与数据准备 §10](docs/交付/部署与数据准备.md#10-最短验证)。

```powershell
# 1. 健康检查：database_ok=true
curl.exe -s http://127.0.0.1:8000/api/v1/health

# 2. 前端：返回 200
curl.exe -s -o NUL -w "%{http_code}" http://127.0.0.1:5173/

# 3. 导入合成六表：返回 dataset_id 与 job_id
$session = Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8000/api/v1/demo-sessions `
  -ContentType 'application/json' `
  -Body (@{ role = 'teacher' } | ConvertTo-Json)
$token = $session.data.session_token
$form = @()
Get-ChildItem tests\fixtures\six_tables\*.csv | ForEach-Object {
  $form += @('-F', "files=@$($_.FullName)")
}
$receipt = curl.exe -s -X POST http://127.0.0.1:8000/api/v1/imports `
  -H "Authorization: Bearer $token" @form | ConvertFrom-Json

# 4. 导出预检：ready=true（任务跑完后执行）
$ds = $receipt.data.dataset_id
$bound = Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8000/api/v1/demo-sessions `
  -ContentType 'application/json' `
  -Body (@{ role = 'teacher'; dataset_id = $ds } | ConvertTo-Json)
$headers = @{ Authorization = "Bearer $($bound.data.session_token)" }
$runs = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/datasets/$ds/runs" -Headers $headers
$runId = $runs.data.items[0].run_id
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/runs/$runId/exports/preflight" -Headers $headers
```

Linux 下健康检查与前端检查分别为 `curl -s http://127.0.0.1:8000/api/v1/health` 与 `curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:5173/`。

## 常见失败

| 现象 | 原因与处理 |
|---|---|
| 503 `MODEL_NOT_CONFIGURED` | 三要素没填、`MODEL_PROVIDER` 不是 `openai_compatible`、或密钥无效。补齐配置；`.env` 改完重启后端 |
| 500 `MODEL_EXTERNAL_NOT_ALLOWED` | 远端模型未授权外发。确认合规后打开 `ALLOW_EXTERNAL_MODEL_INPUT` 或界面勾选 |
| 空库接口 500，日志 `no such table` | 没执行迁移。运行 `uv run alembic upgrade head` |
| 跨数据集 403 `FORBIDDEN` | 教师会话已绑定另一个数据集。回身份页重选，或新建绑定该数据集的会话 |
| 导出 422 `EXPORT_INCOMPLETE`，明细含 `PLAN_UNPUBLISHED` | 方案未发布。发布或重新生成方案后再预检，不能绕过预检 |
| 导入 `incomplete` / `needs_confirmation` | 缺表时补齐六表重传；文件角色未定时调用 `/imports/{id}/confirm` 指定角色 |
| 前端能开但请求全失败 | 后端未启动、端口不对或 `VITE_API_BASE_URL` 配错。前端不会回退演示数据 |

更完整的排查表见 [部署与数据准备 §9](docs/交付/部署与数据准备.md#9-常见失败)。

## 常用命令

| 命令 | 作用 |
|---|---|
| `uv run pytest -q` | 后端全量测试（合成数据） |
| `uv run alembic upgrade head` | 迁移到最新 head |
| `uv run alembic heads` | 查看当前 head（应为单头） |
| `uv run study-companion export-contracts` | 导出 JSON Schema / OpenAPI |
| `uv run study-companion backup --out .\backup\2026-11-28` | 停写窗口备份 |
| `uv run study-companion verify-backup .\backup\2026-11-28` | 校验备份 |
| `uv run study-companion restore .\backup\2026-11-28 --target .\restored` | 恢复演练 |
| `cd frontend; npm run typecheck; npm run build; npm test -- --run` | 前端质量门 |

## 目录结构

```text
src/study_companion/
  contracts/       共享契约（base/common/catalog/raw/analysis/plan/events/jobs/chat/views）
  api/             包络、错误、会话、幂等与全部路由
  db/              ORM 模型、版本仓储、证据解析
  jobs/            任务注册表、服务与 worker
  library/         数据集快照、统一导入、文件存证、学习事件账本
  curriculum/      课程目录与题库构建
  knowledge/       A：知识体系构建、读取与质量
  analysis/        B：清洗、统计、评分
  recommendation/  C：六策略、方案、微调、审核发布、微课堂
  agent/           受控工具循环与供应商适配
  exports/         正式成果导出（冻结、序列化、校验）
  maintenance/     备份、校验、恢复
  pipeline.py      把纯函数结果落成正式版本并发布
migrations/        Alembic 迁移（0001—0006）
tests/             后端测试与合成样本
frontend/          React + TypeScript + Vite 双端界面
docs/formal-design/ 设计与验收依据（00—09、视觉规范、决策记录）
docs/api-surface.md 已冻结的 HTTP 接口面
docs/交付/          交付说明、部署与数据准备、截图
docs/evidence/      实际运行证据（环境、测试输出、截图）
```

## 关键约束（实现时严格遵守）

- 原始记录、历史版本、已完成作答一律不改写；评分修订追加，原记录保留。
- 正式读取只走 `formal_heads` 指针；候选、草稿、被拒对象不进正式读取。
- 学生只能读本人；未完成题目不下发答案、解析、评分细则（路由层就裁剪，不靠前端隐藏）。
- 任务成功 ≠ 审核通过 ≠ 已发布：三者分别记录，页面上分别展示。
- 模型失败明确报错或降级为规则结果，并标注生成方式；不用预设文本冒充模型输出。
- 导出只打包冻结的正式版本闭包，不调用模型、不重算业务。

## 已知未完成项

不依赖模型的设计项已全部落地（见 [设计补齐清单](docs/设计补齐清单.md)）。剩下的是需要外部条件才能验收的部分：

1. 真实赛方数据（40 学生 2 班）与真实模型下的验收，需授权环境，当前标记 `not_run`。
2. A 的教材页面语义提取已接入模型；公式核对与关系判断尚未形成可验收能力，真实教材上的效果未验收。课标扫描件与教材公式提取存在已知错误，页码与公式需要人工核对。
3. C 的方案模型独立复核与微课堂模型生成已接入流水线，真实模型下的质量与成本未验收；每次任务有调用预算，超出后按规则结果继续。
4. 解答题按评分点逐项评分依赖模型；未配置模型时保持「待复核」，不给分。
5. Agent 的教学解释、事实组织与回答审查：程序侧事实校验与工具轨迹已完成，语义回答需要模型，真实模型下的效果未验收。
6. 界面「补充到现有系列」尚未收集 family 与父快照，追加导入目前只能用 API 完成。

## 数据与合规

- `.env`、密钥、数据库、原始赛事资料、学生作答原文都不进 Git（见 `.gitignore`）。
- 仓库自带的都是**合成**样例（`tests/fixtures/`），只用于验证机制，不证明真实教学成效。
- 未获授权不向外部模型发送赛事原始材料；`ALLOW_EXTERNAL_MODEL_INPUT` 默认关闭。
