# 湛知 Web 已冻结的 HTTP 接口面（P1/P3 实测）

本文件是前端唯一可依赖的接口清单，字段以实际响应为准；未列出的接口不得由前端假设。
所有路径相对 `/api/v1`，除 `/health` 与本地 `/demo-sessions*` 外都要
`Authorization: Bearer <token>`。统一包络：

```json
{"schema_version":"1.1","request_id":"req-...","data":{},"warnings":[]}
```

错误包络：`{"schema_version":"1.1","request_id":"...","error":{"code","message","details"}}`。

## 身份与范围

| 方法 路径 | 请求 | 响应 data |
|---|---|---|
| GET /health | — | `{status, schema_version}` |
| POST /demo-sessions | `{role, dataset_id?, student_id?, class_ids?}` | `{session_token, expires_at, scope:{session_id,role,dataset_id,dataset_family_id,student_id,allowed_class_ids,bootstrap,capabilities}}` |
| GET /demo-sessions/roster?dataset_id= | — | `{dataset_id, classes:[{class_id, student_ids:[...]}]}`（仅 demo 模式） |
| GET /datasets | — | `{items:[DatasetSummary], total}` |
| GET /datasets/{dataset_id} | — | `DatasetSummary` |
| GET /datasets/{dataset_id}/classes | — | `{items:[{class_id, display_name, roster_count}], total}` |
| GET /datasets/{dataset_id}/students?class_id=&page=&page_size= | — | `{items:[{student_id,class_id,grade,observed_event_count,graded_event_count}],total,page,page_size}` |
| GET /datasets/{dataset_id}/quality | — | `{items:[QualityIssue],total,page,page_size}` |
| GET /datasets/{dataset_id}/runs | — | `{items:[{run_id,dataset_id,catalog_version,knowledge_version,rule_version,status,created_at,node_ids}],total}` |

## 导入

| 方法 路径 | 请求 | 响应 data |
|---|---|---|
| POST /imports | multipart：`files`（可多文件）+ `metadata`（JSON 文本 `{intent:new_family\|append_to_family\|auto_detect,...}`），建议带 `Idempotency-Key` | `ImportReceipt{import_id,dataset_family_id,dataset_id,classification_status,input_status,files:[{file_id,original_name,sha256,byte_size,format,role,sheet_name,status,reason}],missing_inputs,quality_summary,job_id,warnings}` |
| GET /imports/{import_id} | — | 同 `ImportReceipt` |
| POST /imports/{import_id}/confirm | `{file_mappings:[{file_id,sheet_name,role}], intent, dataset_family_id?, expected_parent_dataset_id?}` | 同 `ImportReceipt` |

## 任务

| 方法 路径 | 请求 | 响应 data |
|---|---|---|
| GET /jobs?dataset_id=&kind=&status=&page=&page_size= | — | `{items:[JobDetail],total,page,page_size}` |
| GET /jobs/{job_id} | — | `JobDetail{job_id,kind,status,stage,scope,created_at,updated_at,result_ref,error,warnings,steps:[{step_key,label,status,message,started_at,ended_at}],review_status,publication_status,plan_version,retry:{root_job_id,retry_of_job_id,retry_job_id,manual_retry_count,can_retry,retry_block_reason},result,cancel_requested}` |
| GET /jobs/{job_id}/events?after_sequence=&page_size= | — | `{items:[{sequence,stage,status,message,occurred_at}],next_sequence,has_more}` |
| POST /jobs/{job_id}/cancel | — | `JobDetail` |
| POST /jobs/{job_id}/retry | 空体 + `Idempotency-Key` | `{job_id,retry_of_job_id,root_job_id,status}`（202） |

## 画像、方案、进展

| 方法 路径 | 请求 | 响应 data |
|---|---|---|
| GET /datasets/{dataset_id}/students/{student_id}/profile?node_id=&analysis_version= | — | `{result:StudentResultView, freshness, limitations}` |
| GET /datasets/{dataset_id}/classes/{class_id}/profile?node_id=&analysis_version= | — | `{result:ClassResultView, freshness, limitations}` |
| GET /datasets/{dataset_id}/students/{student_id}/plan?node_id=&plan_version= | — | `{plan:RecommendationBundle, plan_version, freshness, limitations, analysis_version, applicability}` |
| GET /datasets/{dataset_id}/students/{student_id}/progress?page=&page_size= | — | `{items:[ProgressComparison],total,page,page_size}` |
| GET /datasets/{dataset_id}/students/{student_id}/learning-events?page=&page_size= | — | `{items:[LearningEventView],total,page,page_size}` |
| GET /datasets/{dataset_id}/alerts?page=&page_size= | — | `{items:[{alert_id,kind,status,summary,target_ref,student_id,evidence_refs,created_at}],total,...}` |
| GET /runs/{run_id}/classes/compare?node_id=&left_class_id=&right_class_id=&indicator_id= | — | `ClassComparisonView{class_ids,node_id,member_analysis_versions,comparable,items:[{indicator_id,left_value,right_value,delta,left_numerator,left_denominator,right_numerator,right_denominator,limitations}],limitations}` |
| GET /runs/{run_id}/students/{student_id}/answer-records?node_id=&page=&page_size= | — | `{items:[AnswerRecordListItem],total,page,page_size}` |
| GET /runs/{run_id}/quality/analysis?node_id=&class_id= | — | `{scope,node_id,generated_at,rule_version,counts,coverage,issues,limitations}` |

