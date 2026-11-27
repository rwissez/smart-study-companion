import type { ReactNode } from "react";
import { IconAlert, IconClock, IconLock, IconRefresh } from "../icons";

export type StateKind = "loading" | "empty" | "error" | "not-ready" | "history" | "expired" | "forbidden";

interface Props {
  kind: StateKind;
  title: string;
  message?: ReactNode;
  requestId?: string | null;
  action?: ReactNode;
  onRetry?: () => void;
  retryLabel?: string;
}

const ICONS: Partial<Record<StateKind, ReactNode>> = {
  error: <IconAlert size={18} />,
  "not-ready": <IconClock size={18} />,
  expired: <IconClock size={18} />,
  history: <IconClock size={18} />,
  forbidden: <IconLock size={18} />,
};

/** 加载保留骨架布局；空、错误、未就绪、历史、过期各有明确文案。 */
export function StatePanel({ kind, title, message, requestId, action, onRetry, retryLabel = "重试" }: Props) {
  if (kind === "loading") {
    return (
      <div className="skeleton" role="status" aria-busy="true" aria-label={title}>
        <div className="skeletonLine" />
        <div className="skeletonLine" />
        <div className="skeletonLine" />
      </div>
    );
  }

  const tone = kind === "error" ? "stateError" : kind === "not-ready" || kind === "expired" ? "stateReady" : "";
  return (
    <div className={`statePanel ${tone}`.trim()} role={kind === "error" ? "alert" : "status"}>
      <div className="row" style={{ gap: 8 }}>
        {ICONS[kind] ? <span className="muted" style={{ display: "inline-flex" }}>{ICONS[kind]}</span> : null}
        <span className="stateTitle">{title}</span>
      </div>
      {message ? <div>{message}</div> : null}
      {requestId ? <div className="xs">请求编号：{requestId}</div> : null}
      {(action || onRetry) && (
        <div className="stateActions">
          {action}
          {onRetry ? (
            <button type="button" className="btn btnSm" onClick={onRetry}>
              <IconRefresh size={16} />
              {retryLabel}
            </button>
          ) : null}
        </div>
      )}
    </div>
  );
}
