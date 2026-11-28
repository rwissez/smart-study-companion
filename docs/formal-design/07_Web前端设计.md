# 湛知｜07 Web 前端技术设计

- 版本 v1.1-draft（06 契约核对；待最终技术审核）｜2026-11-27｜面向 DSH。
- 前置：以 [审阅索引](审阅索引.md) 的 00—06 同分支版本、决策 v0.54 及增量审阅稿为准；HTTP schema_version 1.1。尚未冻结的路由、DTO 和权限形态见 [待确认问题](待确认问题.md)。
- 视觉执行依据：[07_UI视觉设计规范.md](07_UI视觉设计规范.md)、[visual-tokens.css](visual-tokens.css)、[桌面参考原型](reference/index.html)、assets。参考原型所有内容均为合成，不连正式业务。具体色值已确认；布局尺寸为实施建议。唯一正式品牌资产为用户选定的 [湛知 Logo 原图](assets/湛知书页新芽徽标.png)，原尺寸比例引用。

## 1. 目标、职责和已确认范围

教师四个一级页面（班级总览、课程体系、数据管理、AI 对话），学生三个一级页面（个人画像、学习计划、AI 对话），学生画像、班级详情、对比、作答、微课堂、编辑、提醒等为原页子路由/弹层。身份入口重做视觉，保持本地模拟身份。桌面优先（1920×1080、1440×900、1366×768），不要求移动专项。教师墨绿、学生青蓝、公共纯白底，使用唯一 CSS token，精简冗余说明。不开发班级教学策略、自动选案例算法、手写识别、任意模型代码渲染、注册系统、独立日志/数据库管理页面。

Web 是正式业务服务的授权消费者，不是 A/B/C/Agent 算法或数据源。只做展示、交互、传输、表单初步检查、角色安全投影的消费、固定组件绘制和真实任务状态映射。所有有效得分、掌握判断、推荐排序、证据、权限、发布、事件、导出均以服务器为准。不得以 mock 补正式结果，不能把图形示例当真实结果。不存在需要新增的前端模型任务；模型提示词和输出按 02—05，微课堂模型只输出 04 LessonDraft，Agent 按 05 受控工具执行。

## 2. 源码核对和改造边界

之前 07 讨论时实际通过 GitHub 连接器读取 D 集成分支 `codex/d-integration-review-20261123` 的目录、下列页面和组件代码摘录。当前文档按这些实际静态结果及同步修订版 01 §1 复用；未在本轮再次检查最新远端提交，08 须锁定 SHA 并复查。不得把摘录核查称完整逐行审计。main 的 Agent/知识/观察服务与 D 的 HTTP、前端、模型配置须整合，不能用 D 单次模型文本替代 main 工具循环。S=frontend/src。

|现有路径（S 下）|静态核对事实及文件 blob SHA 前缀|处理|
|---|---|---|
|app/router.tsx|56d2e820；已有路由与 StatePanel|复用路由守卫，增加下级学习视图及语义定位|
|app/providers.tsx、scope.tsx、session.tsx|目录中存在|复用 provider/会话范围，核对缓存隔离及失效|
|pages/IdentityPage.tsx|目录存在|保留会话创建，重做白底品牌入口|
|pages/teacher/ClassesPage.tsx|bd7d5e73；filter/page/indicator、ClassComparison、IndicatorRadar、名册、EvidencePanel|复用读取/分页/对比入口；改真实 B 版本、证据及比较服务，不复算|
|pages/teacher/StudentProfilePage.tsx|fe0169bd；概览/记录/建议、生成、RecommendationEditor|四页签、进展、完整 C 大编辑器，移除旧未经授权发布|
|pages/teacher/CurriculumPage.tsx|c8a940bd；旧主题/指标网络|扩 A 细粒度图谱、正式关系/出处及候选审核|
|pages/teacher/DataPage.tsx|48217e1c；四页签、六文件、运行、publishedOnly|统一上传、真调度、质量、正式导出（删除草稿导出切换）|
|pages/student/StudentHomePage.tsx|7f896ba5；旧学习表现/发现/下一步|雷达、知识状态、建议、真实进展|
|pages/student/StudentPlanPage.tsx|a74b8f3a；建议读取、审核中|活动任务组、原版本、微课堂、在线尝试、反馈闭环|
|pages/teacher/ChatPage.tsx、pages/student/StudentChatPage.tsx|目录存在|共用 ChatPanel，角色投影及上下文|
|components/chat/ChatPanel.tsx|3b452162；本地 messages、busy、includeAnalysis|服务器历史、真实工具进度、确认/受助/操作回执；删除随意附全量分析开关|
|components/chat/ModelSettingsForm.tsx|目录存在|复用教师本地模型配置；密钥不回显|
|components/evidence/EvidencePanel.tsx|目录存在，页面已引用|统一 EvidenceQuery/Detail，抽屉及嵌套引用，角色与阶段保护|
|components/learning/{ClassComparison,ClassSummary,FindingCard,IndicatorRadar,MetricValue,RecommendationEditor,RecommendationList,StudentRoster}.tsx|目录存在|优先复用组件与测试，按正式新 DTO、视觉 token、异常语义升级|
|components/feedback、components/layout、layouts、styles|目录存在|复用真实可用状态/布局，集中 CSS token；删除旧蓝色页面背景|
|api/service.ts、api/types.ts|01 §1 已核对声明|服务层唯一 API 入口；生成共享 OpenAPI TS，不复制业务 DTO|

