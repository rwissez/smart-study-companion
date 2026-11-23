import { useState } from "react";
import { Link } from "react-router-dom";
import styles from "../../styles/ui.module.css";

interface Props {
  role: "teacher" | "student";
  targetLabel: string;
  nodeName: string;
  backTo: { label: string; to: string };
}

/**
 * 双端共用对话视图。
 * 模型未配置时不产生任何回答，也不显示虚构的工具轨迹。
 */
export function ChatPanel({ role, targetLabel, nodeName, backTo }: Props) {
  const [draft, setDraft] = useState("");
  const configured = false;

  return (
    <div className={`${styles.card} ${styles.stack}`}>
      <div className={styles.cardHead}>
        <div className={styles.row}>
          <span className={styles.cardTitle}>
            {role === "teacher" ? "教师对话" : "学习对话"}
          </span>
          <span className={`${styles.badge} ${styles.badgeNeutral}`}>对象：{targetLabel}</span>
          <span className={`${styles.badge} ${styles.badgeNeutral}`}>节点：{nodeName}</span>
        </div>
        <span className={`${styles.badge} ${styles.badgeWarn}`}>模型服务尚未配置</span>
      </div>

      <div className={styles.cardBody}>
        <div className={`${styles.state} ${styles.stateEmpty}`} role="status">
          <span className={styles.stateTitle}>暂无可对话内容</span>
          <span>
            当前构建没有接入模型服务，提问后会返回“模型服务尚未配置”。学情分析、证据和学习建议不受影响，
            可以直接在对应页面查看。
          </span>
          <div className={styles.row}>
            <Link className={styles.button} to={backTo.to}>
              {backTo.label}
            </Link>
          </div>
        </div>
      </div>

      <div className={styles.cardBody} style={{ borderTop: "1px solid var(--border)" }}>
        <label className={styles.field}>
          <span className={styles.fieldLabel}>你的问题</span>
          <textarea
            className={styles.input}
            style={{ minHeight: 88, padding: 10, resize: "vertical" }}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="例如：这名学生在移项上主要卡在哪一步？"
            disabled={!configured}
          />
        </label>
        <div className={styles.row} style={{ marginTop: "var(--space-3)" }}>
          <button type="button" className={`${styles.button} ${styles.buttonPrimary}`} disabled>
            发送
          </button>
          <span className={`${styles.small} ${styles.muted}`}>
            模型未配置，输入框暂不可用；配置接入后再开放提问。
          </span>
        </div>
      </div>
    </div>
  );
}
