# A-Agent-8 知识归属与审核设计

状态：A-Agent-8 第一部分已实现并通过回归验收。设计日期：2026-11-24；验收日期：2026-11-25。

## 2026-11-25 实现验收

全量测试：238 passed，1 个既有 Starlette/httpx 弃用警告。已验证 manifest 绑定导入、知识点/归属/关系人工审核、新版本发布、父快照不可变、hash 变化拒绝、指针失败保留历史、角色受限查询和无 run 聊天证据轨迹。审核使用受信任本地 Python service，不提供模型写工具或 HTTP 审核入口。

补齐原差距：跨资料导入支持 parent_version，严格重复聚合来源，名称/别名/定义歧义生成 MergeCandidate；教师 accept/reject 保留审计并安全重写引用，关系折叠冲突拒绝合并。教材片段通过 register_textbook_fragment 注册稳定 ID，来源页证据对照 manifest 校验。resolver 仅重读被引用页，并使用 manifest 指定的文字层/离线 OCR 提取方式。旧文件通过 import_legacy_json 显式迁入指定 dataset/catalog，状态重置为待审核，原文件不变。

本轮范围保持不变：模型自动关系生成、图形化审核界面和授权模型实机质量评测不在本轮交付范围；审核/发布为受信任本地服务。OCR 需要已有可选本地依赖。单一 current pointer 指向 draft 时学生暂不可查询，发布后恢复。

### 本地调用入口

- `ingest_textbook_snapshot(..., parent_version=...)`：跨资料增量导入、来源绑定、去重和新草稿保存。
- `deduplicate_knowledge(kb)` / `propose_merge_candidates(kb)`：纯函数返回新对象或合并候选，不修改父快照。
- `register_textbook_fragment(source_id, file_page, evidence, target_id)`：创建仍待审核的片段归属候选；`source_id` 必须属于绑定 manifest。
- `KnowledgeStore.import_legacy_json(dataset_id, catalog_version, legacy_path, kb_version=..., source_files=...)`：显式迁入本地旧 JSON，不自动猜测 Catalog。
- `KnowledgeStore.review_candidates(..., catalog=...)`：知识点、归属、关系和合并候选人工审核；模型无此入口。
- `list_knowledge_candidates(..., category="merge")`：教师只读分页查看合并候选。

## 意图与原因

当前知识模块已有 `KnowledgePoint`、四类 `Relation`、`Annotation`、PDF 页码定位、规则抽取、关系候选验证及 Catalog 投影；但归属候选（知识点→Topic/Indicator，材料/题目/学习卡/评分点→知识点）、重复知识处理、教师审核版本和 Agent 知识查询尚未形成闭环。目标是在现有知识模型上增量扩展，把导入结果保留为候选，经过程序校验和教师核对后发布新知识库版本，再提供只读查询工具。

## 方案取舍

1. **本地 JSON 不可变版本快照（选定）**：延续现有 `data/knowledge/` 文件存储；每次导入、审核或发布都写入新版本目录，父版本保留。证据只存短摘，校验时从绑定的本地源文件重新读取相应页。无数据库迁移，符合当前数据留本地约定；并发编辑/查询索引能力较轻。
2. SQLite 版本与审核表：查询和审计更直接，但需要迁移、事务设计及文件/数据库双重生命周期；当前阶段暂不采用。

## 目标

- 继续复用现有 `KnowledgePoint`、`Relation`、`Annotation` 和 `Catalog`，不建立第二套知识图谱。
- 所有自动归属、关系和去重歧义均是 `candidate` / `unverified`；只有教师审核可生成新 verified 版本。
- 每条候选可复核：稳定来源 ID、目标 ID、关键词、短证据、来源文件/页码、生成原因、proposer、核对状态和审核记录。
- 程序校验来源/目标存在、Catalog/知识库版本、证据原文子串、重复、关系结构与环路；失败返回明确错误，不静默丢弃。
- 提供知识定位、详情、关系和教师候选只读工具，按角色及核对状态过滤，并接入 A-Agent-7 对应任务。
- 保留旧文件快照读取兼容；不改变 B/C 统计、Catalog 契约或现有 HTTP 接口。

## 非目标与反目标

- 不自动把模型/规则产物发布为 verified，不提供模型确认或发布工具。
- 不把教材整页文本复制到候选、审计记录、prompt 或模型上下文；只存受限短摘。
- 不用向量数据库；第一版使用规范化名称、别名、关键词和章节/状态过滤。
- 不改 `analysis/`、`recommendation/` 统计/建议函数签名，不改既有冻结 contracts/API 响应。
- 不为没有稳定来源 ID 的评分点伪造编号。