建议新增 S/components/learning/{TaskGroupView,ActivityRow,AttemptEditor,GradeFeedback,LessonRenderer,LessonComponents,ProgressView}.tsx；S/components/chat/{JobTimeline,ConfirmationCard,AssistedConfirmationCard,OperationResults}.tsx；S/components/feedback/{AsyncBoundary,FreshnessNotice}.tsx；S/lib/{queryKeys,errors,mathView,draftStore}.ts；S/styles/visual-tokens.css（本包复制），S/assets/brand/（本包资产复制）。存在同职责文件则就地改，不另建重复实现。路由/组件/文件名是实施建议，不要求重命名所有旧路径。

## 3. 应用框架、路由和会话

以下为目标前端语义路由；旧 router 实际路径优先保留，必要时别名重定向，不影响 API。URL 参数仅定位，后端验证。

|目标位置|访问条件|页面/子状态|
|---|---|---|
|/identity|未登录或主动切换|选择教师/学生，学生选择已登记身份|
|/teacher/classes?classId=&nodeId=&view=|teacher|默认当前画像、测验切换、detail/compare 子视图|
|/teacher/students/:studentId?tab=&nodeId=|teacher 授权|overview/progress/records/plan；来源班级、返回及筛选保留|
|/teacher/curriculum?topicId=&kpId=&tab=|teacher|graph/reviews，按授权范围读取|
|/teacher/data?tab=&jobId=|teacher/bootstrap|dataset/jobs/quality/exports|
|/teacher/chat?conversationId=|teacher|当前授权对象、会话、设置弹窗|
|/student/profile|本人|当前/历史节点与进展|
|/student/plan|本人|当前正式计划、活动历史|
|/student/plan/:planVersion/activities/:activityId|本人、正式/允许进行中历史|题目、学习材料、微课堂判别渲染|
|/student/chat?conversationId=|本人|当前课程/活动上下文|

AppShell：固定64px页头、208px白侧栏、Logo、角色文字、教师提醒/身份菜单、范围选择（显示数据集名称，不泄露内部编号）。学生本人身份固定。教师入口 bootstrap 可上传/选数据集，不展示未授权学生。通过 POST /demo-sessions 创建新角色/数据集会话；当前 scope 由服务端返回。学生预登录名册选择使用已有受限本地身份入口，若无合法名册接口不得扩大 GET /students 的匿名权限，交06/08确认受控 bootstrap。教师切换 dataset/family 必须确认未保存操作、清空 scope/缓存/旧 pending 表单，重建会话；学生切换身份亦同。旧 token 不跨会话复用。身份选择、实际会话、数据来源是独立状态。DEMO_MODE 只是本地身份；VITE_DATA_SOURCE=api|demo 严格分离。正式默认 api，失败不转 demo。合成参考 HTML 永不进入正式 frontend 路由。

## 4. API 适配、缓存、任务和错误（01 §9—10）

所有路径相对于 /api/v1。通过唯一 API service 注入 Bearer token，解析 schema_version/request_id/data/warnings 或 error；下载二进制单独处理。服务层 TypeScript 从最新版 OpenAPI 生成或核对既有生成机制；禁 any 掩盖结构冲突。使用项目现有请求/查询库，若未具备取消与缓存，薄适配 AbortController + scope generation，不新增重型状态框架。QueryKey 示例：`[mode,sessionEpoch,family,dataset,kind,subject,node,run,version,activity,visibility,page,filtersHash]`；登录 token 不进 key/log，sessionEpoch 随身份变化递增。禁止跨角色复用缓存。精确历史版不可无声替换 latest。正式指针查询与不可变版本缓存区分，发布后失效相关 student/class/plan/alerts/quality，保留其他学生稳定缓存。学生答案敏感响应不进入持久浏览器缓存；授权活动变化后清除旧题和证据数据。

```ts
// 伪代码：具体类型由01/OpenAPI生成
const epoch = scope.epoch;
const res = await api.request(endpoint, {signal, key: stableMutationKey});
if (epoch !== scope.currentEpoch || signal.aborted) return; // 丢弃旧响应
if (res.error) return renderDomainError(res.error.code, res.error.message);
commitAuthorizedView(res.data); renderWarnings(res.warnings);
```

读请求可有限重试临时网络错误，写请求稳定 Idempotency-Key（随机 UUID，按一次明确业务意图生成，保存到响应确认；重试同 payload/key，新意图新 key）。提交后超时先查询 attempt/job/原确认；不得新建第二次作答。冲突409保留草稿并重读版本，不自动重放旧写入。切换对象时取消旧读，后台写 job 不随页面关闭取消；显示可恢复 job 链接。401 返回身份页并清敏感缓存；403/404 安全不可用，不暴露他人信息；409 RESULT_NOT_READY 显示前置任务；REVISION_CONFLICT/DEPENDENCY_STALE 显示重读/重新预览；422 表单问题定位字段；413 文件超限；503 模型未配置/超时提供设置或真实重试/已有成果；未知错误显示 request_id（可复制），不显示堆栈。禁止把失败转为0分/已完成。

任务状态 queued/running/succeeded/failed/cancelled 与审核、发布分列。GET /jobs/{job_id}，增量 GET /jobs/{job_id}/events?after_sequence=；按 sequence 去重追加，先读现状态再增量，succeeded 也核查 publication_status。普通任务轮询约2秒，60秒给“可离开页面，任务仍在运行”，不是超时失败；Agent 1秒、无变更后2秒、最长5秒（05）。后台页不可见降低频率，返回立刻查询；终态停止轮询，取消按钮仅真实允许任务调用 POST /jobs/{id}/cancel，已发布不可撤销。进度只用已保存阶段或服务器提供总量/已完成量；未知总量不画百分比。06 已规定后台重试、租约与恢复；教师仅在 JobDetail.retry.can_retry 为真时显示一次重试，POST /jobs/{job_id}/retry 带稳定 Idempotency-Key；保留原失败状态，打开关联 job。公共任务链列表仍待确认。

