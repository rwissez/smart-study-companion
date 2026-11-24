# 智慧学伴（smart-study-companion）

面向学生和教师的本地学习支持原型。项目围绕三项任务建设：

1. 梳理学习要求、课程目录和知识点关系；
2. 基于可复核数据形成学生与班级学情分析；
3. 根据分析事实和材料证据生成个性化教学建议。

所有分析、建议和模型回答都应能够追溯到数据、材料、教材页码或明确的证据引用。

## 当前状态

当前分支已经具备可运行的后端骨架、数据分析能力、知识点图谱增量模块、学习观察事件存储，以及第一阶段“B 分析工具多轮解释”智能体链路。完整的教育智能体闭环仍在推进中。

| 模块 | 当前状态 | 主要能力 |
| --- | --- | --- |
| A：契约与后端 | 已完成基础版本 | Pydantic 契约、FastAPI、SQLite、Alembic、会话和权限边界 |
| B：数据与分析 | 已完成基础版本 | 六表准备、修订选择、质量隔离、学生/班级统计、作答证据 |
| C：规则建议 | 已完成基础版本 | 材料搜索、三类学生建议规则、引用和泄答校验 |
| 知识点图谱 | 已完成增量模块 | PDF/图片处理边界、知识点候选、关键词定位、关系校验和 Catalog 投影 |
| 受控智能体 | B 工具解释链路已验证 | OpenAI 兼容 Provider、工具调用循环、角色工具白名单、证据轨迹 |
| 学习观察事件 | 后端骨架已完成 | 学生事件提交、教师查询、事件序号约束；尚未触发新分析 |
| 前端 | 尚未并入 | `frontend/` 目录和浏览器端工具轨迹展示仍待完成 |

第一阶段智能体已经通过本地 `MockTransport` 的协议级多轮验收：

```text
教师询问学生为什么失分
  -> get_student_analysis
  -> get_answer_evidence
  -> 模型根据真实工具结果生成回答
  -> 返回指标和作答记录 evidence_refs
```

这证明了 Provider、AgentLoop、数据库上下文、B 工具和权限边界可以协同工作，但不等同于已经使用真实授权模型完成实机验收。默认不配置模型，不会产生假模型回答，也不会自动向远程服务发送数据。

## 快速开始

环境要求：

