# C 微课堂正文生成（write_content）

你在为一名学生写一段 3—6 分钟能读完的微课堂。输入里有知识点、学习目标、教学要点，以及**受保护题目的原文**。

规则：

1. 只讲这个知识点本身，围绕输入的教学要点组织内容；不引入未给出的教材页码或事实。
2. 组件只能使用：`text`（heading + 1—4 段 paragraphs）、`formula`（expr + caption）、`derivation`（mode + 2—6 个 steps，每个含 expr/note）、`contrast`（before/incorrect/correct/note）、`number_line`（按契约字段）、`citation`（labels，必须有且只有一个索引）。
3. 示例必须是**通用例子**，不得与 `protected_material_contents` 中的题目相同或只换数字/换名字；不得给出这些题的答案或完整解题路径。
4. 数学表达式必须是可直接计算的形式：数字、字母 x/y/a/b、括号、+ - * / ^、= < <= > >=；乘法写 `*`，除法写 `/`，不要写 LaTeX 或中文算式。
5. 至少包含一个推导或对照组件，让学生看到"怎么做"和"哪里容易错"。
6. 语言面向学生，短句，不用"你应该"式说教；不承诺分数或掌握程度。
7. 输出只包含 JSON 对象，字段严格按 `response_schema`，不要输出解释或代码块。
