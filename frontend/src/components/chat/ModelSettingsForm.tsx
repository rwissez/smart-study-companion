import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { saveModelSettings, testModelSettings, type ModelSettings } from "../../api/service";
import styles from "../../styles/ui.module.css";

export function ModelSettingsForm({ current }: { current: ModelSettings }) {
  const query = useQueryClient();
  const [url, setUrl] = useState(current.url ?? "");
  const [modelId, setModelId] = useState(current.model_id ?? "");
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  const perform = async (action: () => Promise<void>, message: string) => {
    setBusy(true);
    setNotice("");
    setError("");
    try {
      await action();
      setNotice(message);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "操作失败，请重试。");
    } finally {
      setBusy(false);
    }
  };

  return <details className={`${styles.card} ${styles.cardPad}`}>
    <summary>AI 模型配置{current.configured ? " · 已保存" : ""}</summary>
    <form className={styles.stack} onSubmit={(event) => {
      event.preventDefault();
      void perform(async () => {
        await saveModelSettings({ url: url.trim(), model_id: modelId.trim(), api_key: key });
        setKey("");
        await query.invalidateQueries({ queryKey: ["model-settings"] });
      }, "配置已保存，可以测试连接。");
    }}>
      <p>支持兼容 Chat Completions 的服务。填写带版本的 API 地址，或填写完整对话接口。</p>
      <label className={styles.field}>API URL
        <input className={styles.input} type="url" value={url} onChange={(e) => setUrl(e.target.value)} required />
      </label>
      <label className={styles.field}>模型 ID
        <input className={styles.input} value={modelId} onChange={(e) => setModelId(e.target.value)} required />
      </label>
      <label className={styles.field}>API Key
        <input className={styles.input} type="password" autoComplete="new-password" value={key}
          placeholder={current.has_api_key ? "同一地址留空会保留已保存密钥" : "服务要求鉴权时填写"}
          onChange={(e) => setKey(e.target.value)} />
      </label>
      <div className={styles.row}>
        <button className={styles.button} disabled={busy}>保存配置</button>
        <button type="button" className={styles.button} disabled={busy || !current.configured || url.trim() !== current.url || modelId.trim() !== current.model_id || Boolean(key)}
          onClick={() => void perform(async () => { await testModelSettings(); }, "模型连接成功。")}>测试连接</button>
      </div>
      {notice && <p role="status">{notice}</p>}
      {error && <p role="alert">{error}</p>}
    </form>
  </details>;
}
