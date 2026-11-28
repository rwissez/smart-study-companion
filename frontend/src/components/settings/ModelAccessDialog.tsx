import { useEffect, useState } from "react";
import { errorRequestId, toErrorMessage } from "../../api/client";
import { getModelSettings, saveModelSettings } from "../../api/service";
import type { ModelSettings } from "../../api/types";
import { ConfirmDialog } from "../ui/ConfirmDialog";

interface Props {
  open: boolean;
  onClose: () => void;
}

export function ModelAccessDialog({ open, onClose }: Props) {
  const [settings, setSettings] = useState<ModelSettings | null>(null);
  const [allowed, setAllowed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ text: string; requestId?: string | null } | null>(null);

  useEffect(() => {
    if (!open) return;
    setError(null);
    getModelSettings()
      .then((value) => {
        setSettings(value);
        setAllowed(value.allow_external_model_input);
      })
      .catch((reason) => setError({ text: toErrorMessage(reason), requestId: errorRequestId(reason) }));
  }, [open]);

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      await saveModelSettings({ allow_external_model_input: allowed });
      onClose();
    } catch (reason) {
      setError({ text: toErrorMessage(reason), requestId: errorRequestId(reason) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <ConfirmDialog
      open={open}
      title="模型与外发授权"
      confirmLabel="保存"
      cancelLabel="取消"
      onConfirm={() => void save()}
      onCancel={onClose}
      busy={busy}
    >
      <div className="stackSm">
        <p className="small">
          当前模型：{settings?.url || "未配置"} {settings?.model_id ? `· ${settings.model_id}` : ""}
        </p>
        <p className="small muted">
          远端模型会把题目、学生作答和教材片段发送到模型服务商。只有确认这些内容允许发给该服务商时才打开。
          本地模型（127.0.0.1 / localhost）不需要这个授权。
        </p>
        <label className="row" style={{ gap: 8, alignItems: "flex-start" }}>
          <input
            type="checkbox"
            checked={allowed}
            onChange={(event) => setAllowed(event.target.checked)}
            disabled={busy}
          />
          <span>
            允许把赛方内容发送到远端模型
            <br />
            <span className="xs muted">关闭后，远端模型调用会被拒绝；本地模型不受影响。</span>
          </span>
        </label>
        {error ? (
          <p className="fieldError" role="alert">
            {error.text}
            {error.requestId ? `（请求编号 ${error.requestId}）` : ""}
          </p>
        ) : null}
      </div>
    </ConfirmDialog>
  );
}
