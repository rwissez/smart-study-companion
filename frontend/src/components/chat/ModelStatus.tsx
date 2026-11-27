import { useQuery } from "@tanstack/react-query";
import { toErrorMessage } from "../../api/client";
import { getModelSettings } from "../../api/service";
import { queryKeys } from "../../lib/queryKeys";
import { useSession } from "../../app/session";
import type { ModelSettings } from "../../api/types";

interface Props {
  onConfigure?: () => void;
}

/** 未配置时给出真实原因与下一步，不伪造回答，也不把空文本当模型成果。 */
export function ModelStatus({ onConfigure }: Props) {
  const { scopeKey } = useSession();
  const settings = useQuery<ModelSettings>({
    queryKey: queryKeys.modelSettings(scopeKey),
    queryFn: getModelSettings,
  });

  if (settings.isPending) return <p className="small muted">正在读取模型配置状态…</p>;
  if (settings.isError) {
    return (
      <div className="chatNotice" role="status">
        模型配置状态读取失败：{toErrorMessage(settings.error)}
      </div>
    );
  }
  const data = settings.data;
  if (data?.configured) {
    return (
      <div className="chatNotice" role="status">
        模型服务已配置（{data.provider ?? "未标注提供方"} · {data.model_id ?? "未标注模型"}）
        {data.has_api_key ? " · 已保存密钥，不会回显" : ""}
      </div>
    );
  }
  return (
    <div className="chatNotice" role="status">
      <div className="rowBetween">
        <strong>模型服务尚未配置</strong>
        {onConfigure ? (
          <button type="button" className="btn btnSm" onClick={onConfigure}>配置模型</button>
        ) : null}
      </div>
      <p className="small" style={{ marginTop: 6 }}>
        对话接口已就绪，但服务端没有可用的模型配置；现在不会返回任何模型回答。教师配置模型后可以重新尝试。
      </p>
    </div>
  );
}
