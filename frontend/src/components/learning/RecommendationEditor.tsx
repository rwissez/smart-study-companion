import { useState } from "react";
import type { Recommendation } from "../../api/types";
import styles from "../../styles/ui.module.css";

type Revision = { recommendation: Recommendation; reason: string };

export function RecommendationEditor({
  recommendation, history: initialHistory, onSave,
}: {
  recommendation: Recommendation;
  history: Revision[];
  onSave: (value: Recommendation, reason: string) => Revision[];
}) {
  const [history, setHistory] = useState(initialHistory);
  const [draft, setDraft] = useState<Recommendation | null>(null);
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const activityListId = `edit-material-${recommendation.recommendation_id}`;

  const updateActivity = (index: number, change: Partial<Recommendation["activities"][number]>) => {
    if (!draft) return;
    setDraft({ ...draft, activities: draft.activities.map((item, position) =>
      position === index ? { ...item, ...change } : item) });
  };

  return <div className={styles.stack}>
    {!draft && <button className={styles.button} onClick={() => {
      setReason("");
      setError("");
      setDraft(recommendation);
    }}>调整学习建议</button>}
    {draft && <form className={`${styles.card} ${styles.cardPad} ${styles.stack}`} onSubmit={(event) => {
      event.preventDefault();
      if (!draft.goal.trim() || !reason.trim()) {
        setError("请填写学习目标和修改理由。");
        return;
      }
      setHistory(onSave({ ...draft, goal: draft.goal.trim() }, reason.trim()));
      setDraft(null);
    }}>
      <strong>调整示例建议 · 当前版本 {history.length + 1}</strong>
      <label className={styles.field}>学习目标
        <input className={styles.input} value={draft.goal} onChange={(e) => setDraft({ ...draft, goal: e.target.value })} />
      </label>
      <label className={styles.field}>推荐说明
        <textarea className={styles.input} value={draft.reason} onChange={(e) => setDraft({ ...draft, reason: e.target.value })} />
      </label>
      {draft.activities.map((activity, index) => <fieldset className={styles.stack} key={activity.order}>
        <legend>活动 {index + 1} · {activity.material_id}</legend>
        <label className={styles.field}>分步提示（每行一条）
          <textarea aria-describedby={activityListId} className={styles.input} value={activity.hint_levels.join("\n")}
            onChange={(e) => updateActivity(index, { hint_levels: e.target.value.split("\n") })} />
        </label>
        {index > 0 && <button type="button" className={styles.button} onClick={() => {
          const next = [...draft.activities];
          [next[index - 1], next[index]] = [next[index], next[index - 1]];
          setDraft({ ...draft, activities: next.map((item, position) => ({ ...item, order: position + 1 })) });
        }}>上移此活动</button>}
      </fieldset>)}
      <span id={activityListId} className={styles.small}>示例模式 · 修改仅保留在当前会话</span>
      {draft.adjustment_conditions.map((condition, index) => <fieldset className={styles.stack} key={index}>
        <legend>后续调整条件 {index + 1}</legend>
        <label className={styles.field}>观察到
          <input className={styles.input} value={condition.observable_condition} onChange={(event) =>
            setDraft({ ...draft, adjustment_conditions: draft.adjustment_conditions.map((item, position) =>
              position === index ? { ...item, observable_condition: event.target.value } : item) })} />
        </label>
        <label className={styles.field}>下一步
          <input className={styles.input} value={condition.next_action} onChange={(event) =>
            setDraft({ ...draft, adjustment_conditions: draft.adjustment_conditions.map((item, position) =>
              position === index ? { ...item, next_action: event.target.value } : item) })} />
        </label>
      </fieldset>)}
      <label className={styles.field}>修改理由
        <textarea className={styles.input} value={reason} onChange={(e) => setReason(e.target.value)} />
      </label>
      {error && <p role="alert">{error}</p>}
      <div className={styles.row}>
        <button type="submit" className={styles.button}>保存示例版本</button>
        <button type="button" className={styles.button} onClick={() => setDraft(null)}>取消</button>
      </div>
    </form>}
    {history.length > 0 && <div className={styles.stack}>
      <span role="status">当前示例版本 {history.length + 1}</span>
      {history.map((entry, index) => <details key={index}>
        <summary>查看原版本 {index + 1}</summary>
        <p>{entry.recommendation.goal} · {entry.recommendation.reason}</p>
        <ol>{entry.recommendation.activities.map((activity) =>
          <li key={activity.order}>{activity.material_id} · {activity.purpose} · {activity.hint_levels.join("；")}</li>)}</ol>
        <p>改为下一版本的理由：{entry.reason}</p>
      </details>)}
    </div>}
  </div>;
}
