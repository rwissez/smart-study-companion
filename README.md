# 湛知（smart-study-companion）

面向教师与学生的本地智慧学伴：把课程要求、教材题库与真实作答，变成可追溯的学情分析与个性化学习方案，并让学生真的在线完成学习任务形成反馈闭环。

三项赛题任务分别对应：

1. **理解学科**：课程要求 → 主题/指标 → 知识点、关系、题目与评分点关联（A）。
2. **理解学生**：原始作答 → 清洗与评分 → 个人/班级统计、诊断、进展、比较（B）。
3. **理解教学**：学情依据 → 六类策略 → 任务组与材料 → 审核发布 → 学习事件回流（C）。

教师与学生各自有独立界面，Agent 通过受控工具读取同一套正式成果，不另建事实来源。

## 当前实现状态（如实）

| 能力 | 状态 | 说明 |
|---|---|---|
| 统一导入（CSV/XLSX/PDF 分类、内容寻址存证、快照去重、待确认） | 已实现 | `POST /imports`，旧 `POST /datasets` 兼容 |
| 数据库统一迁移（main 与 D 双头合并为单 head） | 已实现 | `0001`—`0006`，空库与两个旧头升级均验证 |
| 正式版本与依赖（不可变版本、CAS 发布、依赖边与证据索引） | 已实现 | `versions` / `formal_heads` / `publications` / `artifact_edges` / `artifact_use` / `evidence_index` / `version_compatibility` |
| A：课程知识体系、教材文本层与质量报告 | **规则级已实现** | 指标级知识点、关系、材料关联、覆盖与映射缺口、版本差异与影响集；教材 PDF 文本层、章节/范围计划、定义句候选、页面级统计与单页来源；**页面语义提取需要模型** |
| B：学情统计、评分、进展、班级与诊断 | 已实现（规则路径） | 分指标发布、选择/填空规则评分、进展四分类、掌握程度、错误类型、班级共性与重点关注学生、质量报告、教师评分修订 |
| C：选题、负担、策略试验与四条发布路径 | 已实现（无模型路径） | 候选评分/难度/指纹/负担/策略试验与刹车/质量报告/教师介入；`system_auto`、`teacher_direct`、`agent_revision`、人工确认均已通 |
| 微课堂（六类组件 schema + 规则模板生成 + 完成事件） | 已实现（生成用规则模板） | 阅读只记接触、不计分；教师预览可读；模型生成内容待接入 |
| 学习事件账本（幂等、序号、高水位、组完成触发） | 已实现 | 组完成→局部 B→C→重新发布 |
| 后台任务（入队去重、CAS 领取、租约恢复、取消、一次教师重试） | 已实现 | 单进程 worker，重启可恢复 |
| 导出（冻结闭包、16 文件 ZIP、预检、适用性、学习历史、授权下载） | 已实现 | `POST /runs/{run_id}/exports`、`GET /runs/{run_id}/exports/preflight` |
| 备份/校验/恢复 | 已实现 | `study-companion backup / verify-backup / restore` |
| Agent 对话（受控工具、确认卡、事实校验、工具轨迹） | 已实现（无模型时规则摘要） | 只读工具、候选生成→确认→`agent_revision` 发布、事实校验与 `tool_traces` 已通；语义回答需模型 |
| Web 教师/学生页面 | **已实现** | `frontend/`：真实 API、三档分辨率自检通过、70 张截图；方案编辑器、审核、质量、导出预检、确认卡均已对接 |

> 未配置模型时，系统不会伪造模型成果：需要语义的任务返回明确的 `MODEL_NOT_CONFIGURED` 或标注"规则结果/待复核"。

## 环境要求

