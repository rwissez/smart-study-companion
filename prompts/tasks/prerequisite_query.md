prompt_version: task-prerequisite-query-v1

先用 locate_knowledge 将用户提供的知识点名称或别名解析为知识点 ID，再使用 get_knowledge_relations 查询其前置关系。不要自行猜测或编造知识点 ID。仅将 verified 前置关系表述为正式先修依据；教师看到的候选需明确标识，学生查询只返回 verified 关系。