## 数据模型增量

在 `knowledge/models.py` 扩展现有类型，新增字段保持可选以兼容旧 JSON：

- `KnowledgePoint`：可选 `topic_ids`，保留已有 `indicator_ids`；补充创建/合并所需来源位置列表，避免重复知识点丢失新资料出处；可选规范化定义摘要/指纹供去重使用。
- `Annotation`：兼容旧 `material_id`，新增可选 `candidate_id`、`source_type`、`source_id`、`target_type`、`target_ids`、`evidence`、`source_file`、`file_page`、`printed_page`、`reason`、`reviewed_by`、`reviewed_at`。目标类型覆盖 `topic`、`indicator`、`knowledge_point`；材料来源类型覆盖教材片段、题目、学习卡和评分点。
- `KnowledgeBase`：新增可选 `parent_version`、`status`（draft/verified）、创建时间、来源摘要和变更摘要；旧文件缺字段时仍可加载为 draft。
- 审核逐项记录通过新 KnowledgeBase 版本中的 `reviewed_by/reviewed_at` 和状态变化保留，不原地改写已有快照。
- 文件布局：`data/knowledge/<dataset-or-library>/<kb_version>/knowledge_base.json`、来源 manifest 和必要的本地源文件定位信息。新文件写入原子临时文件后替换目标版本目录；版本 ID 不可复用。默认不把整页 OCR/抽取文本保存进正式快照；页文本仅用于本次导入/候选校验的内存对象，若本地保留源 PDF，则按文件相对路径与页码重读。

## 导入、归属与去重流程

```text
本地教材/教学资料 + 当前 Catalog/知识库版本
  -> 复用 B split_textbook 和现有 Extractor 建立 PageRecord/Locator
  -> 现有 terms 规则抽取 KnowledgePoint 候选
  -> 按规范化名称、别名、短定义指纹、章节/页码与现有版本比较
  -> 确认重复：合并来源 locator 到 draft；同名异义/定义近似歧义：生成待审核 merge candidate
  -> 生成 KnowledgePoint -> Topic/Indicator 归属候选
  -> 生成教材片段/题目/学习卡/评分点 -> KnowledgePoint 候选
  -> 生成四类 Relation 候选
  -> 校验全部来源、目标、Catalog 编号、证据页和关系结构
  -> 写入新的 draft 快照，所有自动项保持 candidate/unverified
```

去重规则按确定性从高到低执行：规范化名称完全相同、别名完全相同、定义短摘规范化指纹相同、同章节与同页重复。自动确认的重复只在 draft 版本聚合来源，不覆盖父版本；同名异义、相似定义、来源冲突一律生成教师可见候选，不自动合并。定义相似度第一版只做保守精确/规范化比较，不引入外部模型或黑盒阈值。

### 知识点到 Topic/Indicator

规则候选应组合指标描述/名称、知识点名称/别名/短定义、章节小节、支撑关键词、源页短证据及已 verified 邻接归属。每个 target ID 单独产生候选和理由；允许一对多但不合并证据。第一版规则为可解释关键词重合和章节上下文排序，不足/平分时保留多个候选或无候选，由教师选择，不自动定案。

### 材料/片段到知识点

使用现有 Catalog `Material`、本地教材分段和评分点文本生成候选；题目、学习卡、教材片段可多对多关联知识点。评分点只有当源数据具备稳定 `rubric_point_id` 时才生成候选；否则报告明确的 `SOURCE_ID_MISSING`/待标识统计，不造 ID。方法能力（移项、去分母、合并同类项、不等号变向、解检验、规范解答流程）允许作为 `kind=method` 知识点候选，但通过去重和章节粒度规则，避免无限拆分。

### 关系候选

沿用 `has_property`、`derives_from`、`composed_of`、`special_case` 和现有 `validate_candidates`。每个非人工关系必须引用指定页的真实短原文子串和 file page；自动状态永远为 candidate。补充检查候选的 relation ID 唯一、关系端点存在、端点不相同、关系形状/重复/环路；已 rejected 的候选不进入普通 verified 查询。

## 审核与版本发布

- 教师在指定 draft version 上按候选 ID 核对；动作限定为 accept/reject/edit-target，不允许 Agent 工具执行这些动作。
- 所有候选决议生成新版本，`parent_version` 指向原 draft；旧 draft/verified 快照保持不变。
- 只有通过程序校验、被教师 accept 的点/归属/关系变为 verified；reject 项记为 rejected 且在普通查询隐藏；修改 target/evidence 形成新候选并重新完整校验。
- 发布 verified version 之前验证所有引用 ID、Catalog 版本关联、evidence source page 和页码状态。未核对材料/页码必须保留 unverified，不作为学生确定性诊断或建议唯一依据。
- 文件仓储 API 提供 `load_version`、`save_draft(parent, kb)`、`review_candidates(base_version, decisions, reviewer)`、`publish_verified(draft_version)`，都采用版本不可变语义；写入失败不改变当前版本指针。

