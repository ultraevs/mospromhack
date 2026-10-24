import { useEffect, useId, useRef, type ReactNode } from "react";
import type { CamState } from "../lib/cameras";
import { REVIEW_LABEL, SEVERITY_LABEL, type Review, type Severity } from "../types";
import { Icon } from "./Icons";

export function PageHeader({ title, description, actions }: { title: string; description?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="page-header">
      <div>
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </header>
  );
}

export function Empty({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty">
      <strong>{title}</strong>
      {children && <p>{children}</p>}
      {action}
    </div>
  );
}

export function Notice({ tone = "error", children, onClose }: { tone?: "error" | "ok" | "warn"; children: ReactNode; onClose?: () => void }) {
  return (
    <div className={`notice notice-${tone}`} role={tone === "error" ? "alert" : "status"}>
      <span>{children}</span>
      {onClose && (
        <button type="button" className="icon-btn" aria-label="Скрыть" onClick={onClose}>
          <Icon name="close" size={15} />
        </button>
      )}
    </div>
  );
}

export function Loading({ label = "Загружаем…" }: { label?: string }) {
  return <div className="loading" role="status">{label}</div>;
}

export function Modal({ title, onClose, children, wide }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  const id = useId();
  const ref = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeRef.current();
    };
    document.addEventListener("keydown", onKey);
    ref.current?.querySelector<HTMLElement>("input, select, textarea, button:not(.icon-btn)")?.focus();
    return () => document.removeEventListener("keydown", onKey);
  }, []);
  return (
    <div className="modal-layer">
      <div className="modal-backdrop" onClick={onClose} />
      <div ref={ref} className={`modal ${wide ? "modal-wide" : ""}`} role="dialog" aria-modal="true" aria-labelledby={id}>
        <div className="modal-head">
          <h2 id={id}>{title}</h2>
          <button type="button" className="icon-btn" aria-label="Закрыть" onClick={onClose}>
            <Icon name="close" />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function Segmented<T extends string>({ value, options, onChange, label }: {
  value: T;
  options: { value: T; label: ReactNode }[];
  onChange: (value: T) => void;
  label: string;
}) {
  return (
    <div className="segmented" role="group" aria-label={label}>
      {options.map((o) => (
        <button key={o.value} type="button" aria-pressed={value === o.value} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Toggle({ checked, onChange, label, disabled }: { checked: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean }) {
  return (
    <button type="button" role="switch" aria-checked={checked} aria-label={label} disabled={disabled}
      className={`toggle ${checked ? "is-on" : ""}`} onClick={() => onChange(!checked)}>
      <span />
    </button>
  );
}

export function SeverityChip({ severity }: { severity: Severity }) {
  return <span className={`sev sev-${severity}`}>{SEVERITY_LABEL[severity]}</span>;
}

export function ReviewChip({ review }: { review: Review }) {
  return <span className={`review review-${review}`}>{REVIEW_LABEL[review]}</span>;
}

export function Figure({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="figure">
      <span>{label}</span>
      <strong className="num">{value}</strong>
      {note && <small>{note}</small>}
    </div>
  );
}

export function Dot({ state }: { state: CamState }) {
  return <span className={`dot dot-${state}`} aria-hidden="true" />;
}
