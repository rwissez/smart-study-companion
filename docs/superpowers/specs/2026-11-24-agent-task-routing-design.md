# A-Agent-7 任务路由设计（扩展版）

状态：已批准并完成 A-Agent-7 第一版实现。日期：2026-11-24。

## 意图与原因

`/chat` 目前把所有请求固定成“解释学生为什么失分”，并强制要求 `student_id`。教师问班级共性、知识点位置、材料或学习计划时，仍按单生失分处理。目标是先确定任务类型，再选择任务提示词、目标要求和可用工具，并为尚未接入的 A/C 工具预留位置，使 A-Agent-8～10 只需填充，不必重做路由。

## 目标

- 覆盖尽量广的教学场景：16 类任务（见下表），按角色划分，并含“超范围”拒绝类。
- 规则优先；规则无结果或多类冲突时，模型只做一次无工具的任务分类。
- 学生或班级目标由请求显式提供并经会话权限校验，路由器不能选择或猜测对象。
- 每类任务有独立提示词位置；工具未就绪的任务明确返回“暂不可用”，不用不相关工具硬答。
- 既有请求（`run_id/student_id/node_id/messages`）继续可用，视为学生目标。

## 非目标与反目标

- 不让模型决定目标、扩大范围，或在分类时调用工具。
- 本步不实现知识/C 工具、最终校验门、对话持久化（A-Agent-8/9 及后续）。
- 不为无法判断的请求编造任务；分类失败明确报错。
- 提示词不是安全边界，权限和校验仍由代码强制。

## 任务分类

| 任务类型 | 角色 | 目标 | 现可用工具 | 状态 |
|---|---|---|---|---|
| `student_loss_explanation` 失分解释 | 师/生 | 学生+节点 | 分析、作答证据 | 可用 |
| `evidence_lookup` 查看作答依据 | 师/生 | 学生+run+node | 分析引用、作答证据 | 待补独立证据发现步骤 |
| `data_quality` 数据质量说明 | 师/生 | 学生或数据集(师)+run | 质量问题 | 可用 |
| `student_progress` 学生整体进展概述 | 师/生 | 学生+节点+多次run | 分析 | 待跨运行比较能力 |
| `class_analysis` 班级共性分析 | 师 | 班级+节点 | 班级分析 | 可用 |
| `class_data_quality` 班级数据质量 | 师 | 班级+run | 质量问题 | 待班级过滤和混合问题脱敏 |
| `student_learning_advice` 学习建议 | 师/生 | 学生+节点 | 分析+C 工具 | 待 A-Agent-8 |
| `study_plan` 学习计划 | 师/生 | 学生+节点 | 分析+知识+C | 待 A-Agent-8 |
| `class_strategy` 班级教学策略 | 师 | 班级+节点 | 班级分析+C | 待 A-Agent-8 |
| `knowledge_lookup` 知识点与教材定位 | 师/生 | 无 | 知识工具 | 待 A-Agent-8 |
| `prerequisite_query` 前置知识查询 | 师/生 | 无 | 知识关系 | 待 A-Agent-8 |
| `material_search` 材料检索 | 师/生 | 无/学生 | C 材料检索 | 待 A-Agent-8 |
| `concept_tutoring` 概念讲解（不含答案） | 生 | 无 | 知识（可选） | 待 A-Agent-8 |
| `relation_review` 知识关系候选审阅 | 师 | 无 | 知识关系 | 待 A-Agent-8 |
| `observation_review` 学习观察回顾 | 师/生 | 学生 | 观察工具 | 待 A-Agent-10 |
| `out_of_scope` 超范围/系统说明 | 师/生 | 无 | 无 | 可用（礼貌拒绝/引导） |

学生角色不可使用班级类和教师专用任务（`class_*`、`relation_review`），路由后权限校验拦截。

## 设计

- **请求**：`ChatRequest` 新增可选 `target: {type: "student"|"class", id}` 和 `task_type`（显式指定时跳过路由）。`student_id` 保留并等价于学生目标；与 `target` 不一致返回 `VALIDATION_ERROR`。
- **任务注册表**：`agent/tasks.py` 定义 `TaskSpec`：`task_type`、允许角色、目标类型要求、是否需要 `node_id`、工具名子集、提示词文件、可用性（`ready`/`pending_tools`）。新增任务只改注册表。
- **路由模块**：`agent/router.py`，`route_task(messages, provider, role) -> RoutedTask(task_type, source)`，`source` 为 `explicit`/`rule`/`model`。规则按角色可用任务的关键词命中集合判定，仅命中一类则采用；0 类或多类进入模型分类。模型分类不带 `tools`，要求 JSON `{"task_type": 枚举}`，解析失败或不在角色允许集合内返回 `TASK_UNCLEAR`（422）。
- **权限校验**（路由后、构建工具前）：角色是否允许、目标类型是否匹配、学生目标是否本人、班级/学生在数据集内是否存在。`pending_tools` 任务返回 `TASK_NOT_AVAILABLE`（422），说明所需能力。
- **提示词位置**（本设计重点）：
  - 路由分类提示词：`prompts/router.md`（`prompt_version: router-v1`），含枚举、角色约束、仅输出 JSON、不得选对象。
  - 每类任务一个提示词：`prompts/tasks/<task_type>.md`，带 `prompt_version`，内容含任务目标、必须使用的证据、回答结构、禁止事项和证据不足时的表述。`ready` 任务提供初稿，`pending_tools` 任务创建占位文件（标明“待 A-Agent-8 补充”及所需工具），使 C/A 可独立编写提示词而不改路由代码。
  - 超范围拒绝文案：`prompts/tasks/out_of_scope.md`。
  - `PromptRegistry.compose` 扩展 `task_type` 参数：加载任务提示词，拼入现有角色风格；返回的版本串包含 `router`（如适用）+任务+角色版本。缺少任务提示词文件时启动检查报错（测试覆盖每个注册任务均有提示词文件）。
- **响应**：现有字段不变，新增 `task_type`、`route_source`。
- **测试**：每类任务规则命中样例；多类冲突转模型；模型分类越界/不可解析；显式 `task_type`；学生越权班级/教师专用任务；班级目标成功；`pending_tools` 返回暂不可用；提示词文件与注册表一致性；旧请求兼容。

## 方案取舍

规则优先 + 模型兜底分类（用户已选）：适应性好；代价是一次额外模型调用和失败处理。纯规则不适应模糊表达，完全模型判断对象歧义和权限风险高，均已排除。

## 留给实现者（均为我的决定，可调整）

关键词表具体内容；`TASK_UNCLEAR`、`TASK_NOT_AVAILABLE` 提示文案；各任务提示词初稿措辞；`data_quality` 与 `class_data_quality` 是否合并（暂分开以保留目标类型约束）。
