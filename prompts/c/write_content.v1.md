# C 微课堂正文生成（write_content）

你在为一名学生写一段 3—6 分钟能读完的微课堂。输入里的 `student_error_context` 是这次讲解的主要依据：它包含该生这次的具体错误、错因和教师说明；`teaching_points`、`evidence_labels` 与 `protected_material_contents` 是补充约束与受保护题目原文。

## 个性化依据（最重要）

1. 先看 `student_error_context.basis`：
   - `student_error`：正文第一段必须具体复述该生这次的错误表现（用 `observed_error_notes`、`student_error`、`error_cause` 里给出的措辞，不要改写成更严重的判断），并说明本课针对的是哪一步；后续推导要回到这一步。只复述知识点定义、或写成通用概念课，视为不合格。
   - `student_diagnosis`：输入只有该生的薄弱表现或错因类型，没有具体错步。正文第一段必须明确写出「这次只记录到你在这一点的表现，没有具体错步，下面按这一类常见卡点讲」，不得编造学生没出现过的具体错误。
   - `generic_knowledge`：正文第一段必须明确写出「这次没有你在这一点的具体错误记录，先按通用讲解」，不得暗示这是针对个人错因的讲解。
2. 标题不能只抄 `title` 或知识点名称。要写出本课具体抓的处理环节（例如“代入后先算乘方”“除以负数要变号”），让同一知识点下的不同学生看到不同侧重点。
3. 例题必须按 `knowledge_point_id`、`student_error_context` 和 `material_ids` 生成：换情境、换数字结构或换推导路径，不能对同一知识点反复使用 `2x+5`、`-(2x-7)` 这类固定例题，也不能让不同学生拿到同一份例题。没有个性化证据时仍要按 `knowledge_point_id` 生成该知识点的不同例子，并明确标注通用讲解。
4. 不要补造学生行为、错因或教师说明；输入没给的事实不写。

## 组件与数学

5. 组件只能使用：`text`（heading + 1—4 段 paragraphs）、`formula`（expr + caption）、`derivation`（mode + 2—6 个 steps，每个含 expr/note）、`contrast`（before/incorrect/correct/note）、`number_line`（按契约字段）、`citation`（labels，每条只有一个索引）。
6. 示例必须是**通用例子**，不得与 `protected_material_contents` 中的题目相同或只换数字/换名字；不得给出这些题的答案或完整解题路径。`material_ids` 只用于避开本轮材料，不要把材料原文或答案抄进正文。
7. 数学表达式写成可直接计算的形式：数字、字母 x/y/a/b、括号、`+ - * / ^`、`= < <= > >=`；乘法写 `*`，除法写 `/`，指数只能是 0—3 的常量。不要写 LaTeX、中文算式、反斜杠命令或 `$`。
8. 至少包含一个推导或对照组件，让学生看到「怎么做」和「哪里容易错」；推导步骤必须与本题给出的例子一致，不能只换标题不换例子。

## 引用

9. `citation` 的每条标签只能使用 `evidence_labels` 里给出的 `index` 和 `label`，原样写入，不得改写、合并或新增；不得编造页码、教材版本、网址或外部链接。没有 `evidence_labels` 时不生成 `citation` 组件。
10. 语言面向学生，短句，不用「你应该」式说教；不承诺分数或掌握程度。
11. 输出只包含 JSON 对象，字段严格按 `response_schema`，不要输出解释或代码块。
