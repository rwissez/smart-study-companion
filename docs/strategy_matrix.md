# C 策略矩阵 v1（正式版，A 任务单 C-1 交付物）

> 生成日期：2026-11-24。配套实现：`src/study_companion/recommendation/strategy.py`（策略选择器）+ `service.py`（四类 builder + 校验）。
> 红线不变：C 不产生 B 未输出的判断；所有数字只转述分析结果；学生端禁直接给答案。

## 一、输入维度

| 维度 | 取值 | 现状 |
| --- | --- | --- |
| 证据状态 evidence_status | sufficient / insufficient / conflicting | ✅ 已接入（Finding） |
| 学习表现 performance | observed_strength / needs_practice / undetermined | ✅ 已接入（Finding） |
| 前置知识缺口 | 沿 catalog.links prerequisite 链 + 本批 findings 判断 | ✅ 已接入 |
| error_stage（错误阶段） | 不会开始 / 建模 / 概念理解 / 关键步骤 / 计算 / 检验 | ⏳ 等 B 的 StrategySignal，接入后用于细分 1a/1b 判定 |
| error_type（错误类型） | 概念错误 / 步骤错误 / 计算错误 / 缺少检验 / 证据不足 | ⏳ 同上 |
| rubric_focus（失分评分点） | 具体评分点 ID | ⏳ 同上；接入后建议理由可落到评分点 |

**字段对齐约定**：B 先定 `StrategySignal` 字段并提供 ≥3 个典型样例，C 的 `select_strategy` 按样例字段读取；共享字段统一由 A 修改契约，不各自定义。

## 二、策略矩阵（按输入组合）

| # | 输入组合 | 策略 | 允许 | 禁止 | 活动上限 | 提示层级上限 | 停止条件 | 回退策略 | 教师介入 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| S1 | 证据不足或冲突（insufficient/conflicting，或 undetermined） | 类型三：观察题组 | 覆盖不同变式的观察题；如实记录 limitation | 输出"会/不会"结论；安排扶练或迁移；强行归因 | 3 | 1 | 题组完成（证据齐备）或学生未参与 | 证据齐备后重新走本矩阵归类；未参与则如实呈现 | 学生未参与本轮观察时提示教师 |
| S2 | needs_practice + 无过程信号 | 1b：递减干预扶练 | 扶助卡（L1 方向层起步）+ 独立练习；教师可指定原错题 | 直接给答案；追加新课；强制走完提示阶梯 | 3 | 3（L1/L2/L3，接住即放手） | 独立完成即停（不问完所有层级） | 同类位置再错 → 转教师；连续两次未完成 → 转教师 | 连续两次未完成 |
| S3 | needs_practice + 有过程信号 | 1a：倾听式四步引导 + 巩固 | 复述确认→定位分岔→引导自撞→修正重走；巩固题 | 追加新课；直接上扶助阶梯（学生有思路）；给答案 | 2 | 2 | 巩固全对 → 进类型二迁移 | 同类位置再次出错 → 降级 S2 | 同类位置再次出错 |
| S4 | observed_strength + sufficient | 类型二：两级迁移 | 第一级变式确认；第二级思想辨识选择（学生选、学伴解释） | 刷题；知识讲授；同一思想每周超过一次 | 2 | 1 | 完成选择即收尾（选择记录为亮点） | 变式出错 → 回落第一级补一道 | 持续困惑则建议面谈 |
| S5 | 当前失分 + 前置知识也失分 | 前置优先（S2/S3 用于前置指标） | 只安排前置指标的补练；后继写入复评条件 | 后继指标立即安排练习；两份练习叠加 | 继承前置条目 | 继承前置条目 | 前置补练完成且复评通过 | 复评仍失分 → 前置条目再走本矩阵 | 复评两次仍失分 |
| S6 | 当前失分 + 前置正常 | 按证据/表现走 S2/S3/S4，理由注明"前置已查" | 同 S2/S3/S4 | 同 S2/S3/S4 | 同左 | 同左 | 同左 | 同左 | 同左 |

## 三、建议包级规则（归并 / 排序 / 负荷，均已实现）

1. **归并**：同指标同类型诊断合并为一条（finding_ids 全挂）；前置链失分归并（S5）。
2. **排序优先级**：S1 观察 > S2 扶练 > S3 纠错 > S4 迁移。
3. **负荷上限**：一次最多 `max_recommendations`（默认 **2**）个核心目标；超出暂缓，写进保留建议的调整条件。
4. **材料去重**：同一材料在本建议包内只出现一次；排除已用材料后无题可用 → 该知识点转暂缓，不硬凑重复题。
5. **暂缓话术**（教师语言）：说明"以不超过 N 个知识点为限（减负原则），指标 X 下周跟进"。

## 四、校验器阻断清单（validate_recommendations，全部硬规则）

| 检查 | 代码 | 状态 |
| --- | --- | --- |
| 运行身份不一致 | CONTEXT_MISMATCH | ✅ |
| 诊断引用越界 | FINDING_REF_INVALID | ✅ |
| 指标不在目录 | INDICATOR_REF_INVALID | ✅ |
| 材料不存在 / 不适配指标 | MATERIAL_REF_INVALID / MATERIAL_SCOPE_INVALID | ✅ |
| 未核对材料发布 | UNVERIFIED_MATERIAL_PUBLISHED | ✅ |
| 重复建议（同 finding 多条） | DUPLICATE_RECOMMENDATION | ✅ |
| 重复材料（跨建议） | DUPLICATE_MATERIAL | ✅ |
| 负荷超限（需传 config） | LOAD_LIMIT_EXCEEDED | ✅ |
| 目标表述重复（呈现层去重兜底） | DUPLICATE_GOAL | ✅ |
| 提示泄答 | HINT_LEAKS_ANSWER | ✅（置 needs_review，不得 validated 保存） |
| 空话禁令 | VAGUE_REASON | ✅ |
| 活动顺序 / 提示 / 观察点 / 调整条件缺失 | ACTIVITY_ORDER_INVALID / HINT_MISSING / OBSERVATION_MISSING / ADJUSTMENT_MISSING | ✅ |

## 五、select_strategy 工具输出结构（A 任务单 C-2）

`choose_strategy(analysis_findings, catalog, config) -> StrategyPlan`：

- `entries[].kind`：策略编号（type3 / 1b / 1a / type2，对应 S1–S4）
- `entries[].rationale`：选择理由（教师语言，含前置检查结论）
- `entries[].findings`：对应 Finding 及证据引用
- 活动上限 / 提示上限 / 停止条件 / 回退：由各 builder 按矩阵第二节硬编码 + adjustment_conditions 承载
- `deferred_indicators`：暂缓清单（负荷截断 + 前置链归并）
- `follow_up_indicators`：前置条目完成后需复评的后继指标

**待 B 的 StrategySignal 字段确定后**：StrategyEntry 增加 `signal` 字段承接 error_stage/error_type/rubric_focus，S2/S3 的细分判定（不会开始 → 更低起点扶助；计算错误 → 减提示层级；概念错误 → 补概念卡）在矩阵 v2 细化。

## 六、学习观察事件（与 A 任务单 B-3 对接）

- C 侧 schema：`ObservationEvent`（strategy.py，只追加，不修改原始成绩）
- 闭环规则：`next_action_rule(events)` → advance / retry_same / fall_back / escalate_teacher / pending
- 待 A 的 API 层落库后，B 的分析模块读取 `LearningObservation` 作为新证据生成新分析运行（A 任务单 B-3），回流本矩阵重新归类。
