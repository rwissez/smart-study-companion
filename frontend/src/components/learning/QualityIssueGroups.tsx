import { useMemo } from "react";
import { Link } from "react-router-dom";
import type { QualityIssue } from "../../api/types";
import { qualityActionLabel, qualityCodeLabel } from "../../lib/labels";

const GROUPS = [
  { key: "error", title: "需要处理（阻断）", open: true, link: "去任务进度 →" },
  { key: "warning", title: "警告", open: false, link: null },
  { key: "info", title: "提示", open: false, link: null },
] as const;

/**
 * 数据质量按严重度分组：阻断级问题展开并带处理入口，警告与提示默认折叠。
 * 教师先看到“有多少件需要处理”，再看逐条明细。
 */
export function QualityIssueGroups({ items }: { items: QualityIssue[] }) {
  const grouped = useMemo(() => {
    const map: Record<string, QualityIssue[]> = { error: [], warning: [], info: [] };
    items.forEach((issue) => {
      const key = issue.severity === "error" || issue.severity === "warning" ? issue.severity : "info";
      map[key].push(issue);
    });
    return map;
  }, [items]);

  return (
    <div className="stackSm">
      <p className="small">
        需要处理 {grouped.error.length} · 警告 {grouped.warning.length} · 提示 {grouped.info.length}
        {grouped.error.length > 0 ? "；阻断级问题会阻塞正式成果，建议先处理。" : ""}
      </p>
      {GROUPS.map((group) => {
        const list = grouped[group.key];
        if (list.length === 0) return null;
        return (
          <details key={group.key} open={group.open}>
            <summary className="small" style={{ cursor: "pointer" }}>
              {group.title}（{list.length}）
            </summary>
            <div className="tableWrap" style={{ marginTop: 8 }}>
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">问题</th>
                    <th scope="col">处理</th>
                    <th scope="col">说明</th>
                  </tr>
                </thead>
                <tbody>
                  {list.map((issue) => (
                    <tr key={issue.issue_id}>
                      <td className="small">{qualityCodeLabel(issue.code)}</td>
                      <td className="small">
                        {qualityActionLabel(issue.action)}
                        {group.link ? (
                          <div>
                            <Link to="/teacher/data?tab=jobs" className="xs">{group.link}</Link>
                          </div>
                        ) : null}
                      </td>
                      <td className="small">{issue.message}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        );
      })}
    </div>
  );
}
