import { Link } from "react-router-dom";
import type { AlertItem, ClassResultView } from "../../api/types";

interface Props {
  result: ClassResultView;
  names: Record<string, string>;
  nodeLabel: string | null;
  alerts: AlertItem[];
  chatHref: string;
}

interface FocusAction {
  key: string;
  severity: "alert" | "attention" | "common";
  title: string;
  detail: string | null;
  studentId: string | null;
}

/**
 * 班级总览的结论卡：把“现在需要处理什么”排在数据前面。
 * 行动按 提醒 → 服务端标记的关注学生 → 共性问题 排序；全部为空时给明确结论。
 */
export function TodayFocus({ result, names, nodeLabel, alerts, chatHref }: Props) {
  const actions: FocusAction[] = [];
  alerts
    .filter((item) => item.status !== "resolved")
    .forEach((item) => {
      actions.push({
        key: `alert-${item.alert_id}`,
        severity: "alert",
        title: item.summary,
        detail: item.student_id ? `提醒 · ${item.student_id}` : "提醒",
        studentId: item.student_id,
      });
    });
  result.attention_students.forEach((item) => {
    actions.push({
      key: `attention-${item.student_id}`,
      severity: "attention",
      title: `${item.student_id}：${item.reason}`,
      detail: "服务端标记待关注",
      studentId: item.student_id,
    });
  });
  result.common_findings.slice(0, 3).forEach((item) => {
    const name = names[item.indicator_id] ?? item.indicator_id;
    actions.push({
      key: `common-${item.finding_id ?? item.indicator_id}`,
      severity: "common",
      title: name,
      detail: item.description ?? null,
      studentId: null,
    });
  });

  return (
    <section className="section todayFocus" aria-labelledby="today-focus-title">
      <div className="sectionHead">
        <h2 className="sectionTitle" id="today-focus-title">
          今日关注{nodeLabel ? ` · ${nodeLabel}` : ""}
        </h2>
        <span className="sectionHint">
          {actions.length > 0 ? `${actions.length} 件待处理，按优先级排列` : "本轮没有需要立即处理的事项"}
        </span>
      </div>
      {actions.length === 0 ? (
        <p className="small muted">
          服务端没有标记待关注学生，也没有共性问题。下一次观察后这里会更新行动建议。
        </p>
      ) : (
        <ol className="todayFocusList">
          {actions.slice(0, 6).map((action) => (
            <li key={action.key} className="todayFocusItem">
              <span className={`tag tagWarning`}>
                {action.severity === "alert" ? "提醒" : action.severity === "attention" ? "待关注" : "共性问题"}
              </span>
              <span className="todayFocusText">
                {action.title}
                {action.detail ? <span className="xs muted"> · {action.detail}</span> : null}
              </span>
              {action.studentId ? (
                <Link className="btn btnSm" to={`/teacher/students/${encodeURIComponent(action.studentId)}`}>
                  进入画像
                </Link>
              ) : null}
            </li>
          ))}
        </ol>
      )}
      <div className="row" style={{ gap: 8, marginTop: 12 }}>
        <Link className="btn btnSm btnPrimary" to={chatHref}>
          就此班级提问 AI
        </Link>
        <Link className="btn btnSm" to="/teacher/data?tab=quality">
          查看数据质量
        </Link>
      </div>
    </section>
  );
}
