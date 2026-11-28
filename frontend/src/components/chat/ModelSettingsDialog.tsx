import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { errorRequestId, toErrorMessage } from "../../api/client";
import { getModelSettings, saveModelSettings, testModelSettings } from "../../api/service";
import { queryKeys } from "../../lib/queryKeys";
import { useSession } from "../../app/session";
import type { ModelSettings } from "../../api/types";
import { Modal } from "../ui/Modal";

interface Props {
  open: boolean;
  onClose: () => void;
}

/**
 * 模型设置：密钥输入框只用于本次提交，提交后立即清空前端变量；
 * “已保存密钥”只显示 has_api_key 状态，不回显任何密钥内容。
 * 外发授权也放在这里，聊天页右上角的未配置标记可直达。
 */
export function ModelSettingsDialog({ open, onClose }: Props) {
  const { scopeKey } = useSession();
  const queryClient = useQueryClient();
  const [provider, setProvider] = useState("");
  const [url, setUrl] = useState("");
  const [modelId, setModelId] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [clearKey, setClearKey] = useState(false);
  const [allowExternal, setAllowExternal] = useState(false);
  const [configured, setConfigured] = useState<ModelSettings | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<{ text: string; requestId: string | null } | null>(null);
  const [testResult, setTestResult] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setMessage(null);
    setError(null);
    setTestResult(null);
    getModelSettings()
      .then((value) => {
        setConfigured(value);
        setProvider(value.provider ?? "");
        setUrl(value.url ?? "");
        setModelId(value.model_id ?? "");
        setAllowExternal(value.allow_external_model_input);
      })
      .catch((reason) => {
        setError({ text: toErrorMessage(reason), requestId: errorRequestId(reason) });
      });
  }, [open]);

  const resetFeedback = () => {
    setMessage(null);
    setError(null);
    setTestResult(null);
  };

  const save = async () => {
    setBusy(true);
    resetFeedback();
    try {
      const latest = await saveModelSettings({
        provider: provider || undefined,
        url: url || undefined,
        model_id: modelId || undefined,
        api_key: apiKey || undefined,
        clear_api_key: clearKey || undefined,
        allow_external_model_input: allowExternal,
      });
      setApiKey("");
      setClearKey(false);
      setConfigured(latest);
      setMessage("已保存。密钥不会回显，也不会写进浏览器缓存。");
      queryClient.setQueryData(queryKeys.modelSettings(scopeKey), latest);
    } catch (reason) {
      setError({ text: toErrorMessage(reason), requestId: errorRequestId(reason) });
    } finally {
      setBusy(false);
    }
  };

  const test = async () => {
    setBusy(true);
    resetFeedback();
    try {
      const result = await testModelSettings();
      setTestResult(
        result.connected
          ? `连接成功：${result.message ?? "接口可以连通"}。这只说明接口可连，不代表教学模型效果已验证。`
          : `连接失败：${result.message ?? "服务没有返回原因"}`,
      );
    } catch (reason) {
      setError({ text: toErrorMessage(reason), requestId: errorRequestId(reason) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open={open}
      title="模型设置与外发授权"
      description="教师本地配置；密钥只提交给本机后端，不在聊天正文与日志中出现。"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose} disabled={busy}>关闭</button>
          <button type="button" className="btn" onClick={() => void test()} disabled={busy}>测试连接</button>
          <button type="button" className="btn btnPrimary" onClick={() => void save()} disabled={busy}>
            {busy ? "处理中…" : "保存设置"}
          </button>
        </>
      }
    >
      <div className="stack">
        <p className={`badge ${configured?.configured ? "badgeSuccess" : "badgeWarning"}`} role="status">
          {configured?.configured ? "当前已配置模型" : "当前未配置模型，聊天只会返回规则摘要或明确失败"}
        </p>
        <label className="field">
          <span className="fieldLabel">提供方（可选）</span>
          <input className="input" value={provider} onChange={(event) => setProvider(event.target.value)} placeholder="例如 openai_compatible" />
        </label>
        <label className="field">
          <span className="fieldLabel">接口地址</span>
          <input className="input" value={url} onChange={(event) => setUrl(event.target.value)} placeholder="https://…/v1" autoComplete="off" />
        </label>
        <label className="field">
          <span className="fieldLabel">模型编号</span>
          <input className="input" value={modelId} onChange={(event) => setModelId(event.target.value)} placeholder="例如 deepseek-chat" autoComplete="off" />
        </label>
        <label className="field">
          <span className="fieldLabel">API 密钥</span>
          <input
            className="input"
            type="password"
            value={apiKey}
            onChange={(event) => setApiKey(event.target.value)}
            placeholder="留空表示保留已保存的密钥"
            autoComplete="new-password"
          />
          <span className="fieldHint">已保存的密钥不会显示；需要更换时直接输入新密钥。</span>
        </label>
        <label className="checkRow">
          <input type="checkbox" checked={clearKey} onChange={(event) => setClearKey(event.target.checked)} />
          清除已保存的密钥
        </label>
        <div className="field">
          <span className="fieldLabel">外发授权</span>
          <label className="checkRow">
            <input
              type="checkbox"
              checked={allowExternal}
              onChange={(event) => setAllowExternal(event.target.checked)}
            />
            允许把赛方内容发送到远端模型
          </label>
          <span className="fieldHint">
            本地模型（127.0.0.1 / localhost）不需要这个授权；关闭后远端模型调用会被拒绝。
          </span>
        </div>
        {message ? <p className="small" role="status">{message}</p> : null}
        {testResult ? <p className="small" role="status">{testResult}</p> : null}
        {error ? (
          <p className="fieldError" role="alert">
            {error.text}
            {error.requestId ? `（请求编号 ${error.requestId}）` : ""}
          </p>
        ) : null}
      </div>
    </Modal>
  );
}
