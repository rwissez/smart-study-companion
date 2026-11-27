import { useEffect, useRef, type ReactNode } from "react";

interface Props {
  open: boolean;
  title: string;
  children: ReactNode;
  confirmLabel: string;
  cancelLabel?: string;
  onConfirm: () => void;
  onCancel: () => void;
  danger?: boolean;
  busy?: boolean;
}

/**
 * 确认卡：默认焦点在“取消”，接受与拒绝同等明确，不用回车默认接受。
 */
export function ConfirmDialog({
  open,
  title,
  children,
  confirmLabel,
  cancelLabel = "取消",
  onConfirm,
  onCancel,
  danger = false,
  busy = false,
}: Props) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    cancelRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onCancel();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      previous?.focus?.();
    };
  }, [open, onCancel]);

  if (!open) return null;

  return (
    <div className="modalOverlay" onMouseDown={(event) => { if (event.target === event.currentTarget) onCancel(); }}>
      <div ref={panelRef} className="modal" role="alertdialog" aria-modal="true" aria-label={title} style={{ width: "min(480px, calc(100vw - 64px))" }}>
        <div className="modalHead">
          <h2 className="modalTitle">{title}</h2>
        </div>
        <div className="modalBody">{children}</div>
        <div className="modalFoot">
          <button ref={cancelRef} type="button" className="btn" onClick={onCancel} disabled={busy}>
            {cancelLabel}
          </button>
          <button
            type="button"
            className={danger ? "btn btnDanger" : "btn btnPrimary"}
            onClick={onConfirm}
            disabled={busy}
          >
            {busy ? "处理中…" : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
