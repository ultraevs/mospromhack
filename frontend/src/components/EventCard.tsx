import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { formatDuration } from "../lib/journal";
import { EVENT_LABEL, REVIEW_LABEL } from "../types";
import type { Review, SafetyEvent } from "../types";
import { formatTime, SeverityBadge } from "./EventBits";

const DETAIL_LABEL: Record<string, string> = {
  fraction: "Доля кадров",
  window_s: "Окно, с",
  observations: "Наблюдений",
  closed_by: "Закрыто",
};

function detailValue(key: string, value: unknown): string {
  if (key === "closed_by" && value === "worker_restart") return "при перезапуске воркера";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

function Field({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-3 border-b border-line py-1.5">
      <span className="text-muted">{title}</span>
      <span className="text-right">{children}</span>
    </div>
  );
}

export function EventCard({ event, onChange, onClose }: {
  event: SafetyEvent;
  onChange: (event: SafetyEvent) => void;
  onClose: () => void;
}) {
  const [view, setView] = useState<"snapshot" | "clean" | "clip">("snapshot");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const mark = async (review: Review) => {
    setBusy(true);
    try {
      onChange(await api.reviewEvent(event.id, event.review === review ? "none" : review));
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const views: ["snapshot" | "clean" | "clip", string, string | null][] = [
    ["snapshot", "Стоп-кадр", event.snapshot_url],
    ["clean", "Без рамок", event.snapshot_clean_url],
    ["clip", "Клип", event.clip_url],
  ];
  const current = views.find(([key, , url]) => key === view && url) ?? views.find(([, , url]) => url);

  return (
    <aside className="flex w-[460px] shrink-0 flex-col border-l border-line bg-panel" data-testid="event-card">
      <div className="flex items-center gap-2 border-b border-line px-3 py-2">
        <span className="font-semibold">{EVENT_LABEL[event.type]}</span>
        <SeverityBadge severity={event.severity} />
        <button onClick={onClose} className="ml-auto text-muted hover:text-text" title="Закрыть карточку">✕</button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        <div className="mb-2 flex gap-1 text-xs">
          {views.map(([key, title, url]) => (
            <button key={key} disabled={!url} onClick={() => setView(key)}
                    className={`rounded border px-2 py-1 disabled:opacity-40 ${current?.[0] === key ? "border-accent text-text" : "border-line text-muted"}`}>
              {title}{key === "clip" && !url && event.status === "open" ? " (готовится)" : ""}
            </button>
          ))}
        </div>
        <div className="mb-3 aspect-video overflow-hidden rounded border border-line bg-bg">
          {!current && <div className="flex h-full items-center justify-center text-muted">Доказательств нет</div>}
          {current && current[0] === "clip" && (
            <video key={current[2]} src={current[2]!} controls autoPlay loop muted className="h-full w-full object-contain" />
          )}
          {current && current[0] !== "clip" && (
            <a href={current[2]!} target="_blank" rel="noreferrer" title="Открыть в полном размере">
              <img src={current[2]!} alt={`${current[1]}: ${EVENT_LABEL[event.type]}`} className="h-full w-full object-contain" />
            </a>
          )}
        </div>

        <div className="mb-3 flex gap-2">
          <button disabled={busy} onClick={() => mark("confirmed")}
                  className={`flex-1 rounded border px-3 py-1.5 disabled:opacity-40 ${event.review === "confirmed" ? "border-ok bg-ok/15 text-ok" : "border-line hover:border-ok"}`}>
            ✔ Подтвердить
          </button>
          <button disabled={busy} onClick={() => mark("false_positive")}
                  className={`flex-1 rounded border px-3 py-1.5 disabled:opacity-40 ${event.review === "false_positive" ? "border-medium bg-medium/15 text-medium" : "border-line hover:border-medium"}`}>
            ✖ Ложное срабатывание
          </button>
          <button disabled={busy} onClick={() => mark("forwarded")}
                  className={`flex-1 rounded border px-3 py-1.5 disabled:opacity-40 ${event.review === "forwarded" ? "border-accent bg-accent/15 text-accent" : "border-line hover:border-accent"}`}>
            ➜ Мастеру
          </button>
        </div>
        {error && <div className="mb-2 text-high">▲ {error}</div>}

        <div className="text-sm">
          <Field title="Отметка">{REVIEW_LABEL[event.review]}</Field>
          <Field title="Камера"><Link className="underline" to={`/cameras/${event.camera_id}`}>{event.camera_name}</Link></Field>
          <Field title="Зона">{event.zone_name ?? "—"}</Field>
          <Field title="Начало">{formatTime(event.started_at)}</Field>
          <Field title="Конец">{event.ended_at ? formatTime(event.ended_at) : "—"}</Field>
          <Field title="Длительность">{formatDuration(event.started_at, event.ended_at)}</Field>
          <Field title="Состояние">{event.status === "open" ? "идёт" : "закрыто"}</Field>
          <Field title="Уверенность">{event.confidence.toFixed(2)}</Field>
          <Field title="Номер человека на камере">{event.track_id ?? "—"}</Field>
          {Object.entries(event.details).map(([key, value]) => (
            <Field key={key} title={DETAIL_LABEL[key] ?? key}>{detailValue(key, value)}</Field>
          ))}
          <Field title="Идентификатор"><code className="text-xs">{event.id}</code></Field>
        </div>
      </div>
    </aside>
  );
}
