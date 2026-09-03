import { AlertTriangle, CheckCircle2, Info, X } from "lucide-react";
import { type ReactNode, useEffect, useRef } from "react";
import { titleCase } from "../lib/format";
import type { EventRecord, UserAccount } from "../types";

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description: string;
  actions?: ReactNode;
}) {
  return (
    <header className="page-header">
      <div>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {actions ? <div className="page-actions">{actions}</div> : null}
    </header>
  );
}

export function StatusTag({ status }: { status: EventRecord["status"] | string }) {
  return <span className={`status-tag status-${status}`}>{titleCase(status)}</span>;
}

export function Avatar({ user, size = "md" }: { user: UserAccount; size?: "sm" | "md" | "lg" }) {
  const short = user.name
    .split(" ")
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0])
    .join("")
    .toUpperCase();
  return user.avatar_url ? (
    <img className={`avatar avatar-${size}`} src={user.avatar_url} alt={user.name} />
  ) : (
    <span className={`avatar avatar-${size} avatar-fallback`} title={user.name}>
      {short}
    </span>
  );
}

export function Readiness({ value }: { value: number }) {
  const tone = value >= 90 ? "good" : value >= 65 ? "warn" : "danger";
  return (
    <div className="readiness" aria-label={`${value}% ready`}>
      <strong>{value}%</strong>
      <span className="readiness-track"><span className={tone} style={{ width: `${value}%` }} /></span>
    </div>
  );
}

export function EmptyState({ title, message, action }: { title: string; message: string; action?: ReactNode }) {
  return (
    <div className="empty-state">
      <div className="empty-icon"><Info size={20} /></div>
      <strong>{title}</strong>
      <p>{message}</p>
      {action}
    </div>
  );
}

export function Modal({
  open,
  title,
  description,
  children,
  onClose,
  size = "md",
}: {
  open: boolean;
  title: string;
  description?: string;
  children: ReactNode;
  onClose: () => void;
  size?: "sm" | "md" | "lg";
}) {
  const panelRef = useRef<HTMLElement>(null);
  const closeHandler = useRef(onClose);
  closeHandler.current = onClose;

  useEffect(() => {
    if (!open) return;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const panel = panelRef.current;
    panel?.querySelector<HTMLElement>("button, input, select, textarea, a[href], [tabindex]:not([tabindex='-1'])")?.focus();

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        closeHandler.current();
        return;
      }
      if (event.key !== "Tab" || !panel) return;
      const controls = [...panel.querySelectorAll<HTMLElement>("button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href], [tabindex]:not([tabindex='-1'])")];
      if (!controls.length) return;
      const first = controls[0];
      const last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      previousFocus?.focus();
    };
  }, [open]);

  if (!open) return null;
  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(event) => {
      if (event.currentTarget === event.target) onClose();
    }}>
      <section ref={panelRef} className={`modal-panel modal-${size}`} role="dialog" aria-modal="true" aria-labelledby="modal-title" aria-describedby={description ? "modal-description" : undefined}>
        <header className="modal-head">
          <div>
            <h2 id="modal-title">{title}</h2>
            {description ? <p id="modal-description">{description}</p> : null}
          </div>
          <button className="icon-button" type="button" onClick={onClose} aria-label="Close dialog" title="Close">
            <X size={19} />
          </button>
        </header>
        <div className="modal-body">{children}</div>
      </section>
    </div>
  );
}

export function ConflictState({ count }: { count: number }) {
  return count > 0 ? (
    <span className="conflict-state conflict-open"><AlertTriangle size={15} />{count} item{count === 1 ? "" : "s"}</span>
  ) : (
    <span className="conflict-state conflict-clear"><CheckCircle2 size={15} />None</span>
  );
}

export function Spinner({ label = "Working" }: { label?: string }) {
  return <span className="spinner" role="status" aria-label={label} />;
}
