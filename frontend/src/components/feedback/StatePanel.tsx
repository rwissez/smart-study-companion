import type { ReactNode } from "react";
import styles from "../../styles/ui.module.css";

type Kind = "loading" | "empty" | "error" | "info";

interface Props {
  kind: Kind;
  title?: string;
  message?: ReactNode;
  onRetry?: () => void;
  action?: ReactNode;
}

const defaultTitle: Record<Kind, string> = {
  loading: "正在加载",
  empty: "暂无可用内容",
  error: "加载失败",
  info: "提示",
};

/**
 * 页面与局部的状态块：加载、空数据、失败、说明。
 * 失败时就地给出重试，不保留上一范围的旧内容。
 */
export function StatePanel({ kind, title, message, onRetry, action }: Props) {
  const className = [
    styles.state,
    kind === "error" ? styles.stateError : "",
    kind === "empty" ? styles.stateEmpty : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div className={className} role={kind === "error" ? "alert" : "status"}>
      <span className={styles.stateTitle}>{title ?? defaultTitle[kind]}</span>
      {message ? <span>{message}</span> : null}
      {onRetry || action ? (
        <div className={styles.row}>
          {onRetry ? (
            <button type="button" className={styles.button} onClick={onRetry}>
              重试
            </button>
          ) : null}
          {action}
        </div>
      ) : null}
    </div>
  );
}