- Python 3.12；
- [uv](https://docs.astral.sh/uv/)；
- Windows、macOS 或 Linux 均可运行；
- 默认使用 SQLite，本地数据保存在 `data/`。

在仓库根目录执行：

```bash
uv sync
cp .env.example .env
uv run alembic upgrade head
uv run study-companion seed-demo
uv run uvicorn study_companion.main:app --reload --port 8000
```

Windows PowerShell 可以使用：

```powershell
Copy-Item .env.example .env
```

启动后检查：

- 健康检查：http://127.0.0.1:8000/api/v1/health
- OpenAPI 文档：http://127.0.0.1:8000/api/docs
- ReDoc：http://127.0.0.1:8000/api/redoc

`seed-demo` 只接受明确标记为 `synthetic` 的合成样例，不会导入竞赛原始数据。

## 常用命令

| 命令 | 作用 |
| --- | --- |
| `uv run pytest -q` | 运行全部测试 |
| `uv run alembic upgrade head` | 执行数据库迁移 |
| `uv run alembic heads` | 查看当前迁移 head |
| `uv run study-companion seed-demo` | 载入合成演示数据 |
| `uv run study-companion seed-demo --force` | 清空并重新载入合成演示数据 |
| `uv run study-companion export-contracts` | 导出 JSON Schema、OpenAPI 和契约样例 |
| `uv run study-companion split-textbook --help` | 查看教材切段和页码核对命令 |
| `uv run pytest tests/knowledge -q` | 运行知识点图谱测试 |
| `uv run pytest tests/api/test_chat_agent.py -q` | 运行智能体多轮闭环验收测试 |

当前全量回归结果：`186 passed`，另有一个既有的 Starlette/httpx 弃用警告。

## 智能体任务路由与下一步

当前 `/chat` 已从固定的单生失分解释扩展为“显式任务优先、规则优先、模糊时模型分类”的路由结构。模型分类只接收最新用户消息和角色允许的任务枚举，不接收工具，也不能选择学生、班级、数据集、运行或节点。目标必须在请求中显式给出，并由会话和数据集范围校验。

已接入现有 B 工具、可以执行的任务：

| 任务类型 | 用途 | 需要的目标/上下文 | 当前工具 |
| --- | --- | --- | --- |
| `student_loss_explanation` | 解释学生失分 | 学生、run、node | 学生分析、作答证据 |
| `data_quality` | 说明数据质量问题 | 学生或当前数据集、run | 质量问题 |
| `class_analysis` | 分析班级共性 | 教师、班级、run、node | 班级分析 |
| `out_of_scope` | 拒绝无关任务并提示支持范围 | 无 | 固定安全回复，不调用模型或工具 |

任务类型和提示词位置已为以下场景预留，但由于所需工具或校验尚未接入，会返回 `TASK_NOT_AVAILABLE`，不会让模型使用无关工具作答：`evidence_lookup`、`student_progress`、`class_data_quality`、`student_learning_advice`、`study_plan`、`class_strategy`、`knowledge_lookup`、`prerequisite_query`、`material_search`、`concept_tutoring`、`relation_review`、`observation_review`。

### 提示词结构

- `prompts/router.md`：模糊请求的任务分类提示词，仅输出任务枚举，不决定目标或执行工具。
- `prompts/tasks/<task_type>.md`：16 类任务各自的版本化提示词，列出目标、证据要求、回答结构、限制和证据不足时的表达；pending 文件标明待接入能力。
- `PromptRegistry` 返回任务和角色提示词版本；模型分类参与时也记录 `router-v1`。

规则唯一命中时不增加分类模型调用；没有命中或规则冲突时，最多调用一次无工具分类。分类输出在服务端严格解析和校验。`student_id` 兼容旧请求并等价于学生 `target`；新请求可以提供 `target: {"type":"student|class","id":"..."}`。学生目标、班级 roster、节点和分析运行会由服务端校验并绑定，工具参数不能切换范围。

### 后续阶段

| 优先级 | 尚未完成 | 建议阶段 |
| --- | --- | --- |
| P0 | `AgentTaskState` 的缺失信息、策略选择和下一动作尚未形成程序化决策状态 | A-Agent-7 后续完善 |
| P0 | A 知识图谱查询、C 材料/策略/建议工具未接入 | A-Agent-8 |
| P0 | 最终输出证据引用、泄答、负荷和角色表达校验及模型失败规则回退 | A-Agent-9 |
| P1 | 对话范围/历史、Agent 请求和工具轨迹未持久化 | 后续后端工作 |
| P1 | 总请求时限、安全重试、调用/Token 预算和统一模型上下文裁剪不足 | 后续运行保护工作 |
| P1 | 学习观察事件尚未触发新分析和下一轮策略 | A-Agent-10 |
| P2 | 预生成/缓存、知识关系候选自动生成和人工确认入口 | 后续迭代 |
| P2 | 获授权真实模型的质量、速度和降级案例评测 | 接入真实模型后执行 |
| P2 | 前端任务选择、目标选择和真实工具轨迹展示 | D 前端工作 |

当前模型协议和 B 工具链路已由 `MockTransport` 多轮测试验证；这不等于真实授权模型实机质量验收。确定性统计、权限、目标范围和任务可用性仍由程序决定，提示词不构成安全边界。

## 核心架构

```text
用户请求
  -> FastAPI 路由与会话权限
  -> ServerContext（数据集、运行、学生范围、Catalog 版本）
  -> 当前：B 分析事实、质量问题、作答证据
  -> 后续：A 知识查询、C 材料与策略工具
  -> AgentLoop：工具调用 -> 观察结果 -> 再规划
  -> 后续：最终输出校验门与持久化轨迹
  -> 候选回答或候选建议
```

系统采用“确定性事实优先”的边界：

- B 负责清洗、统计、质量问题和作答证据；
- C 负责规则建议、材料匹配、引用校验和泄答检查；
- A 负责契约、上下文、工具白名单、模型适配和整合；
- 模型只能选择服务端提供的只读工具、解释工具结果和组织回答；
- 模型不能修改成绩、重算官方统计、扩大数据范围或直接发布建议。

## 真实模型配置

默认 `.env` 不配置模型。需要使用经过授权的本地模型或 OpenAI 兼容服务时，填写：

```dotenv
MODEL_PROVIDER=openai_compatible
MODEL_BASE_URL=http://127.0.0.1:11434/v1
MODEL_NAME=你的模型名称
MODEL_API_KEY=
```

配置后，教师对话请求需要提供运行和学生上下文，例如：

```json
{
  "run_id": "synthetic-run",
  "student_id": "S01",
  "node_id": "N01",
  "messages": [
    {"role": "user", "content": "S01 为什么失分？"}
  ]
}
```

模型调用过程由服务端控制：

- Provider 发送工具 schema 并解析 `tool_calls`；
- AgentLoop 最多执行 8 次工具调用；
- 工具参数在服务端校验；
- 学生和教师获得不同的工具集合；
- 工具轨迹记录调用编号、工具名、状态和证据引用；
- 越权、范围不匹配和未注册工具调用会终止本次任务；
- 最终模型文本目前尚未接入完整程序校验门，不能视为已完成引用、泄答和策略负荷校验；
- 模型失败时的规则文本回退尚未完整接入；
- 未配置模型时返回 `MODEL_NOT_CONFIGURED`，不会回退成伪造回答。

## 知识点图谱与教材处理

知识点处理不修改既有 HTTP 接口、共享契约、B/C 纯函数签名或既有业务统计口径。

```text
PDF / 图片
  -> B: split_textbook
       章节切段、文件页/印刷页映射、连续性检查
  -> A: knowledge.ingest
       新出现知识点、关键词索引、单元/章节/页码定位
  -> 人工或受控智能体核对
       知识点关系、题目和材料标注
  -> Catalog 投影
       只有 verified 数据进入正式目录
```

四类知识关系：

- `has_property`：概念具有的性质；
- `derives_from`：在条件变化下由已有知识点产生的衍生关系；
- `composed_of`：多个知识点组合并产生新的性质或概念；
- `special_case`：特殊现象、公理或不能直接形式化归纳的特例。

在现有 Catalog 投影中：

- `derives_from` 和 `composed_of` 映射为学习前置关系；
- `special_case` 映射为支持关系；
- `has_property` 不直接转换成学习顺序。

带文字层的教材 PDF 使用 `pypdf`。扫描 PDF 或图片可以使用可选的本地 RapidOCR；低置信度页面仍需要人工核对。真实教材、竞赛数据和用户私有数据只放在本地 `data/`，不提交仓库。

知识点候选默认是 `unverified`，关系候选默认是 `candidate`。未经人工核对的数据不能作为正式课程事实或建议的唯一依据。

## 数据、可靠性与权限

项目区分三类可靠性依据：

1. **数据可靠性**：质量问题、作答事件处置状态、统计排除数量；
2. **来源可靠性**：材料和页码的 `verified` / `unverified` 状态，以及联系来源；
3. **结论可靠性**：证据是否充分、冲突或不足，以及结论限制。

处理原则：

- 用户上传数据默认视为 `unverified`，系统负责结构校验和质量检查，不替用户判断真实性；
- 未核对材料可以展示，但不能作为建议的唯一依据；
- 团队推断关系必须明确标注为 `team_inference`；
- 证据不足时输出“证据不足”或“未核对”，不伪造确定性结论；
- 已发布数据集和目录版本不原地修改，编辑应产生新的版本以保持历史结果可复核。

权限边界包括：

- 学生只能访问自己的分析、作答证据和质量问题；
- 学生不能访问班级分析、其他学生数据或参考答案；
- 教师工具范围绑定当前会话的数据集；
- 模型不能通过参数自行修改 `run_id`、数据集、Catalog 版本或学生范围。

## 学习观察事件

当前已提供外部学习闭环的后端最小骨架：

```text
学生提交观察事件
  -> learning_observations
  -> 教师或后续服务查询
```

支持的事件包括 `answer_submitted`、`hint_requested`、`activity_completed` 和 `teacher_review`。观察事件不会直接修改正式成绩、`AnswerEvent` 或既有分析运行。

以下部分仍待后续实现：活动/建议发布绑定、前端逐步作答、事件驱动的新分析运行、B 的观察事件汇总，以及根据新观察生成下一轮策略。

## 团队分工与文档

| 角色 | 主要职责 | 工作指南 |
| --- | --- | --- |
| A | 共享契约、课程目录、后端主流程、整合和统稿 | [A 工作指南](docs/team/03_A工作指南.md) |
| B | 数据导入、质量检查、个体与班级统计 | [B 工作指南](docs/team/04_B工作指南.md) |
| C | 教学建议、材料匹配、引用验证 | [C 工作指南](docs/team/05_C工作指南.md) |
| D | 教师端、学生端、成果包和演示 | [D 工作指南](docs/team/06_D工作指南.md) |

推荐阅读顺序：

1. [统一实施规范](docs/team/11_统一实施规范.md)
2. [架构与数据设计](docs/team/01_架构与数据设计.md)
3. [接口协议](docs/team/08_接口协议.md)
4. [知识点图谱设计](docs/team/18_知识点图谱与自动入库设计.md)
5. [受控智能体与双闭环实施方案](docs/team/19_受控智能体与双闭环实施方案.md)
6. [Git 协作指南](docs/team/07_Git协作指南.md)

项目框架气泡图见：[17_assets/framework_bubble.svg](docs/team/17_assets/framework_bubble.svg)。

## 当前未完成项

以下内容不应被当前 README 描述为已完成：

- `frontend/` 前端和浏览器端工具轨迹展示；
- IntentRouter 和学生/班级 Target 请求模型；当前 `/chat` 任务仍固定为解释学生失分；
- AgentTaskState 的缺失信息、策略选择和下一动作更新逻辑；
- A 知识查询工具接入 AgentLoop；
- C 材料搜索、策略选择和候选建议工具接入 AgentLoop；
- 最终回答的证据引用、泄答、策略限制和角色表达校验，以及失败时的规则回退；
- 对话范围/历史持久化与 Agent 请求、工具轨迹、模型用量的持久化；
- 总请求时限、安全重试、Token/调用预算和统一模型上下文裁剪；
- 工具参数的数组元素、最大数量和嵌套字段严格校验；
- 观察事件触发新分析运行；
- 前端实际显示工具轨迹；
- 真实授权模型的实机多轮验收；
- 用户上传数据集的完整 HTTP 导入、目录版本编辑和成果包导出链路。

## 协作与数据安全

- 不提交 `.env`、密钥、数据库、原始竞赛资料或真实用户数据；
- `data/` 和 `outputs/` 仅用于本地数据与结果，默认不入库；
- 使用短期功能分支和 PR，合并前运行全量测试；
- 共享契约和数据库迁移由 A 统一协调；
- 多个 Alembic 历史合并时保留已发布 revision，真实出现双 head 后使用 merge revision，不伪造缺失的父迁移。

技术版本以 `uv.lock` 为准，当前基线为 Python 3.12、FastAPI、Pydantic v2、SQLAlchemy 2.x、Alembic、SQLite、pandas、httpx 和 pytest。
