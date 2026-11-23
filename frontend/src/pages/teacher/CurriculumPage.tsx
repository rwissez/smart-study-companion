import { useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getCatalog } from "../../api/service";
import { queryKeys } from "../../api/queryKeys";
import { StatePanel } from "../../components/feedback/StatePanel";
import styles from "../../styles/ui.module.css";
import page from "../../styles/pages.module.css";
import view from "../../components/learning/TeacherViews.module.css";

/** 课程体系：主题、观察指标与出处，以及人工核对过的必要联系 */
export function CurriculumPage() {
  const catalog = useQuery({
    queryKey: queryKeys.catalog(null),
    queryFn: () => getCatalog(),
    retry: false,
  });

  const [search, setSearch] = useSearchParams();
  const requestedIndicator = search.get("indicatorId");
  const topicId = search.get("topicId") ?? catalog.data?.indicators
    .find((indicator) => indicator.indicator_id === requestedIndicator)?.topic_id;
  const setTopicId = (id: string) => setSearch({ topicId: id });
  const topics = catalog.data?.topics ?? [];
  const active = topicId ?? topics[0]?.topic_id ?? null;
  const current = topics.find((t) => t.topic_id === active);
  const indicators = (catalog.data?.indicators ?? []).filter((i) => i.topic_id === active);
  const indicatorTopic: Record<string, string> = {};
  for (const item of catalog.data?.indicators ?? []) {
    indicatorTopic[item.indicator_id] = item.topic_id;
  }
  // 只显示与当前主题相关的联系：两端指标至少有一端属于这个主题
  const links = (catalog.data?.links ?? []).filter(
    (l) => indicatorTopic[l.from_id] === active || indicatorTopic[l.to_id] === active,
  );

  const indicatorName = (id: string) =>
    catalog.data?.indicators.find((i) => i.indicator_id === id)?.description ?? id;

  if (catalog.isLoading) return <StatePanel kind="loading" title="正在读取课程目录" />;
  if (catalog.error)
    return (
      <StatePanel
        kind="error"
        message={(catalog.error as Error).message}
        onRetry={() => catalog.refetch()}
      />
    );

  return (
    <div className={styles.stackLg}>
      <div className={view.head}><div><div className={view.breadcrumbs}>首页 / 课程体系</div><h2>课程体系</h2></div><span className={view.muted}>目录版本：{catalog.data?.catalog_version ?? "待确认"}</span></div>
      <section className={view.panel}>
        <div className={view.panelHeader}><h2>课程网络</h2><span className={view.muted}>课程主题 · 观察指标</span></div>
        <div className={view.network} aria-label="课程主题与观察指标">
          {topics.map((topic) => <div className={view.networkRow} key={topic.topic_id}>
            <button className={`${view.topic} ${topic.topic_id === active ? view.topicActive : ""}`} onClick={() => setTopicId(topic.topic_id)}>{topic.name}</button>
            <span className={view.networkLine} aria-hidden="true" />
            <div className={view.networkIndicators}>{(catalog.data?.indicators ?? []).filter((item) => item.topic_id === topic.topic_id).map((item) =>
              <button key={item.indicator_id} className={view.networkIndicator} onClick={() => setSearch({ indicatorId: item.indicator_id })}>{item.description}</button>)}</div>
          </div>)}
        </div>
      </section>

      <div className={page.curriculum}>
        <ul className={page.topicList}>
          {topics.map((topic) => (
            <li key={topic.topic_id}>
              <button
                type="button"
                className={`${page.topicButton} ${
                  topic.topic_id === active ? page.topicButtonActive : ""
                }`}
                onClick={() => setTopicId(topic.topic_id)}
              >
                <strong>{topic.name}</strong>
                <span className={`${styles.small} ${styles.muted}`}>{topic.topic_id}</span>
              </button>
            </li>
          ))}
        </ul>

        <div className={styles.stackLg}>
          {current ? (
            <section className={`${styles.card} ${styles.cardPad} ${styles.stack}`}>
              <h3>{current.name}</h3>
              <p className={styles.muted}>{current.goal}</p>
            </section>
          ) : null}

          <section className={styles.stack}>
            <div className={page.sectionHead}>
              <h2>观察指标</h2>
              <span className={`${styles.small} ${styles.muted}`}>
                共 {indicators.length} 项
              </span>
            </div>

            <div className={`${styles.card} ${styles.tableScroll}`} tabIndex={0} role="region" aria-label="观察指标表">
              <table className={styles.table}>
                <thead>
                  <tr>
                    <th>指标编号</th>
                    <th>观察指标</th>
                    <th>课标条目</th>
                    <th>课标位置</th>
                    <th>教材页码</th>
                  </tr>
                </thead>
                <tbody>
                  {indicators.map((item) => (
                    <tr key={item.indicator_id}>
                      <td className={styles.num}>{item.indicator_id}</td>
                      <td>{item.description}</td>
                      <td className={styles.num}>{item.curriculum_ref}</td>
                      <td>{item.curriculum_page ? `课程标准（2022 年版）· 文件页／正文页 ${item.curriculum_page}（赛方表列）` : "暂无页码"}</td>
                      <td>
                        <div>{item.textbook_pages || "暂无正文页码"}</div>
                        {item.textbook_refs?.map((source) => (
                          <div key={`${source.source_file}:${source.pdf_page}`}>
                            {source.source_file.split("/").at(-1)} · 文件第 {source.pdf_page} 页
                          </div>
                        ))}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className={styles.stack}>
            <div className={page.sectionHead}>
              <h2>必要联系</h2>
              <span className={`${styles.small} ${styles.muted}`}>
                关系来源
              </span>
            </div>

            <div className={styles.stack}>
              {links.length === 0 ? (
                <StatePanel kind="empty" title="当前主题暂无登记的联系" />
              ) : (
                links.map((link) => (
                  <div
                    key={`${link.from_id}-${link.to_id}`}
                    className={`${styles.card} ${styles.cardPad} ${styles.stack}`}
                  >
                    <div className={styles.row}>
                      <span className={`${styles.badge} ${styles.badgeInfo}`}>
                        {link.relation === "prerequisite" ? "前置关系" : "支撑关系"}
                      </span>
                      <strong>{indicatorName(link.from_id)}</strong>
                      <span aria-hidden="true">→</span>
                      <strong>{indicatorName(link.to_id)}</strong>
                    </div>
                    <p className={`${styles.small} ${styles.muted}`}>{link.description ?? link.basis}</p>
                  </div>
                ))
              )}
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