- Python **3.12**、[uv](https://docs.astral.sh/uv/)
- Node.js ≥ 22（本机验证用 v26）、npm
- Windows / macOS / Linux；默认 SQLite，数据在 `data/`，导出在 `outputs/`

主机若没有 3.12：

```powershell
python -m pip install --user uv
uv python install 3.12
```

## 启动（后端）

```powershell
Copy-Item .env.example .env
uv sync --locked --python 3.12
uv run alembic upgrade head
uv run uvicorn study_companion.main:app --host 127.0.0.1 --port 8000
```

- 健康检查：<http://127.0.0.1:8000/api/v1/health>
- 接口文档：<http://127.0.0.1:8000/api/docs>
- 前端接口清单（已冻结）：[docs/api-surface.md](docs/api-surface.md)

### 启动前端

```powershell
cd frontend
Copy-Item .env.example .env.local   # 如目录下提供；否则直接创建 .env.local
npm install
npm run dev
```

前端默认 `VITE_DATA_SOURCE=api`、`VITE_API_BASE_URL=http://127.0.0.1:8000/api/v1`。API 失败时显示真实错误，**不会**回退演示数据；`demo` 模式必须显式设置且页面标注"演示数据"。

## 导入一份数据

六张 CSV（或一份六工作表 XLSX）字段见 `docs/formal-design/01_统一接口与数据规范.md` §3.1。仓库自带合成样例：`tests/fixtures/six_tables/`。

```powershell
# 教师 bootstrap 会话
$session = Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/v1/demo-sessions `
  -ContentType 'application/json' -Body '{"role":"teacher"}'
$token = $session.data.session_token

# 上传六张表（PowerShell 7）
$form = @{
  metadata = '{"intent":"new_family"}'
  files = Get-ChildItem tests/fixtures/six_tables/*.csv | ForEach-Object {
    @{ GetMember = 'files'; Name = $_.Name; Value = $_.FullName }
  }
}
```

更简单的做法是用后端自带脚本（会打印数据集与任务 id）：

```powershell
uv run python _local/e2e_import.py
```

导入成功后自动排队：A 知识体系 → B 学情 → C 方案 → 微课堂补丁；用 `GET /api/v1/jobs/{job_id}` 看真实进度。

## 常用命令

| 命令 | 作用 |
|---|---|
| `uv run pytest -q` | 后端全量测试（合成数据） |
| `uv run alembic upgrade head` | 迁移到最新 head |
| `uv run alembic heads` | 查看当前 head（应为单头） |
| `uv run study-companion export-contracts` | 导出 JSON Schema / OpenAPI |
| `uv run study-companion backup --out .\backup\2026-11-27` | 停写窗口备份 |
| `uv run study-companion verify-backup .\backup\2026-11-27` | 校验备份 |
| `uv run study-companion restore .\backup\2026-11-27 --target .\restored` | 恢复演练 |
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
docs/evidence/      实际运行证据（环境、测试输出、截图）
```

## 关键约束（实现时严格遵守）

- 原始记录、历史版本、已完成作答一律不改写；评分修订追加而不是覆盖。
- 正式读取只走 `formal_heads` 指针；候选、草稿、被拒对象不进正式读取。
- 学生只能读本人；未完成题目不下发答案、解析、评分细则（路由层就裁剪，不靠前端隐藏）。
- 任务成功 ≠ 审核通过 ≠ 已发布：三者分别记录，页面上分别展示。
- 模型失败明确报错或降级为规则结果，并标注生成方式；不用预设文本冒充模型输出。
- 导出只打包冻结的正式版本闭包，不调用模型、不重算业务。

## 已知未完成项

不依赖模型的设计项已全部落地（见 [设计补齐清单](docs/设计补齐清单.md)）。剩下的是需要外部条件才能验收的部分：

1. A 的教材页面语义提取、公式核对与关系判断（需要模型与受控图像能力）；教材候选链路已实现，合成样例没有教材 PDF，端到端待真实教材验证。
2. C 的模型独立复核与一次修复；微课堂模型生成。
3. Agent 的教学解释、事实组织与回答审查（程序侧事实校验与工具轨迹已完成）。
4. 解答题按评分点逐项评分（未配置模型时保持“待复核”，不给分）。
5. 真实赛方数据（40 学生 2 班）与真实模型下的验收，需授权环境，当前标记 `not_run`。

## 数据与合规

- `.env`、密钥、数据库、原始赛事资料、学生作答原文都不进 Git（见 `.gitignore`）。
- 仓库自带的都是**合成**样例（`tests/fixtures/`），只用于验证机制，不证明真实教学成效。
- 未获授权不向外部模型发送赛事原始材料；`ALLOW_EXTERNAL_MODEL_INPUT` 默认关闭。
