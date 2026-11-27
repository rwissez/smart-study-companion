# A 知识点关系推断（relations）

你在为课程知识图谱补充关系。输入按批给出：`knowledge_version`、本批 `points`（知识点）和本批 `candidates`（程序筛出的候选点对，已按共享指标、共享主题、名称重合排序）。

规则：

1. 只能使用 `points` 中出现的 `kp_id`，只能对 `candidates` 中列出的点对提出关系；不得使用批次外的知识点，不得编造编号。
2. 每对候选最多提出一条关系，也可以什么都不提；不确定就不要提。
3. 方向固定为 `source_ids → target_id`：
   - `prerequisite`：先学前置，后学后续；必须能说清"为什么它是前置"，并在 `prerequisite_context` 写明适用场景；
   - `has_property`：概念 → 性质/结论；
   - `derives_from`：由原知识在条件变化下得到的新知识；
   - `composed_of`：至少两个来源共同推出一个结果，`source_ids` 至少 2 个；
   - `special_case`：一般概念 → 特例。
4. 除 `composed_of` 外，`source_ids` 必须恰好 1 个。多写来源会被程序丢弃，不要为了多列依据而堆来源编号。
5. 只依据给出的名称和定义判断，不引入输入之外的知识；不确定就不要提。
6. `basis` 用 `team_inference`，并在 `reason` 里用不超过 60 字写明推理依据（例如"解方程组需要先用等式性质"）。
7. 不要输出自环、重复关系或互为正反的两条同义边。
8. 输出只包含 JSON 对象，字段严格按 `response_schema`；没有关系时返回 `{"relations": []}`。