## 5. 教师班级总览及比较（UI-02）

布局：标题行班级和节点切换、查看对比，下面人数/有效观测/需关注三项数字、左雷达右共性及分布、名册表格（学生编号、正式表现摘要、证据状态、关注、进入画像）。数据：GET /datasets/{id}/classes、/students?class_id=&page=；当前 GET /datasets/{id}/classes/{classId}/profile，历史 GET /runs/{runId}/classes/{classId}?node_id=&analysis_version=。名册 roster_count 含无记录学生，不能拿有效人数作全班人数。common_findings、performance_distribution、attention_students 按 B 返回展示；不擅自按得分设关注阈值。搜索优先服务端可用参数；若只具备分页且无搜索 API，不把当前页过滤声称全班搜索：加载授权班级全部分页后本地按编号精确/子串筛选并分页，或明确仅本页；优先前者（比赛规模小），请求取消。排序仅展示，不更改分析结论。

雷达组件只使用相同指标/口径、同范围正式 metric；按 catalog/指标稳定顺序（不是得分排序），默认 valid_score_ratio，比例0—1乘100展示，刻度0/25/50/75/100；无记录 null 不画0点，断线/点列和数据表保留缺失；单点或两维退回带明确标签的条/点展示；全空显示暂无有效记录。禁止闭合跨缺失维度的多边形。鼠标/键盘聚焦点显示指标、原始分子分母、排除、限制；点击证据。分布按服务端 count/roster_count，分母0显示无数据，状态文字配色而非仅颜色。班级比较调用 GET /runs/{run_id}/classes/compare，以左/右班、节点和可选指标/知识点为筛选，使用 B 返回的 comparable 和 items.delta；不可比不画差值，只并列合法原指标和原因。禁前端自行重算 class mean，禁生成班级教学策略。点击学生进入画像携带定位，返回保留班级、节点、分页、滚动。

## 6. 教师学生画像与学习进展（UI-03/04）

当前 GET /datasets/{id}/students/{studentId}/profile，历史 GET /runs/{runId}/students/{id}?node_id=&analysis_version=；当前计划 GET /datasets/{id}/students/{id}/plan，历史按 plan_version；进展 GET .../progress；学习事件 GET .../learning-events。概览雷达与 B summary/finding/mastery/evidence 状态、错误诊断并列；诊断展开具体支持记录、已知限制、相关知识与方案入口。用原有指标展示及知识级视图分区，不把得分率当掌握率。知识粒度可按指标展开，未映射显示不足。作答记录优先复用 B 正式证据/记录索引；通过 evidence resolver 分页查看，不把某个 finding 的有限样本冒充全部记录。完整学生作答走 GET /runs/{run_id}/students/{student_id}/answer-records，使用 node_id、可选 analysis_version、page/page_size 以及服务端 total；详细证据仍走统一 resolver。每条记录显示状态、节点、得分/满分（null 为—）、依据、评分点（授权），原始解答多行原样呈现。

进展使用 ProgressComparison.classification 与 comparable；同指标、同口径、可比范围才画线；不同节点或规则导致不可比用并列两点与文字限制。时间按服务端真实发生/分析日期排序，绝不按版本号推断时间；缺点断线。分类映射 improved=明显改善、partially_improved=有所改善、unchanged_or_declined=无明显改善或退步、insufficient=证据不足。不得从正负差值替代 B 分类。每一趋势点链接实际版本和证据，学生端安全投影。学习方案摘要展示 C 的目标、活动、理由、进度、调整/介入提醒；若旧方案已按新版学情检查仍适用，显示原依赖和“已复查，安排不变”，不得改写历史依赖。freshness current/compatible_history/stale；stale 说明是否允许继续，以服务端为准。

## 7. 教师学习方案编辑器（UI-05）

独立大弹窗，顶部学生/当前计划，左组与活动列表，右题库/材料/选中微课堂预览；下方操作固定。初始读当前正式 PlanView、历史 revisions；基于 base_plan_version 生成本地 draftChanges，仅字段 goal,reason,activities,adjustment_conditions,student_intro,lessons 白名单（01 §5.2、04 §9），不接受 state/score/student/evidence 等改写。已完成/进行中活动锁定；未开始可增删/换序；排序控件键盘上下移动和可选拖放（不以 order 当永久 ID）；教师不得重排突破必要依赖。题库 GET /datasets/{id}/materials/search?catalog_version=&indicator_id=&kp_id=&question_type=&difficulty=&q=&page=；选择显示题干、来源、难度/未知、关联与教师合法预览；加入时使用正式材料 ID，不让 UI 造题。学习卡/教材合法来源同规则。三提示编辑仅文本输入并预览，提交由 C 硬门/泄答审核；微课堂使用六类组件字段编辑，新增内容可走 Agent，不允许任意 HTML/JS，既有 LessonVersion 不覆盖。任务组依赖与历史由服务端判定，UI 可提前提示但不当发布授权。

