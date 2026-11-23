import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getModelSettings, isDemo, sendChat, type ChatContext, type ChatMessage } from "../../api/service";
import { useSession } from "../../app/session";
import { ModelSettingsForm } from "./ModelSettingsForm";
import styles from "../../styles/ui.module.css";

interface Props {
  role: "teacher" | "student";
  targetLabel: string;
  nodeName: string;
  backTo: { label: string; to: string };
  analysisContext?: ChatContext;
}

export function ChatPanel({ role, targetLabel, nodeName, backTo, analysisContext }: Props) {
  const { session } = useSession();
  const settings = useQuery({
    queryKey: ["model-settings", session?.session_id],
    queryFn: getModelSettings,
    enabled: Boolean(session),
  });
  const [draft, setDraft] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [includeAnalysis, setIncludeAnalysis] = useState(false);
  const configured = settings.data?.configured === true;
  const canIncludeAnalysis = Boolean(settings.data?.analysis_context_available && analysisContext);

  return <div className={styles.stack}>
    {role === "teacher" && !isDemo && settings.data && <ModelSettingsForm current={settings.data} />}
    {settings.error && <p role="alert">{settings.error.message}</p>}
    <div className={`${styles.card} ${styles.stack}`}>
      <div className={styles.cardHead}>
        <div className={styles.row}>
          <span className={styles.cardTitle}>{role === "teacher" ? "教师对话" : "学习对话"}</span>
          <span className={styles.small}>{targetLabel} · {nodeName}</span>
        </div>
        {!configured && <span className={styles.badge}>模型服务尚未配置</span>}
      </div>
      <div className={`${styles.cardBody} ${styles.stack}`}>
        <p className={styles.small}>消息会发给所配置的模型。只有连接本机模型时，才可选择附上当前学情摘要。</p>
        <Link to={backTo.to}>{backTo.label}</Link>
        <div aria-live="polite" className={styles.stack}>
          {messages.map((message, index) => <div key={index}>
            <strong>{message.role === "user" ? "你" : "AI 助手"}</strong>
            <p style={{ whiteSpace: "pre-wrap" }}>{message.content}</p>
          </div>)}
        </div>
        {error && <p role="alert">{error}</p>}
        <form className={styles.stack} onSubmit={async (event) => {
          event.preventDefault();
          const text = draft.trim();
          if (!configured || busy || !text) return;
          const next: ChatMessage[] = [...messages.slice(-38), { role: "user", content: text }];
          setBusy(true);
          setError("");
          try {
            const reply = await sendChat(next, includeAnalysis && canIncludeAnalysis ? analysisContext : undefined);
            setMessages([...next, { role: "assistant", content: reply.text }]);
            setDraft("");
          } catch (failure) {
            setError(failure instanceof Error ? failure.message : "发送失败，请重试。");
          } finally {
            setBusy(false);
          }
        }}>
          <label className={styles.field}>你的问题
            <textarea className={styles.input} rows={3} value={draft} disabled={!configured || busy}
              maxLength={8000} onChange={(e) => setDraft(e.target.value)} />
          </label>
          {canIncludeAnalysis && <label className={styles.row}>
            <input type="checkbox" checked={includeAnalysis} disabled={busy}
              onChange={(event) => setIncludeAnalysis(event.target.checked)} />
            附上当前学情摘要和已发布学习目标
          </label>}
          <button className={`${styles.button} ${styles.buttonPrimary}`} disabled={!configured || busy || !draft.trim()}>
            {busy ? "等待回复…" : "发送"}
          </button>
        </form>
      </div>
    </div>
  </div>;
}
