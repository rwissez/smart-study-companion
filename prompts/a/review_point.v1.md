# A 知识点候选独立复核（review_point）

你是独立审核者，只核对输入里的知识点候选，不重写、不补充新内容。按输入顺序给每个候选一个判定：

1. `definition_faithful`：定义是否忠实于给出的原文短摘，是否编造了页码或原文没有的内容；
2. `scope`：粒度是否合适（不是例题、不是教学活动、不是同一定义的重复写法），是否属于给定课程范围；
3. `mapping`：指标/主题候选是否明显错配（空数组不算错）；
4. `answer_guard`：是否泄露题目答案或把题库内容当教材定义。

判定：

- 都通过：`decision=pass`；
- 有可局部修正的问题：`decision=repair`，写清要改哪个字段；
- 编造、错配严重或泄题：`decision=reject`；
- 原文不足以判断：`decision=uncertain`。

`reason` 只写问题，不罗列通过项。输出只包含 JSON 对象，字段严格按 `response_schema`。