直接编辑流程：
1. 点击“检查并发布”，POST P/recommendation-revisions/preview（P=/runs/{run_id}/students/{student_id}），发送 node_id/base_plan_version/reason/changes。保存 preview_id,draft_hash,warning_digest,warnings,expires_at；预览前只显示草稿。
2. 硬门失败定位字段，不给发布按钮；教学警告可明确确认，展示具体差异、风险、保留活动；用户编辑任何内容立即作废本地 preview，重新检查。
3. 点击“确认发布” POST P/recommendation-revisions/{preview_id}/publish `{warning_digest}`，同一稳定 key；服务端通过后状态 human_confirmed/published 才刷新正式计划。409 保留草稿，重读、手工重建差异，禁止旧预览再次提交。
4. Agent 修改走聊天候选与完整审核，不调用 teacher_direct；旧兼容修订 POST P/recommendation-revisions 只请求审核，不能冒充直发。教师查看审核失败从 /plan-reviews 定位；approve/edit 产生新教师预览，仍需确认。reject 记录原因。
5. 恢复：历史版本仅可查看；选择恢复目标和原因，当前未开始活动差异经 C 恢复；正式编辑恢复走预览/确认，其他授权恢复按 POST /datasets/{id}/students/{studentId}/plan/undo→job。已完成/进行中、评分及历史不撤销。编辑器关闭有未保存变更提醒；若已经提交任务，关闭不是取消。

## 8. 课程体系和教师提醒（UI-06/07）

GET /catalog、GET /datasets/{id}/knowledge?knowledge_version= 只读正式。左主题/指标选择和搜索、中心关系图、右选中定义/课程目标/实际前置与材料/评分点关联；正式关联按 A annotation，不把课程归属冒充逻辑边。关系方向严格 source_ids→target_id，多源 composed_of 使用共享汇合节点表达所有来源，prerequisite 单独箭头及图例，不把 derives_from 等转换为前置。图布局确定性：节点按 topic/indicator/稳定ID分组，默认选中主题局部图；选中节点可扩一层邻居，搜索定位、缩放0.5—2、平移、重置视图、关系类型复选。只根据正式结构做绘图坐标，不改变边。节点过多局部展开/分页，显示当前范围，不宣称完整全图；键盘可用列表替代。可复用现有 SVG 或轻量现有图谱库，禁止新增独立图数据库；布局可用前端确定性算法，无需模型。边线有标签/箭头/可点击证据；来源页通过受控 GET /sources/{file_id}/pages/{pdf_page}，MIME 建议 image/png（06最终核对），Blob URL 用毕 revoke，不直接打开任意本地文件或完整未授权 PDF。

异常审核页 GET /datasets/{id}/knowledge/reviews?status=&object_type=&page=；显示候选和正式差异、原因、核验来源、状态；仅教师 approve/reject/edit，POST .../reviews/{review_id} `{base_candidate_version,decision,changes?,reason}`。编辑按 object_type 白名单，硬门失败保留候选，不显示已发布；job 发布后刷新知识与受影响页面。报告 GET /datasets/{id}/knowledge/quality?knowledge_version=（02 §12）。顶部提醒 GET /datasets/{id}/alerts，open 计数来自服务端总量/可核查分页，不以当前页条数冒充总数；三类 knowledge_review/plan_review/teacher_attention，点击导航至对应对象/审核。已读不等于 resolved；不在前端自行关闭介入案例。教师使用 01 §9.3 interventions 详情及 resolve，填写原因并传详情的 expected_last_cutoff；409 刷新后重审，已读不改变结案状态。

## 9. 数据管理（UI-08）

四页签 dataset/jobs/quality/exports。数据集显示名称、真实范围、成员/材料、快照信息；教师选择现有 family 或新建独立，增量补充必须明确目标及 expected_parent_dataset_id。上传支持拖放/文件选择（CSV/XLSX/PDF），不接受任意路径，客户端检查扩展和基础大小（最终限制服务端/06），逐个文件名/大小/删除；POST /imports multipart files+metadata JSON {intent,new family/append/auto_detect 对应枚举,dataset_family_id?,expected_parent_dataset_id?}；不自行分类判定。响应 ImportReceipt 按 confirmed/needs_confirmation、incomplete/ready/blocked、file status 展示。需要确认时列表 file_id/sheet_name/role，教师选正确角色与目标 family，POST /imports/{id}/confirm；缺输入明确缺项，可继续补传，重复返回原记录；GET /imports/{id} 恢复状态。上传的 PDF 原文仅按授权本地保存，不自行发模型。旧六 CSV 路径只兼容，不把缺正式 A 当完成。

处理进度：已导入→A→B→C 的实际 job 链、可发布与失败状态，上传完成且满足条件自动调度，不让教师按顺序手点运行。保留授权重试/补资料入口；job 阶段不预设固定百分比。质量报告标签“数据 / 知识体系 / 学情 / 学习方案 / 系统运行”，数据 GET /datasets/{id}/quality，A 见 §8，C GET /runs/{id}/quality/recommendations?node_id=&date_from=&date_to=&class_id=&page=；B 使用 GET /runs/{run_id}/quality/analysis，按节点及可选班级或分析版本读取 BQualityReport；Agent 公共报告仍待确认。指标分母和限制来自报告；可定位问题记录/教师处理页面；不生成额外日志页面。质量不合格和证据不足分开，模型降级明确标记。

