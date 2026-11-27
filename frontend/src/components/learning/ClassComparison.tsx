import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { toErrorMessage } from "../../api/client";
import { compareClasses } from "../../api/service";
import type { ClassComparisonView, ClassSummary } from "../../api/types";
import { formatFraction, formatPercent } from "../../lib/format";
import { queryKeys } from "../../lib/queryKeys";
import { useSession } from "../../app/session";
import { StatePanel } from "../feedback/StatePanel";

interface Props {
  runId: string;
  nodeId: string;
  classes: ClassSummary[];
  names?: Record<string, string>;
  defaultLeft: string;
  defaultRight?: string;
}

/**
 * 班级比较：可比性由服务端判定；不可比时不显示差值，只并列两班原值与原因。
 */
export function ClassComparison({ runId, nodeId, classes, names, defaultLeft, defaultRight }: Props) {
  const { scopeKey } = useSession();
  const [left, setLeft] = useState(defaultLeft);
  const [right, setRight] = useState(defaultRight ?? classes.find((item) => item.class_id !== defaultLeft)?.class_id ?? "");
  const [submitted, setSubmitted] = useState<{ left: string; right: string } | null>(null);

  const enabled = Boolean(submitted && submitted.left && submitted.right && submitted.left !== submitted.right && runId);
  const comparison = useQuery<ClassComparisonView>({
    queryKey: queryKeys.comparison(scopeKey, runId, nodeId, submitted?.left ?? left, submitted?.right ?? right),
    queryFn: () => compareClasses(runId, { nodeId, leftClassId: submitted!.left, rightClassId: submitted!.right }),
    enabled,
  });

  return (
    <div className="stack">
      <div className="row" style={{ alignItems: "flex-end", gap: 16 }}>
        <label className="field">
          <span className="fieldLabel">左班</span>
          <select className="select" value={left} onChange={(event) => setLeft(event.target.value)}>
            {classes.map((item) => (
              <option key={item.class_id} value={item.class_id}>{item.display_name || item.class_id}（{item.roster_count} 人）</option>
            ))}
          </select>
        </label>
        <label className="field">
          <span className="fieldLabel">右班</span>
          <select className="select" value={right} onChange={(event) => setRight(event.target.value)}>
            {classes.map((item) => (
              <option key={item.class_id} value={item.class_id}>{item.display_name || item.class_id}（{item.roster_count} 人）</option>
            ))}
          </select>
        </label>
        <button
          type="button"
          className="btn btnPrimary"
          disabled={!left || !right || left === right}
          onClick={() => setSubmitted({ left, right })}
        >
          比较这两个班
        </button>
        {left === right ? <span className="small muted">请选择两个不同的班级。</span> : null}
      </div>

      {!enabled ? (
        <p className="small muted">选择两个班级后查看服务端返回的可比指标；前端不自行计算班级均值。</p>
      ) : comparison.isPending ? (
        <StatePanel kind="loading" title="正在读取班级比较" />
      ) : comparison.isError ? (
        <StatePanel kind="error" title="读取比较失败" message={toErrorMessage(comparison.error)} />
      ) : comparison.data ? (
        <div className="stack">
          <div className={comparison.data.comparable ? "statePanel" : "statePanel stateReady"}>
            <span className="stateTitle">
              {comparison.data.comparable ? "两班口径一致，可以比较" : "两班口径不一致，本次不做差值比较"}
            </span>
            <span className="small">
              节点 {comparison.data.node_id} · 左 {submitted?.left} · 右 {submitted?.right}
            </span>
            {comparison.data.limitations.length ? (
              <ul>{comparison.data.limitations.map((item) => <li key={item} className="small">{item}</li>)}</ul>
            ) : null}
          </div>
          {comparison.data.items.length ? (
            <div className="tableWrap">
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">观察指标</th>
                    <th scope="col" className="numeric">左班</th>
                    <th scope="col" className="numeric">右班</th>
                    <th scope="col" className="numeric">差值</th>
                    <th scope="col">分子 / 分母（左 / 右）</th>
                    <th scope="col">限制</th>
                  </tr>
                </thead>
                <tbody>
                  {comparison.data.items.map((item) => (
                    <tr key={item.indicator_id || `${item.left_value}-${item.right_value}`}>
                      <td>{names?.[item.indicator_id] ?? item.indicator_id}</td>
                      <td className="numeric">{formatPercent(item.left_value)}</td>
                      <td className="numeric">{formatPercent(item.right_value)}</td>
                      <td className="numeric">
                        {comparison.data.comparable && item.delta !== null ? `${(item.delta * 100).toFixed(1)} 个百分点` : "不做差值"}
                      </td>
                      <td className="small tabular">
                        {formatFraction(item.left_numerator, item.left_denominator)} / {formatFraction(item.right_numerator, item.right_denominator)}
                      </td>
                      <td className="small">{item.limitations.join("；") || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="small muted">接口没有返回可比指标。</p>
          )}
        </div>
      ) : null}
    </div>
  );
}