## 只读查询与 Agent 接入

在 `knowledge/` 内提供版本仓储和查询服务，Agent adapters 只调用这些服务，不直接访问文件系统。

- `locate_knowledge(keyword, kb_version?, verified_only?)`：复用 `knowledge.index.locate`；返回 KP、match 类型、Topic/Indicator、章节/小节、source file、file/printed page、KP 状态、locator 状态、KB version。学生默认 verified only；教师可按请求看候选，但必须标状态。
- `get_knowledge_detail(kp_id, kb_version?)`：返回短定义、别名、已核对归属、来源位置、关联材料/评分点候选与状态；不返回整页文本。
- `get_knowledge_relations(kp_id, types?, direction?, depth?, verified_only?)`：支持 neighbors 与上游/下游遍历；学生强制 verified，教师候选显式标记；rejected 永不返回。
- `list_knowledge_candidates(...)`：教师专用，只读，按候选类别、状态、版本分页；含证据短摘、生成方式、来源页和拒绝原因，不提供任何确认/发布动作。
- 所有工具的 `kb_version` 由 server context 绑定或必须属于会话当前数据集允许版本；不能由模型切换到另一数据集/版本。Evidence refs 使用 `kind=curriculum`、candidate/kp ID 和 `catalog_version`，不得包含源页全文。
- 更新 A-Agent-7：`knowledge_lookup` 开放 `locate_knowledge/get_knowledge_detail`；`prerequisite_query` 开放 `get_knowledge_relations`；`relation_review` 仅教师可调用候选读取工具。每个任务只有所需工具、角色裁剪和验证测试均通过后才从 pending 改为 ready。

## 代码边界

- `knowledge/models.py`：只扩展现有知识模型和可选字段。
- 新增 `knowledge/store.py`：本地 JSON 版本快照读写/原子保存。
- 新增 `knowledge/targets.py`：来源/目标索引、去重、归属候选生成与验证；不导入 API、Agent、DB。
- 新增 `knowledge/review.py`：纯知识层审核决议构造新版本，不直接持久化或授权。
- 扩展 `knowledge/relations.py`：统一关系 ID/证据/状态校验，保留现有签名兼容或提供薄 wrapper。
- 扩展 `knowledge/index.py`：详情、归属和方向/深度查询，旧函数保持可用。
- `agent/tools.py`：只负责带权限/版本绑定的只读适配，不把读写能力交给模型。
- `api/routes/chat.py`：仅做 KB context 选择和注册已就绪工具，不改变既有 chat envelope 字段；如审核 API 不在本轮暴露，只提供 service API 并明确人工审核调用边界。
- 无 DB migration；不改变 `contracts/`、B/C 函数签名、现有统计、正式 Catalog 版本。

## 测试与完成标准

- 旧 KnowledgeBase/Annotation JSON 可读取；新快照写入后旧版本字节内容不变。
- 自动抽取、名称/别名/定义去重、同名异义 candidate、来源合并及稳定 ID 规则。
- Topic/Indicator 多目标归属候选分别带依据；不存在的 topic/indicator/source/kp 均报明确校验错误。
- 每条 evidence 在指定页真实存在；长证据被长度限制；默认保存中不出现整页文本。
- 题目、学习卡、片段候选可生成；无稳定 rubric point ID 时不生成假候选且报告原因。
- 关系候选通过现有四类结构/重复/环/证据校验，自动项不能 verified。
- 教师审核 accept/reject/edit-target 生成新的 draft/verified version，旧快照不变；普通查询过滤 rejected。
- 学生查询只返回 verified 点/关系/归属；教师 candidate 查询标明状态；详情不返回全文；非法 KB version/跨数据集 version 拒绝。
- Agent tools 角色集、KB version 绑定、evidence refs 与 A-Agent-7 ready/pending 状态均有测试。
- 执行 knowledge 专项、Agent/API 专项、迁移检查和 `uv run pytest -q` 全量测试。

## 本轮实现范围建议

先实现本地版本仓储、目标/关系候选校验、教师审核生成新快照、四类只读工具和任务注册；自动归属规则先提供保守、可解释候选。模型自动关系生成和图形化审核界面留后续；当前仅生成候选，不外发资料，不直接发布。
