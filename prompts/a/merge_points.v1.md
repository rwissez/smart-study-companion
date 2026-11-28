# A 知识点合并判断（merge_points）

你在判断左右两个知识点是不是**同一个知识点**。只依据给出的名称、定义和来源页，不补充外部知识。

判定：

- `same`：名称/别名指向同一概念，定义的条件与适用范围一致；只是表述详略不同可以算 same。
- `distinct`：名称相同但定义域、条件或适用范围不同；或本质是两个不同概念。
- `uncertain`：给出的信息不足以判断。

对每个点对输出 `left_id`、`right_id`、`decision`、`reason`、`condition_comparison`（比较两者的条件/范围，写清依据）。不确定就写 uncertain，不要为了合并而合并。

输出只包含 JSON 对象，字段严格按 `response_schema`。
