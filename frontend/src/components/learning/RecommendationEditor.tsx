import { useState } from "react";
import type { Catalog, Recommendation, RecommendationRevision } from "../../api/types";
import styles from "../../styles/ui.module.css";

export function RecommendationEditor({
  recommendation, history, materials = [], demo, onSave,
}: {
  recommendation: Recommendation;
  history: RecommendationRevision[];
  materials?: Catalog["materials"];
  demo?: boolean;
  onSave: (value: Recommendation, reason: string, revision: number, publish: boolean) => void | Promise<void>;
}) {
  const [draft, setDraft] = useState<Recommendation | null>(null);
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [baseRevision, setBaseRevision] = useState(1);
  const options = materials.filter((item) => item.indicator_ids.includes(recommendation.indicator_id));
  const materialLabel = (id: string) => {
    const item = materials.find((material) => material.material_id === id);
    return item ? item.content.slice(0, 90) : "当前材料";
  };

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
      setBaseRevision(history.length + 1);
    }}>{demo ? "调整学习建议" : "调整或审核发布"}</button>}
    {draft && <form className={`${styles.card} ${styles.cardPad} ${styles.stack}`} onSubmit={async (event) => {
      event.preventDefault();
      const submitter = (event.nativeEvent as SubmitEvent).submitter;
      const publish = submitter instanceof HTMLButtonElement && submitter.value === "publish";
      if (!draft.goal.trim() || !reason.trim()) {
        setError("请填写学习目标和修改理由。");
        return;
      }
      setSaving(true);
      setError("");
      try {
        await onSave({
          ...draft, goal: draft.goal.trim(),
          activities: draft.activities.map((activity) => ({
            ...activity,
            hint_levels: activity.hint_levels.map((line) => line.trim()).filter(Boolean),
            observation_points: activity.observation_points.map((line) => line.trim()).filter(Boolean),
          })),
        }, reason.trim(), baseRevision, publish);
        setDraft(null);
      } catch (failure) {
        setError(failure instanceof Error ? failure.message : "保存失败，请重试。");
      } finally {
        setSaving(false);
      }
    }}>
      <fieldset disabled={saving} className={styles.stack}>
      <legend>调整学习建议 · 第 {baseRevision} 版</legend>
      {!demo && <p>保存草稿后可以继续调整。审核发布后，学生才能看到这次修改。</p>}
      <label className={styles.field}>学习目标
        <input className={styles.input} value={draft.goal} onChange={(e) => setDraft({ ...draft, goal: e.target.value })} />
      </label>
      <label className={styles.field}>推荐说明
        <textarea className={styles.input} value={draft.reason} onChange={(e) => setDraft({ ...draft, reason: e.target.value })} />
      </label>
      {draft.activities.map((activity, index) => <fieldset className={styles.stack} key={activity.order}>
        <legend>活动 {index + 1}</legend>
        <label className={styles.field}>推荐材料
          <select className={styles.select} value={activity.material_id} onChange={(e) => updateActivity(index, {
            material_id: e.target.value, purpose: "", hint_levels: [], observation_points: [],
          })}>
            {!options.some((item) => item.material_id === activity.material_id) && <option value={activity.material_id}>{materialLabel(activity.material_id)}</option>}
            {options.map((item) => <option key={item.material_id} value={item.material_id}>{materialLabel(item.material_id)}</option>)}
          </select>
        </label>
        {!activity.purpose && !demo && <small>请根据新材料填写做法、提示和观察点。</small>}
        <label className={styles.field}>具体做法
          <textarea className={styles.input} value={activity.purpose} onChange={(e) => updateActivity(index, { purpose: e.target.value })} />
        </label>
        <label className={styles.field}>分步提示（每行一条）
          <textarea className={styles.input} value={activity.hint_levels.join("\n")}
            onChange={(e) => updateActivity(index, { hint_levels: e.target.value.split("\n") })} />
        </label>
        <label className={styles.field}>观察点（每行一条）
          <textarea className={styles.input} value={activity.observation_points.join("\n")}
            onChange={(e) => updateActivity(index, { observation_points: e.target.value.split("\n") })} />
        </label>
        {index > 0 && <button type="button" className={styles.button} onClick={() => {
          const next = [...draft.activities];
          [next[index - 1], next[index]] = [next[index], next[index - 1]];
          setDraft({ ...draft, activities: next.map((item, position) => ({ ...item, order: position + 1 })) });
        }}>上移此活动</button>}
        <button type="button" className={styles.button} onClick={() => setDraft({
          ...draft, activities: draft.activities.filter((_, position) => position !== index)
            .map((item, position) => ({ ...item, order: position + 1 })),
        })}>移除此活动</button>
      </fieldset>)}
      {options.length > 0 && <button type="button" className={styles.button} onClick={() => setDraft({
        ...draft,
        activities: [...draft.activities, {
          order: draft.activities.length + 1, material_id: options[0].material_id, purpose: "",
          hint_levels: [], observation_points: [], previously_attempted: false,
        }],
      })}>添加推荐材料</button>}
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
        <button type="submit" className={styles.button}>{demo ? "保存示例版本" : "保存草稿"}</button>
        {!demo && <button type="submit" value="publish" className={`${styles.button} ${styles.buttonPrimary}`}>审核并发布给学生</button>}
        <button type="button" className={styles.button} onClick={() => setDraft(null)}>取消</button>
      </div>
      </fieldset>
    </form>}
    {history.length > 0 && <div className={styles.stack}>
      <span role="status">{demo ? "当前示例版本" : "当前版本"} {history.length + 1}</span>
      {history.map((entry, index) => <details key={index}>
        <summary>查看原版本 {index + 1}</summary>
        <p>{entry.recommendation.goal} · {entry.recommendation.reason}</p>
        <ol>{entry.recommendation.activities.map((activity) =>
          <li key={activity.order}>{materialLabel(activity.material_id)} · {activity.purpose} · {activity.hint_levels.join("；")}</li>)}</ol>
        <p>改为下一版本的理由：{entry.reason}</p>
      </details>)}
    </div>}
  </div>;
}
