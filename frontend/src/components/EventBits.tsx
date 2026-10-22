import { EVENT_LABEL, SEVERITY_COLOR, SEVERITY_ICON, SEVERITY_LABEL } from "../types";
import type { SafetyEvent, Severity } from "../types";

export function SeverityBadge({ severity }: { severity: Severity }) {
  return (
    <span className="inline-flex items-center gap-1 whitespace-nowrap" style={{ color: SEVERITY_COLOR[severity] }}>
      <span aria-hidden>{SEVERITY_ICON[severity]}</span>
      {SEVERITY_LABEL[severity]}
    </span>
  );
}

export function formatTime(iso: string): string {
  return new Date(iso).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function EventRow({ event }: { event: SafetyEvent }) {
  return (
    <div className="border-b border-line px-3 py-2" style={{ borderLeft: `3px solid ${SEVERITY_COLOR[event.severity]}` }}>
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium">
          <span aria-hidden style={{ color: SEVERITY_COLOR[event.severity] }}>{SEVERITY_ICON[event.severity]} </span>
          {EVENT_LABEL[event.type]}
        </span>
        <span className="text-xs text-muted">{event.status === "open" ? "идёт" : "закрыто"}</span>
      </div>
      <div className="text-xs text-muted">
        {formatTime(event.started_at)} · {event.camera_name}
        {event.zone_name ? ` · ${event.zone_name}` : ""}
      </div>
      {event.snapshot_url && (
        <a href={event.clip_url ?? event.snapshot_url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}
           title={event.clip_url ? "Открыть клип" : "Открыть стоп-кадр"} className="relative mt-1 block">
          <img src={event.snapshot_url} alt="Стоп-кадр нарушения" loading="lazy" className="w-full rounded border border-line" />
          {event.clip_url && <span className="absolute bottom-1 right-1 rounded bg-bg/80 px-1 text-xs">▶ клип</span>}
        </a>
      )}
    </div>
  );
}
