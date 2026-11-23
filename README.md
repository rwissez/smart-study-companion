# 智慧学伴（smart-study-companion）

面向学生和教师的本地学习支持原型，围绕学习要求梳理、个体与班级学情分析、个性化教学建议三项任务开发。分析与建议需要能够追溯到数据、材料和出处。

## 联调审阅分支

查看 [联调审阅指南](docs/integration-review.md)，可以用合成示例快速预览界面，也可以启动本机后端检查分析、审核发布和导出链路。

## 当前状态

A0-a（共享契约）与A0-b（后端骨架）已实现：可迁移数据库、载入合成样例、启动HTTP服务并通过测试。前端初始化（A0-b前端部分）由D并行进行，尚未并入。模型供应商、地址、模型名称和密钥默认未配置，可选接入；未配置时对话接口返回503 `MODEL_NOT_CONFIGURED`，不产生假模型回复，其余分析与材料功能照常工作。

## 本地运行

需要 Python 3.12 与 [uv](https://docs.astral.sh/uv/)。在仓库根目录执行：

```bash
uv sync                                   # 安装依赖（已锁定）
cp .env.example .env                      # 可选，缺省值即可运行
uv run alembic upgrade head               # 建库，默认 data/app.sqlite3
uv run study-companion seed-demo          # 载入合成演示数据，可重复执行
uv run uvicorn study_companion.main:app --reload --port 8000
```

验证：`curl http://127.0.0.1:8000/api/v1/health` 应返回 `"status":"ok"`；接口文档在 http://127.0.0.1:8000/api/docs 。

| 命令 | 作用 |
| --- | --- |
| `uv run pytest -q` | 运行全部测试 |
| `uv run study-companion seed-demo [--force]` | 载入合成演示数据；已存在时跳过，`--force` 先清空该数据集 |
| `uv run study-companion export-contracts` | 导出 `contracts/` 下的 JSON Schema、OpenAPI 与样例，输出逐字节可复现 |

`seed-demo` 只接受标记为 `synthetic` 的样例文件，不会导入赛题原始数据。

实测版本：Python 3.12.14、fastapi 0.165.0、uvicorn 0.54.0、sqlalchemy 2.1.9、alembic 1.20.1、pydantic 2.14.3、pydantic-settings 2.16.3、pandas 2.3.3、httpx 0.28.1、pytest 9.2.0。以 `uv.lock` 为准。

## 从哪里开始

所有成员先阅读[统一实施规范](docs/team/11_统一实施规范.md)，再阅读[架构与数据设计](docs/team/01_架构与数据设计.md)、[分工与里程碑](docs/team/02_分工与里程碑.md)、[接口协议](docs/team/08_接口协议.md)及自己的工作指南。

| 成员 | 主要职责 | 工作指南 |
| --- | --- | --- |
| A | 课程目录、共享模型、后端主流程、整合及统稿 | [A工作指南](docs/team/03_A工作指南.md) |
| B | 数据导入、质量检查、个体与班级统计 | [B工作指南](docs/team/04_B工作指南.md) |
| C | 教学建议、材料匹配、引用验证 | [C工作指南](docs/team/05_C工作指南.md) |
| D | 教师端与学生端界面、成果包及演示 | [D工作指南](docs/team/06_D工作指南.md) |

A需阅读全部手册。完整导览见[阅读顺序与决策](docs/team/00_阅读顺序与决策.md)，日常协作见[Git协作指南](docs/team/07_Git协作指南.md)，赛题覆盖检查见[任务逐项对照](docs/team/10_赛题任务逐项对照.md)，报名与交付准备见[报名与提交材料清单](docs/team/12_报名与提交材料清单.md)。

## 下一阶段：A0项目骨架

### A0-a：B、C先行所需的共享契约

已提供 Python 3.12、Pydantic v2 的 `study_companion.contracts` 共享类型，以及完全合成的 `tests/fixtures/synthetic_contracts.json`。B 使用 `RawTables`、`PreparedDataset`、`Catalog`、`RunContext` 实现导入和分析；C 使用 `StudentResult`、`Catalog`、`RunContext` 实现选材、建议与验证。函数签名以[统一实施规范](docs/team/11_统一实施规范.md)第四节为准。样例仅用于接口验证，不代表对真实赛题数据的计算结果。

在仓库根目录运行 `uv sync --python 3.12`，再运行 `uv run pytest -q`。B、C开始工作前应核对共享字段；字段变更由A统一维护。全量字段的 JSON Schema 与 OpenAPI 见 `contracts/`，由 `export-contracts` 生成，提交后可离线比对。

A负责共享数据模型与合成示例、后端入口、数据库迁移、健康检查、模拟会话、未配置模型适配器，以及启动和测试入口；D协作初始化前端。技术基线采用Python 3.12、uv、FastAPI、Pydantic v2、SQLite、SQLAlchemy 2.x、Alembic，以及React、TypeScript、Vite和npm。

A0验收要求：在全新克隆目录中，按README安装依赖、迁移数据库、载入合成样例并启动前后端。A0合入main后，B/C/D再从main创建各自功能分支。A0-b已创造这一条件，B（导入与分析）、C（选材与建议）、D（前端）不必等整个后端完成，可先在 8000 端口对着合成数据开工。

## 协作与文件管理

- 一个明确任务对应一个短期分支和一个合并请求（PR）；同一PR可以包含多次提交。
- 合并前检查改动、接口影响和验证结果，由另一名队员审核，A协调合并。main保留经过检查的成果。
- 赛事资料保存在代码仓库之外；data/和outputs/用于后续本地数据及结果，不提交。
- 不提交.env、密钥、数据库或原始赛事资料；未来的.env.example只包含无敏感信息的配置模板。
- Python和前端依赖锁文件需要提交，共享模型和数据库迁移由A协调变更。

正式赛题数据通过后续导入流程使用；测试样例使用明确标记的合成数据。提交时间及学校材料要求以实际通知为准。