成果导出：GET /runs/{id}/exports 预览正式 manifest/缺项，按正式覆盖与范围显示三任务、字段字典、证据和案例。案例选择真实教师指定对象。POST /runs/{id}/exports `{format:'zip',node_ids?,case_student_ids?}` →202 {export_id,snapshot_id,job_id,status}，符合06复用条件时200/201 {export_id,download_url,manifest}，成功后 GET /exports/{id}/download（授权 Blob，使用安全 Content-Disposition 文件名，下载后 revoke）；旧 GET /runs/{id}/exports/download 只兼容正式服务。禁止 published_only=false。数据变更后本次快照仍保持原版本，后续可重新导出，不能混用。导出不能调用模型；证据不足合法，必要结果缺失拒绝并定位。无数据/待审核时按钮禁用并给真实原因；不用预设清单假装就绪。比赛 PDF/视频/PPT 另行制作。

## 10. 学生画像、任务组与学习材料（UI-09/10）

个人画像只读本人正式 profile、progress、plan；雷达复用 §5，配色自动青蓝。显示优势、需要加强、暂需更多证据的内容（不作班级排名或心理标签），学习建议用 B/C 学生安全投影，提供“继续学习”“询问湛知”入口。学生可切换有权历史测验，不带他人或班级数据；图表限制可见，真正诊断内容不删。

计划页显示 C TaskGroup 的 goal、student_intro、estimated_minutes、expected_outcome、activities，任务组依赖、活动状态、实际必需项完成数由后端正式状态/required_activity_ids 决定。UI 只计算展示计数（已完成必需活动数/总必需数），不触发组完成；分组按 C order/dependencies，不按题型强制重排；当前可执行项主按钮，已完成可查看授权历史，未开始但依赖未满足锁定，取消显示已调整；不把 cancelled 算完成。review_status 不是学习状态。若新正式计划发布，正在作答旧活动保留旧 plan_version/attempt 继续，未开始旧活动拒绝新尝试并引导到新计划；不突然清空进行中输入。暂无正式计划显示真实处理状态，无学生手动生成/修改按钮。材料活动使用正式 MaterialView、受控页预览与服务端确认完成；普通 study_material 走01 §9.3 通用活动 start 与 complete，提交同一活动及计划版本；微课堂走专用完成接口，题目走尝试/作答流程。历史 unknown 不显示独立完成。

## 11. 微课堂固定渲染器（UI-11）

04 §6.3 为唯一 schema。GET P/activities/{activityId}/micro-lesson?plan_version=（教师草稿可 preview_id），返回 activity_id/content_id/content_version/schema_version/title/goal/components/safe_sources/fallback_text。无 material_id/attempt_id，不生成题分。`LessonRenderer` 按 type 判别，运行时用生成 schema 校验版本、必填字段、长度、id唯一、引用范围；服务器已经审核仍要防浏览器不兼容。禁止 eval/new Function、dangerouslySetInnerHTML、模型 SVG/HTML/CSS、外链图片、任意 JS、任意 LaTeX 宏或脚本。React 文本转义；数学 AST/受限中间表达式解析沿04（指数0..3、常数/深度/运算上限），安全生成 LaTeX 或等价纯文本，KaTeX 等库若已有优先使用，trust=false、strict/error处理、禁止宏、网络/链接。前端不替代后端数学正确性及泄答审查。P0 六类全部必须实现：

|type/04字段|渲染和交互|边界|
|---|---|---|
|text heading?,paragraphs[]|标题可省，段落自然阅读|1–4段≤300；不解析 HTML|
|formula expr,caption?|居中公式、可复制原表达|expr≤160，受限语法；错误安全回退|
|derivation mode,steps[{expr,note}]|等式/不等式按行，备注侧边/下方；可逐步展开（默认全部）|2–6步，不让前端推导新解答|
|contrast before,incorrect,correct,note|白底双栏、错误/正确标签、细线分隔|显示后端核验的对照；不让颜色承担唯一语义|
|number_line min,max,tick,sets[{label,intervals}],operation,result_index?|固定 SVG 绘制数轴、多行解集、端点/箭头、交集结果|04 区间与 Rat 精确约束；最多41刻度、1–3组；文本替代|
|citation labels[{label,source_index?或evidence_index?}]|引用按钮、EvidencePanel/授权材料|零基索引合法、仅一个、学生不可见引用不显示|

数轴绘图伪代码：使用有理数解析（分母1..1000，分子绝对值≤10000），用整数交叉相乘校验端点/顺序，绘图位置 `x=L+(value-min)/(max-min)*(R-L)`，只在绘图阶段转浮点；无界端点用轴端箭头，开圆/实圆标记开闭；空集显示“空集”，单点实圆；intersection 恰三组且 result_index=2，按后端审核结果绘制并校验结构，前端如做精确交集仅为安全验证，不生成新正式结论。未知/非法组件：新版显示不可读且不允许完成；已审核历史版本若存在安全核验 fallback_text，可阅读，是否允许完成由后端真实兼容规则决定（无明确许可不提交）。不能把解析失败当活动完成；质量问题经06认可入口记录，前端本地提示。

P1：分配律/解集交集结构图，P2：有限滑块/暂停动画，仅按04另行注册 schema、审核、fallback、测试后启用；本期不依赖增强，不能把原型里的演示参数当当前正式 schema。模型只输出已注册组件字段，不能直接生成前端代码。完成按钮 POST P/activities/{id}/micro-lesson/complete `{plan_version,content_version}`，同 key 防重，成功 state completed 后刷新计划；记录接触，不推断掌握。教师预览不写学生事件。学生看当前安全来源，禁止教师原始分析或受保护题解。

## 12. 在线作答、提示、反馈（UI-12/13）

