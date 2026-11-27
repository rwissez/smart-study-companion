import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { errorCode, errorRequestId, toErrorMessage } from "../../api/client";
import { getCatalog, getKnowledge, getKnowledgeQuality, getSourcePage, listKnowledgeReviews } from "../../api/service";
import type { KnowledgePoint, KnowledgeReviewItem, KnowledgeSnapshotView } from "../../api/types";
import { useSession } from "../../app/session";
import { queryKeys } from "../../lib/queryKeys";
import { StatePanel } from "../../components/feedback/StatePanel";
import { KnowledgeGraph } from "../../components/learning/KnowledgeGraph";
import { KnowledgeReviewDialog } from "../../components/learning/KnowledgeReviewDialog";
import { IconSearch } from "../../components/icons";

const TABS = [
  { key: "graph", label: "知识图谱" },
  { key: "reviews", label: "审核" },
] as const;

export function CurriculumPage() {
  const { scopeKey } = useSession();
  const queryClient = useQueryClient();
  const datasetId = scopeKey.datasetId;
  const [searchParams, setSearchParams] = useSearchParams();
  const tab = searchParams.get("tab") === "reviews" ? "reviews" : "graph";
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [reviewItem, setReviewItem] = useState<KnowledgeReviewItem | null>(null);
  const [reviewPage, setReviewPage] = useState(1);
  const [reviewNotice, setReviewNotice] = useState<string | null>(null);
  const [sourceUrl, setSourceUrl] = useState<string | null>(null);
  const [sourceBusy, setSourceBusy] = useState(false);
  const [sourceError, setSourceError] = useState<string | null>(null);

  useEffect(() => {
    return () => {
      if (sourceUrl) URL.revokeObjectURL(sourceUrl);
    };
  }, [sourceUrl]);

  const openSourcePage = async (fileId: string, page: number) => {
    setSourceBusy(true);
    setSourceError(null);
    try {
      const blob = await getSourcePage(fileId, page);
      if (sourceUrl) URL.revokeObjectURL(sourceUrl);
      setSourceUrl(URL.createObjectURL(blob));
    } catch (reason) {
      setSourceError(toErrorMessage(reason));
    } finally {
      setSourceBusy(false);
    }
  };

  const catalog = useQuery({
    queryKey: queryKeys.catalog(scopeKey, null),
    queryFn: () => getCatalog(),
    enabled: Boolean(datasetId),
  });

  const knowledge = useQuery<KnowledgeSnapshotView>({
    queryKey: queryKeys.knowledge(scopeKey, null),
    queryFn: () => getKnowledge(datasetId as string),
    enabled: Boolean(datasetId),
  });

  const reviews = useQuery({
    queryKey: queryKeys.knowledgeReviews(scopeKey, reviewPage),
    queryFn: () => listKnowledgeReviews(datasetId as string, reviewPage, 20),
    enabled: Boolean(datasetId && tab === "reviews"),
  });

  const quality = useQuery({
    queryKey: queryKeys.knowledgeQuality(scopeKey),
    queryFn: () => getKnowledgeQuality(datasetId as string),
    enabled: Boolean(datasetId && tab === "reviews" && knowledge.isSuccess),
  });

  const points = knowledge.data?.points ?? [];
  const filteredPoints = useMemo(() => {
    const text = search.trim().toLowerCase();
    if (!text) return points;
    return points.filter((point) => point.name.toLowerCase().includes(text) || point.kp_id.toLowerCase().includes(text));
  }, [points, search]);

  const activePoint: KnowledgePoint | null =
    points.find((point) => point.kp_id === selectedId) ?? points[0] ?? null;

  const names = useMemo(() => {
    const map: Record<string, string> = {};
    catalog.data?.indicators.forEach((indicator) => { map[indicator.indicator_id] = indicator.description; });
    catalog.data?.topics.forEach((topic) => { map[topic.topic_id] = topic.title; });
    return map;
  }, [catalog.data]);

  const pointNames = useMemo(() => {
    const map: Record<string, string> = {};
    (knowledge.data?.points ?? []).forEach((point) => { map[point.kp_id] = point.name; });
    return map;
  }, [knowledge.data]);

  const materialNames = useMemo(() => {
    const map: Record<string, string> = {};
    (catalog.data?.materials ?? []).forEach((material) => {
      map[material.material_id] = material.source_label || material.content.slice(0, 18);
    });
    return map;
  }, [catalog.data]);

  const qualityCounts = quality.data?.counts ?? (knowledge.data?.review_summary?.counts as Record<string, number> | undefined) ?? {};

  if (!datasetId) {
    return (
      <StatePanel kind="empty" title="当前会话没有绑定数据集" message="选择数据集后才能读取正式知识体系。" />
    );
  }

  return (
    <div className="stackLg">
      <div className="pageHeader">
        <div>
          <h1 className="pageTitle">课程体系</h1>
          <p className="pageSub">
            {knowledge.data ? "正式知识版本 · 已发布 · 只读" : "读取正式知识版本"}
          </p>
        </div>
        <div className="pageActions">
          {TABS.map((item) => (
            <button
              key={item.key}
              type="button"
              className={tab === item.key ? "tab tabActive" : "tab"}
              aria-pressed={tab === item.key}
              onClick={() => setSearchParams({ tab: item.key })}
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>

      {tab === "graph" ? (
        <>
          {knowledge.isPending || catalog.isPending ? <StatePanel kind="loading" title="正在读取知识版本" /> : null}

          {knowledge.isError ? (
            <StatePanel
              kind={errorCode(knowledge.error) === "RESULT_NOT_READY" ? "not-ready" : "error"}
              title={errorCode(knowledge.error) === "RESULT_NOT_READY" ? "知识体系尚未就绪" : "知识体系读取失败"}
              message={
                <>
                  {toErrorMessage(knowledge.error)}
                  <br />
                  A 构建尚未完成时，这里不会用课程目录冒充知识体系。可以在数据管理查看任务链的 knowledge 阶段。
                </>
              }
              requestId={errorRequestId(knowledge.error)}
              onRetry={() => void knowledge.refetch()}
            />
          ) : null}

          {knowledge.isSuccess && catalog.isSuccess ? (
            <div className="curriculumLayout">
              <div className="pane">
                <div className="paneTitle">主题与指标</div>
                <label className="field" style={{ marginBottom: 8 }}>
                  <span className="srOnly">搜索知识点</span>
                  <span className="row" style={{ gap: 6 }}>
                    <IconSearch size={16} />
                    <input
                      className="input"
                      type="search"
                      placeholder="搜索名称或编号"
                      value={search}
                      onChange={(event) => setSearch(event.target.value)}
                    />
                  </span>
                </label>
                <div className="treeList">
                  {(catalog.data?.topics ?? []).map((topic) => (
                    <div key={topic.topic_id}>
                      <div className="treeTopic">{topic.title}</div>
                      {filteredPoints
                        .filter((point) => point.topic_ids.includes(topic.topic_id))
                        .map((point) => (
                          <button
                            key={point.kp_id}
                            type="button"
                            className={activePoint?.kp_id === point.kp_id ? "treeItem treeItemActive" : "treeItem"}
                            aria-pressed={activePoint?.kp_id === point.kp_id}
                            onClick={() => setSelectedId(point.kp_id)}
                          >
                            <span>{point.name}</span>
                            <span className="treeItemMeta">
                              {point.indicator_ids.map((indicatorId) => names[indicatorId] ?? "观察指标").join("、")}
                            </span>
                          </button>
                        ))}
                    </div>
                  ))}
                  {filteredPoints.filter((point) => point.topic_ids.length === 0).map((point) => (
                    <button key={point.kp_id} type="button" className="treeItem" onClick={() => setSelectedId(point.kp_id)}>
                      <span>{point.name}</span>
                      <span className="treeItemMeta">未归类 · 观察指标</span>
                    </button>
                  ))}
                </div>
              </div>

              <div className="pane">
                <div className="paneTitle">关系图</div>
                <KnowledgeGraph
                  snapshot={knowledge.data}
                  catalog={catalog.data}
                  selectedId={activePoint?.kp_id ?? null}
                  onSelect={setSelectedId}
                />
                {knowledge.data.limitations.length ? (
                  <p className="small muted" style={{ marginTop: 8 }}>限制：{knowledge.data.limitations.join("；")}</p>
                ) : null}
              </div>

              <div className="pane">
                <div className="paneTitle">定义与来源</div>
                {activePoint ? (
                  <div className="stack">
                    <div>
                      <h3 className="sectionTitle">{activePoint.name}</h3>
                      <p className="xs muted">概念 · {activePoint.scope_role === "core" ? "核心" : "拓展"}</p>
                    </div>
                    <p className="definition">{activePoint.definition}</p>
                    <div>
                      <h4 className="paneTitle">观察指标</h4>
                      <ul>
                        {activePoint.indicator_ids.map((indicatorId) => (
                          <li key={indicatorId} className="small">{names[indicatorId] ?? indicatorId}</li>
                        ))}
                      </ul>
                    </div>
                    <div>
                      <h4 className="paneTitle">出处</h4>
                      {activePoint.source_locators.length === 0 ? (
                        <p className="small muted">没有登记来源页。</p>
                      ) : (
                        <ul className="sourceList">
                          {activePoint.source_locators.map((locator, index) => (
                            <li key={index}>
                              {locator.source_file}
                              {locator.pdf_page ? ` · PDF 第 ${locator.pdf_page} 页` : ""}
                              {locator.printed_page ? ` · 正文第 ${locator.printed_page} 页` : ""}
                              {` · ${locator.verification_status}`}
                              {locator.file_id && locator.pdf_page ? (
                                <button
                                  type="button"
                                  className="btn btnSm btnGhost"
                                  style={{ marginLeft: 6 }}
                                  disabled={sourceBusy}
                                  onClick={() => void openSourcePage(locator.file_id as string, locator.pdf_page as number)}
                                >
                                  查看原页
                                </button>
                              ) : null}
                            </li>
                          ))}
                        </ul>
                      )}
                      {sourceError ? <p className="fieldError" role="alert">{sourceError}</p> : null}
                      {sourceUrl ? (
                        <div className="stackSm" style={{ marginTop: 8 }}>
                          <div className="rowBetween">
                            <span className="small muted">受控来源页（临时链接，关闭后失效）</span>
                            <button
                              type="button"
                              className="btn btnSm"
                              onClick={() => {
                                URL.revokeObjectURL(sourceUrl);
                                setSourceUrl(null);
                              }}
                            >
                              关闭原页
                            </button>
                          </div>
                          <iframe className="sourceFrame" src={sourceUrl} title="受控来源页" />
                        </div>
                      ) : null}
                    </div>
                    <div>
                      <h4 className="paneTitle">实际关联</h4>
                      {(knowledge.data.annotations ?? []).filter((item) => item.target_ids.includes(activePoint.kp_id)).length === 0 ? (
                        <p className="small muted">没有材料关联记录。</p>
                      ) : (
                        <ul className="sourceList">
                          {(knowledge.data.annotations ?? [])
                            .filter((item) => item.target_ids.includes(activePoint.kp_id))
                            .map((item) => (
                              <li key={item.annotation_id}>
                                {item.source_type === "material" ? "材料" : "来源"} · {materialNames[item.source_id] ?? "已登记材料"} · {item.knowledge_role === "primary" ? "主要关联" : "补充关联"}
                              </li>
                            ))}
                        </ul>
                      )}
                    </div>
                    <div>
                      <h4 className="paneTitle">正式关系</h4>
                      {(knowledge.data.relations ?? []).filter((relation) => relation.source_ids.includes(activePoint.kp_id) || relation.target_id === activePoint.kp_id).length === 0 ? (
                        <p className="small muted">这个知识点没有正式关系边。</p>
                      ) : (
                        <ul className="sourceList">
                          {(knowledge.data.relations ?? [])
                            .filter((relation) => relation.source_ids.includes(activePoint.kp_id) || relation.target_id === activePoint.kp_id)
                            .map((relation, index) => (
                              <li key={index}>
                                {relation.source_ids.map((sourceId) => pointNames[sourceId] ?? "知识点").join("、")} → {pointNames[relation.target_id] ?? "知识点"} · {relation.relation_type === "prerequisite" || relation.type === "prerequisite" ? "前置" : "关联"}
                              </li>
                            ))}
                        </ul>
                      )}
                    </div>
                  </div>
                ) : (
                  <p className="small muted">左侧选择一个知识点。</p>
                )}
              </div>
            </div>
          ) : null}
        </>
      ) : (
        <section className="section" aria-label="知识审核">
          <div className="sectionHead">
            <h2 className="sectionTitle">异常与审核</h2>
            <span className="sectionHint">选中对象后通过 / 拒绝 / 编辑并重审</span>
          </div>
          {reviewNotice ? <p className="small" role="status">{reviewNotice}</p> : null}
          <div className="row" style={{ marginBottom: 12 }}>
            {Object.entries(qualityCounts).slice(0, 8).map(([key, value]) => (
              <span className="tag" key={key}>{key} {String(value)}</span>
            ))}
            {Object.keys(qualityCounts).length === 0 ? <span className="small muted">没有可展示的质量计数。</span> : null}
          </div>
          {quality.isError && !(errorCode(quality.error) === "RESULT_NOT_READY") ? (
            <p className="small fieldError">质量报告读取失败：{toErrorMessage(quality.error)}</p>
          ) : null}
          {quality.isError && errorCode(quality.error) === "RESULT_NOT_READY" ? (
            <p className="small muted">质量报告尚未就绪：{toErrorMessage(quality.error)}</p>
          ) : null}

          {reviews.isPending ? <StatePanel kind="loading" title="正在读取审核队列" /> : null}
          {reviews.isError ? (
            <StatePanel
              kind="error"
              title="审核队列读取失败"
              message={toErrorMessage(reviews.error)}
              requestId={errorRequestId(reviews.error)}
              onRetry={() => void reviews.refetch()}
            />
          ) : null}
          {reviews.data && reviews.data.items.length === 0 ? (
            <StatePanel kind="empty" title="当前没有待审核对象" message="知识候选都已有结论；新的候选会在 A 构建后出现。" />
          ) : null}
          {reviews.data && reviews.data.items.length > 0 ? (
            <div className="tableWrap">
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">对象</th>
                    <th scope="col">类型</th>
                    <th scope="col">状态</th>
                    <th scope="col">检查与问题</th>
                    <th scope="col">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {reviews.data.items.map((item) => (
                    <tr key={item.review_id}>
                      <td className="small">{item.summary || "知识候选对象"}</td>
                      <td className="small">{item.object_type === "knowledge_point" ? "知识点" : item.object_type === "relation" ? "关系" : item.object_type === "annotation" ? "材料关联" : "对象"}</td>
                      <td className="small">{String((item.review_info as Record<string, unknown>).status) === "validated" ? "已通过" : String((item.review_info as Record<string, unknown>).status) === "rejected" ? "已拒绝" : "待审核"}</td>
                      <td className="small">
                        {Array.isArray((item.review_info as Record<string, unknown>).issues)
                          ? ((item.review_info as Record<string, unknown>).issues as Array<Record<string, unknown>>).map((issue, index) => (
                              <div key={index}>{String(issue.reason ?? issue.code ?? "问题")}</div>
                            ))
                          : "—"}
                      </td>
                      <td>
                        <button type="button" className="btn btnSm" onClick={() => setReviewItem(item)}>
                          审核
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="row" style={{ marginTop: 8 }}>
                <span className="small muted">通过会发布新的知识版本；拒绝只记录决定；编辑生成新候选。</span>
              </div>
              <div className="row" style={{ justifyContent: "flex-end", marginTop: 8 }}>
                <span className="small muted">
                  共 {reviews.data.total} 条 · 第 {reviews.data.page} / {Math.max(1, Math.ceil(reviews.data.total / reviews.data.page_size))} 页
                </span>
                <button
                  type="button"
                  className="btn btnSm"
                  disabled={reviews.data.page <= 1}
                  onClick={() => setReviewPage((value) => Math.max(1, value - 1))}
                >
                  上一页
                </button>
                <button
                  type="button"
                  className="btn btnSm"
                  disabled={reviews.data.page * reviews.data.page_size >= reviews.data.total}
                  onClick={() => setReviewPage((value) => value + 1)}
                >
                  下一页
                </button>
              </div>
            </div>
          ) : null}
        </section>
      )}

      <KnowledgeReviewDialog
        open={reviewItem !== null}
        datasetId={datasetId}
        item={reviewItem}
        onClose={() => setReviewItem(null)}
        onDone={(message) => {
          setReviewNotice(message);
          void queryClient.invalidateQueries({ queryKey: queryKeys.knowledgeReviews(scopeKey, reviewPage) });
          void queryClient.invalidateQueries({ queryKey: queryKeys.knowledge(scopeKey, null) });
          void queryClient.invalidateQueries({ queryKey: queryKeys.knowledgeQuality(scopeKey) });
          void reviews.refetch();
          void knowledge.refetch();
        }}
      />
    </div>
  );
}
