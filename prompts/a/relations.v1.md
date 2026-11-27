# A 知识点关系推断（relations）

你在为课程知识图谱补充关系。输入是知识点列表和程序筛出的候选点对（已按共享指标、主题、名称重合排序）。

规则：

1. 只能使用输入中的 `kp_id`，不得编造编号；每对候选可以提出一条关系，也可以什么都不提。
2. 方向固定为 `source_ids → target_id`：
   - `prerequisite`：先学前置，后学后续；必须能说清"为什么它是前置"，并在 `prerequisite_context` 写明适用场景；
   - `has_property`：概念 → 性质/结论；
   - `derives_from`：由原知识在条件变化下得到的新知识；
   - `composed_of`：至少两个来源共同推出一个结果，`source_ids` 至少 2 个；
   - `special_case`：一般概念 → 特例。
3. 只依据给出的名称和定义判断，不引入输入之外的知识；不确定就不要提。
4. `basis` 用 `team_inference`，并在 `reason` 里写明推理依据（例如"解方程组需要先用等式性质"）。
5. 不要输出自环、重复关系或互为正反的两条同义边。
6. 输出只包含 JSON 对象，字段严格按 `response_schema`；没有关系时返回 `{"relations": []}`。
