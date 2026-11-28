import { useQuery } from "@tanstack/react-query";
import { toErrorMessage } from "../../api/client";
import { getModelSettings } from "../../api/service";
import { queryKeys } from "../../lib/queryKeys";
import { useSession } from "../../app/session";
import type { ModelSettings } from "../../api/types";
import { IconAlert, IconCheck } from "../icons";

interface Props {
  onConfigure?: () => void;
}

/**
 * 右上角的小状态标记：已配置显示勾，未配置显示感叹号并可打开
 * 模型设置/外发授权弹窗。不占用回答正文的横向空间。
 */
export function ModelStatus({ onConfigure }: Props) {
  const { scopeKey } = useSession();
  const settings = useQuery<ModelSettings>({
    queryKey: queryKeys.modelSettings(scopeKey),
    queryFn: getModelSettings,
  });

  if (settings.isPending) {
    return (
      <span className="modelMark" role="status" aria-label="正在读取模型配置状态">
        …
      </span>
    );
  }
  if (settings.isError) {
    return (
      <button
        type="button"
        className="modelMark modelMarkWarn"
        onClick={onConfigure}
        title={`模型配置状态读取失败：${toErrorMessage(settings.error)}`}
        aria-label="模型配置状态读取失败，打开模型设置"
      >
        <IconAlert size={14} />
        <span>状态未知</span>
      </button>
    );
  }
  const data = settings.data;
  if (data?.configured) {
    // 已配置同样可以点击：教师需要能随时回来更换提供方、地址或密钥。
    return (
      <button
        type="button"
        className="modelMark modelMarkOk"
        onClick={onConfigure}
        title={`模型服务已配置：${data.provider ?? "未标注提供方"} · ${data.model_id ?? "未标注模型"}${data.has_api_key ? " · 已保存密钥，不回显" : ""}（点击可更换模型、地址或密钥）`}
        aria-label="模型已配置，打开模型设置"
      >
        <IconCheck size={14} />
        <span>模型已配置</span>
      </button>
    );
  }
  return (
    <button
      type="button"
      className="modelMark modelMarkWarn"
      onClick={onConfigure}
      title="模型未配置：点击打开模型设置与外发授权"
      aria-label="模型未配置，打开模型设置与外发授权"
    >
      <IconAlert size={14} />
      <span>模型未配置</span>
    </button>
  );
}