统一 AttemptEditor 依据 question_type=choice/fill_blank/solution，GET /materials/{materialId}?catalog_version=&activity_id=&plan_version=；POST /plans/{plan_version}/activities/{activity_id}/attempts `{}` 创建/恢复；GET /attempts/{id} 读当前。题干、选项从安全 MaterialView 取；原文 Markdown/数学安全显示，未核验公式回退原文本，禁止猜公式。选择题 radio（实际选项，不假设只有四个），填空单行输入可包含等式、分数等；解答题多行 textarea 240px以上、最终答案独立字段（允许只有步骤或答案符合01），普通键盘优先，数学符号工具栏按当前 selectionStart/selectionEnd 插入 − × ÷ ² √ ≤ ≥ ≠ () /，保持焦点和撤销；输入不自动重写原文，中文 IME composition 时不触发提交。使用 `white-space:pre-wrap` 显示历史解答过程。无需复杂富文本/手写识别。输入法、复制粘贴、多行长式、Tab 键、光标插入必测。原始 `final_answer/solution_steps` 不被数学预览覆盖；客户端只判空、长度（按最终契约）和题型必要项，不做评分。草稿 sessionStorage 或内存按 sessionEpoch+family+student+plan+activity+attempt 键隔离（本地比赛模拟数据，禁跨身份、禁敏感长久存储；关闭/提交/切换清理，刷新同一会话恢复，服务端提交状态优先）；不将密钥、标准答案写入草稿。若正式政策禁止浏览器持久化，则仅内存并明确限制。

提交 POST /attempts/{id}/submit `{final_answer?,solution_steps?,unanswered:false}`；主动空白交卷单独确认 `{unanswered:true}`，不把空白当零分。提交期间禁重复，网络超时查询原 attempt，再同键重试；成功后锁原文本，pending/needs_review 显示“已提交，待评分/待复核”，不给虚假0。真实 graded+validated 才显示 score/max_score、允许反馈及按实际分点评分；未正式授权解答不可出现，不能因 submitted 就释放答案。重练必须新活动/attempt 由 C安排。收到更晚 grade 修订以服务端最新版本显示并说明变更，原答卷不重写。

三级提示逐级请求 POST /attempts/{id}/hints `{level,allow_assisted?}`；UI 不预载未释放 hints（即使旧 DTO 含全数组也必须后端裁剪）；实际成功返回才显示对应 level/hint/event_id，重放不重复计数；失败显示重试/未确认，不能自行标记 independent。独立补证/迁移检查先弹确认：“获取帮助后，本次作答不再作为独立完成的证据。继续获取帮助？”拒绝不发送 allow_assisted，接受同次合法请求由服务端先记录受助选择再释放；Agent 通过 §13 assisted-confirm。两渠道共享真实尝试状态和累计保护，返回时刷新当前活动、提示和 assistance_status。历史 unknown 显示“帮助情况未知”；assisted 不扣卷面分。无论普通聊天还是题目引导，学生不应收到后端完整评分细则、标准答案、未来题解或未释放提示。

逐题反馈与组反馈分开：逐题 GET /attempts/{id} 得 grade/feedback/group_status/update_job_id；组完成由后端依据 required_activity_ids 和可信事件判断，UI 显示已完成任务、独立/受助/未知（仅真实统计）及后续任务状态。B 正式分析后才出现进展分类，C replan 审核后才显示新方案/原方案仍适用。组等待期间可继续后端许可活动；不要强制弹“学习进步”或伪造图。取消/失败保留提交与旧计划。活动内“询问湛知”传真实 activity_id/plan_version，返回仍在原版本输入/阅读位置。

## 13. Agent 对话与证据（UI-14/15/18）

师生共用会话栏/正文/输入区；新会话、历史 GET /conversations、GET /conversations/{id}/messages 分页，按服务端所有权。页面联动携带 target/run/node/activity_id/plan_version 等定位，后端重验；目标标签可明确切换，教师读对象不意味着写对象。新请求 POST /chat `{conversation_id?,message,target?,run_id?,node_id?,activity_id?,plan_version?}`，不同时传 messages、不传 system、role、authorization_ref 或任意工具 args。输入最多8000字符（兼容上限）；IME Enter 不提交，Enter/Shift+Enter 行为明确，可用发送键；发送后禁重复，保留草稿直到确认接收。200 ChatResponse 或202回执进入 job；显示实际 StepView.label/status/message，默认折叠完成步骤，失败保留可见，禁止伪造思考流/逐字模型流。真实结果 segments kind fact/explanation/suggestion/operation/limitation，按 Markdown 安全子集（段落、列表、表格、数学、引用；不解析 raw HTML 或未授权链接），未知安全回退 text。引用通过统一 resolver，不直接模型生成 URL。部分成功保留成功/失败目标。

needs_confirmation 互斥分支：clarification 只是问题，普通回复，不生授权；confirmation 显示学生固定名单、每人修改前后及警告、到期，接受/拒绝 POST /conversations/{id}/confirm `{confirmation_id,decision}`；assisted_confirmation 显示当前活动独立性后果，POST .../assisted-confirm 相同结构。按钮双击同键；超时/版本冲突重读候选，不重放；普通聊天“好的”不是确认接口。明确单人操作直接后台执行但显示真实进度与结果；批量/含糊必须确认。Agent C job 审核/发布状态与 job 分开，只有 publication_status=published 且服务端实际正式指针可读才显示已调整；已完成不靠取消回滚，历史恢复走 C。学生不显示班级策略、数据质量、修改计划或模型设置。