`StudentResultView` 关键字段：`student_id,class_id,node_id,analysis_version,run_id,dataset_id,catalog_version,metrics[],findings[],progress[],strategy_signals[],summary,limitations,freshness,scope_kind`。
`metrics[]`：`{metric_id,name,value,numerator,denominator,unit,scope:{student_id,class_id,node_id,indicator_id,topic_id,kp_id},evidence_refs[],excluded_event_count,limitations}`。
`findings[]`：`{finding_id,indicator_id,description,evidence_status,performance,evidence_refs[],limitations,rubric_focus,error_types,diagnostic_details}`。
`ClassResultView` 额外：`roster_count,observed_student_count,common_findings,performance_distribution,attention_students,member_analysis_versions`。

`RecommendationBundle` 关键字段：`run_id,dataset_id,catalog_version,student_id,node_id,recommendations[],validation_issues[],plan_version,analysis_version,publication_status,task_groups[],limitations`。
`recommendations[]`：`{recommendation_id,indicator_id,finding_ids,goal,reason,student_intro,activities[],adjustment_conditions[],review_status,generation_method,strategy_types,kp_ids,evidence_refs,review_info,group_id}`。
`activities[]`：`{order,material_id,purpose,hint_levels[],observation_points[],previously_attempted,activity_id,group_id,knowledge_ids,activity_kind,state,title,expected_outcome,estimate_minutes,dependencies,evidence_refs,exposure_mode,repeat_reason,content_ref,content_version,source_label}`。
`task_groups[]`：`{group_id,goal,strategy_types[],activity_ids[],order,primary_strategy,auxiliary_strategies,goal_target_key,expected_outcome,estimated_minutes,required_activity_ids,evidence_refs,dependencies,student_intro,teacher_reason}`。

## 材料、目录、知识

| 方法 路径 | 请求 | 响应 data |
|---|---|---|
| GET /catalog?catalog_version= | — | `{catalog_version,knowledge_version,topics[],indicators[],materials[]（学生为空数组）,links[]}` |
| GET /materials/{material_id}?catalog_version=&activity_id=&plan_version= | — | `MaterialView`（学生无 `reference_answer/reference_explanation/rubric_points/reviewed_variant_tag`） |
| GET /datasets/{dataset_id}/materials/search?catalog_version=&indicator_id=&question_type=&q=&page=&page_size= | — | `{items:[MaterialView],total,page,page_size}` |
| GET /datasets/{dataset_id}/knowledge?knowledge_version= | — | 正式 `KnowledgeSnapshotView`；未构建时 409 `RESULT_NOT_READY` |
| GET /datasets/{dataset_id}/knowledge/reviews?status=&object_type=&page=&page_size= | — | `{items:[KnowledgeReviewItem],total,...}` |
| GET /datasets/{dataset_id}/knowledge/quality?knowledge_version= | — | `KnowledgeQualityReport`；无报告 409 |

## 证据

| 方法 路径 | 请求 | 响应 data |
|---|---|---|
| POST /evidence/resolve | `{ref:{kind,id,run_id?,catalog_version?,dataset_id?,knowledge_version?,analysis_version?,plan_version?,material_id?,attempt_id?}, context?}` | `EvidenceDetail{ref,title,detail_type:record\|metric\|curriculum\|derived\|learning,detail,summary,related_refs,limitations,visibility}` |

引用定位要求：`record`/`metric` 需 `run_id`；`material`/`curriculum` 需 `catalog_version`；
`finding`/`progress` 需 `run_id` 与 `analysis_version`；`learning_event`/`attempt`/`grade` 需 `dataset_id`。

## 学习闭环

| 方法 路径 | 请求 | 响应 data |
|---|---|---|
| POST /plans/{plan_version}/activities/{activity_id}/start | 空体 | `{activity_id,state,event_id,attempt?}`；题目返回 `attempt` |
| POST /plans/{plan_version}/activities/{activity_id}/attempts | 空体 | `AttemptView`（201） |
| POST /plans/{plan_version}/activities/{activity_id}/complete | 空体 | `{activity_id,state,event_id,group_completed}`（仅学习材料） |
| POST /attempts/{attempt_id}/submit | `{final_answer?,solution_steps?,unanswered:false}` + `Idempotency-Key` | `{attempt:AttemptView,grade:GradingResultView?,job_id,feedback_status,group_status}` |
| POST /attempts/{attempt_id}/hints | `{level:1\|2\|3,allow_assisted?}` + `Idempotency-Key` | `{event_id,level,hint}` |
| GET /attempts/{attempt_id} | — | `{attempt,grade,feedback,group_status,update_job_id}` |

`AttemptView`：`{attempt_id,student_id,plan_version,activity_id,material_id,status,submitted_at,final_answer,solution_steps,assistance_status,hint_max_level,agent_help_count,submission_no,latest_grade_id}`。
`GradingResultView`：`{grade_id,attempt_id,revision,status,validation_status,score,max_score,rubric_scores[],method,limitations,created_at}`，
未 `graded` 时 `score` 为 `null`。提示必须逐级释放（只能请求已释放层+1）。

## 模型设置（教师）

| 方法 路径 | 请求 | 响应 data |
|---|---|---|
| GET /model-settings | — | `{configured,provider,url,model_id,has_api_key}` |
| PUT /model-settings | `{provider?,url?,model_id?,api_key?,clear_api_key?}` | 同上 |
| POST /model-settings/test | 空体 | `{connected,message}` |

## 尚未接入（前端必须显示真实"未就绪/不可用"，不得伪造）

* `GET /datasets/{id}/knowledge` 在未运行 A 构建时返回 409；
* 微课堂（`micro-lesson` 读取/完成）、教师方案预览发布（`recommendation-revisions/preview|publish`）、
  `POST /chat` 的 Agent 工具轨迹与确认、`/conversations*`、`POST /runs/{run_id}/exports` 与
  `/exports/{id}/download` 将在 P4—P7 补齐；页面按接口是否存在显示对应状态。