模型配置仅教师本地能力：GET/PUT /model-settings，字段 provider?,url,model_id,api_key?，未填 key 保留；清空仅 `clear_api_key=true`，不与新 key 冲突；密码输入掩码，提交后清空前端变量，不写日志/缓存。POST /model-settings/test `{}` 不发赛事数据；连接成功只说明接口可连，不声称教学模型验证通过。模型未配置仍可访问正式画像和计划；错误显示具体可处理原因。

证据抽屉 `POST /evidence/resolve {ref,context?}`，按01 §6 EvidenceDetail.detail_type: record/metric/curriculum/derived/learning 渲染。保留 ref 的精确版本，不自动取最新；context 仅定位。record：题干、实际作答、状态、分数（若授权）、评分点、质量、历史修订；metric：指标、单位、分子分母、实际证据数量/排除及限制、分页引用；curriculum：定义、关系、出处、核验短摘；derived：诊断/建议/进展与引用；learning：实际事件及关联。visibility=redacted 不显示“已隐藏答案”的具体原文；只显示服务端允许投影。嵌套引用用抽屉历史栈及返回，不开无限弹窗，重复 ref+version 去重。请求取消、范围切换清除抽屉与缓存；不存在/过期/不可访问提示，禁止自动替换引用或展示整份 JSON。教材页使用受控 Blob，不能从模型地址 fetch。历史知识定位保留原版本和来源。

## 14. 实施状态与公共组件测试

页面加载/空/错误/部分就绪/历史/过期/审核中/权限不足均由公共状态组件表达。表格稳定 key、分页、筛选、实际总量、空白区别；必需字段缺失明确报契约错误。表单键盘可用、焦点可见；弹窗关闭恢复焦点，禁用原因可读。敏感页面不在浏览器控制台打印响应。静态原型中样例数字不复制到真实服务。CSS data-role 仅展示，不是授权；安全必须后端检查。采用现有 npm/锁文件、测试框架、ReactTS/Vite，先查依赖版本，不擅自更新；现有项目若无 Playwright 可由08选择最小浏览器测试方案。前端只允许合成测试 fixture 和明示 demo 模式，正式 api 模式测试不转 fixture。

推荐实施顺序：统一 API+会话/错误+token/app shell→角色入口→B 图表与画像/证据→A 图谱/审核→数据上传/真实任务/质量/导出→C 计划编辑/历史→学生在线作答/微课堂/反馈→Agent会话/确认/进度/设置→端到端回归与视觉验收。依赖真实后端未实现时可先独立测试组件，不用假 API 完成业务验收；08按代码依赖最终排列。

## 15. 验收用例（待 DSH 实施及实际运行）

|ID|前置/输入/操作|预期（必须核查）|
|---|---|---|
|UI-T01|本地身份入口，选教师→学生→教师|会话重建、Logo不变、角色文字与主题正确，旧缓存清空|
|T02|不同 family 同 student_id，切数据集|不混画像、计划、证据与任务|
|T03|api 后端断开或 VITE_DATA_SOURCE 非法|明确错误，无 demo 回退|
|T04|班级含2名无记录学生|名册完整、分母按真实名册、无记录非0|
|T05|雷达含 null、0、100%|0点/缺失分开，无跨缺失闭合，分子分母正确|
|T06|两班节点/评分口径不同|B可比限制，不强画差值|
|T07|教师从分页名册进画像返回|班级/节点/筛选/滚动恢复|
|T08|B两版可比/不可比|真实分类、不可比无伪趋势|
|T09|新B后旧C仍适用|显示真实原依赖及复查，不改旧版|
|T10|教师查看具体失分及来源|精确版本 EvidenceDetail；点击返回不丢筛选|
|T11|编辑进行中/完成活动|锁定；服务端拒绝恶意写 state/score|
|T12|教师选正式题并修改顺序|只允许合法待开始项，依赖硬门|
|T13|教师编辑提示存在泄答|硬门拒绝，不给发布按钮|
|T14|教师直接编辑有教学警告|预览/警告确认后 human_confirmed 发布，无强制模型审核|
|T15|预览后草稿再改或基线变|旧预览失效，409 保留草稿并重建|
|T16|Agent教师修改|完整 C 审核，不调用 teacher_direct|
|T17|恢复旧计划|新修订，完成/进行中历史不变|
|T18|A图含多源和前置|关系方向正确、图例区分、候选隔离|
|T19|A审核失败及来源不可用|真实限制和错误，不能假发布|
|T20|提醒定位|进入正确审核/画像，已读不关闭介入|
|T21|混合 XLSX/CSV/PDF 上传，分类不明|确认表、缺项、重复导入正确，自动调度|
|T22|job 事件重复/乱序/页面退出再进|按 sequence 去重，重读状态，真实进度|
|T23|C失败、B成功|保留 B/旧适用C，正确提示失败|
|T24|缺必需正式结果导出|阻止；合法证据不足可导出|
|T25|导出中并发新方案|下载旧冻结快照，不混新版本|
|T26|学生个人画像|雷达+建议+进展，无他人/班级排名|
|T27|新计划发布时旧题进行中|原 attempt 仍可合法完成，未开始旧题阻止|
|T28|微课堂六类合成合法 fixture|数学/来源/区间显示正确，不计分|
|T29|组件未知、恶意HTML/公式/超限|安全拒绝/核验 fallback，无脚本、无错误完成|
|T30|数轴开闭/无界/空集/单点/交集|端点与文本准确，使用固定组件|
|T31|选择/填空/解答三题型|实际字段与普通键盘输入，过程保留|
|T32|IME、光标插入≤、粘贴多行、刷新|输入不丢、不自动提交、草稿同会话恢复|
|T33|提交双击/超时重试|同 attempt/idempotency，原作答唯一|
|T34|提交后评分 pending/失败/修订|不显示0分，最新可靠反馈，历史不覆盖|
|T35|独立题点击提示后拒绝/接受|拒绝无帮助；接受后台记录，真实受助状态|
|T36|提示请求失败/重复/晚到|未知/真实计数，无前端申报独立|
|T37|学生另开聊天请求答案|统一保护，网络响应、缓存均不含受保护字段|
|T38|任务组完成触发 B/C|真实组状态，先反馈后更新，不伪造进步|
|T39|聊天复合任务或部分失败|实际 StepView 与各目标结果，非模型思考|
|T40|教师模糊/批量、明确单人|正确确认/直发审核分支，固定名单|
|T41|确认双击、过期、版本变化|同键返回原效果，冲突不重放|
|T42|学生受助确认/普通澄清|三种确认互斥，不能伪造写授权|
|T43|聊天历史切换对象/身份|不串上下文、无旧证据泄露|
|T44|教师模型配置留空/清除/测试失败|正确保留/清除，密钥无回显与日志|
|T45|证据五类与跨范围请求|授权安全投影、精确历史版本，越权拒绝|
|T46|1920/1440/1366桌面全页|无主页面横溢、文字裁切、弹窗溢出|
|T47|computedStyle 检查师生背景及按钮、hover|纯白、精确 HEX token、仅局部强调色|
|T48|Tab/焦点/ESC/长内容/错误提示|键盘可用、焦点恢复、错误可理解|
|T49|逐页设计截图人工走查|无大量彩色卡片/渐变/冗余文案，图表清楚|
|T50|赛方典型案例按实际结果展示|A→B→C证据链和真实学习闭环，非模拟教学成效证明|

所有 T 为目标测试，不代表已运行。原型只作视觉和本地交互验证；正式项目须启动真实前后端、使用合成及经授权赛方数据、运行契约/集成/视觉验收。09吸收并扩充。

## 16. 与06及前置契约的冻结门（不可省略）

本轮已对照06 v1.0 和 v0.54，并将已确认契约收录在当前审阅稿。原十项比较如下；待确认项按 [问题清单](待确认问题.md) 处理。

|接口缺口/冲突|必须落地的最小语义|处理|
|---|---|---|
|班级/学生对比 HTTP|02类授权对象、共同范围及版本，返回03 §12.4 可比 items/限制|班级比较由01 §9.3 正式路由；学生横向比较的独立 HTTP 仍待确认。不得由 UI 计算可比性|
|完整作答记录分页|授权 student/node/version，实际 AnswerDetail 或安全引用列表及 total|01 §9.3 作答分页已规定；详情走统一 resolver|
|B 与 Agent 质量报告 HTTP|按 family/dataset/run/class/时间及实际统计、分页限制|B 按01读取 BQualityReport；Agent 对外报告仍待确认|
|普通学习材料完成|绑定正式 activity/plan/source，幂等 completion，无评分|已对齐01 §9.3：POST /plans/{plan_version}/activities/{activity_id}/start 与 /complete；后者仅供普通材料。微课堂走其独立完成接口|
|任务重试/恢复与工作流列表|当前 scope 的 job 链、真实失败原因、仅合法可重试动作|一次公开重试已对齐01，保留原失败并跟踪子任务；公开列表仍待确认|
|适用性检查公开字段|原A/B依赖、最新复查版本/结论/时间/限制|已对齐01 §2、§9.3 PlanApplicabilityView；区分 origin_analysis_version 和 current_analysis_version|
|正式导出预览/案例选择/202回执|正式覆盖缺项、版本快照、授权案例、job/download 引用|已对齐 06 §9.4 请求/202/下载；预检响应的公共判别及查询字段仍待确认|
|教师介入记录/解决|授权、原因、证据及实际 open/resolved|01 §9.3 已明确详情与处理；不新增监控|
|教师编辑器恢复及微课堂安全来源索引|04/01精确 preview 恢复语义、citation 投影索引一致|同步契约；不猜字段、不泄答|
|学生身份入口、源页 MIME 与草稿限制|受控 localhost bootstrap；授权单页；服务端策略|复用旧身份选择及06，禁止匿名枚举正式学生|

前置局部更新建议：00 §9/12 标湛知品牌、学生雷达、桌面优先、固定组件、视觉验收；01 §9 与06冻结补齐上表；04 §6 仍仅P0六组件，P1/P2可选，禁止因视觉原型扩大模型输出；05 §11.3 与本文件一致无需改业务；08/09纳入原型对照及截图验收。原附录 UI-01—30 未随交付包提供，不能逐条补录。现有07设计和视觉规则是本轮审阅的文件依据；06 契约已核对，未冻结项见问题清单。未修改项目源代码，未运行实际项目/模型/赛事闭环。

### 00—07 修订记录

同步 §4、§9、§16 的任务恢复、导出、活动与适用性。保留教师和学生导航、页面内容、组件及案例选择规则。最新版代码静态状态见 [实际代码核查](实际代码核查.md)；旧 07 的路径表属于复用线索。未执行运行、浏览器或真实模型测试。

### 补充接口消费

教师编辑器执行 01 §9.3 preview → 确认 → publish，回传 preview_id 对应 warning_digest。遇到409刷新版本并重新预览；不能由旧 publish=true 绕过预览或当作确认。沿用 A/B/C 各自报告形态，教师介入保留关联证据和处理历史，重试只在后端允许时发起。此处均为目标契约。
