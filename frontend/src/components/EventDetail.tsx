import { useEffect, useState, type ReactNode } from "react";
import { api, errorText } from "../api";
import { formatDuration, formatPercent, formatTime, formatDateTime } from "../lib/format";
import { linkHandler } from "../lib/router";
import { EVENT_LABEL, type Review, type SafetyEvent } from "../types";
import { Icon } from "./Icons";
import { Sign } from "./Sign";
import { Notice, ReviewChip, SeverityChip } from "./Ui";

type View = "snapshot" | "clean" | "clip";

const REVIEW_ACTIONS: { review: Review; label: string }[] = [
  { review: "confirmed", label: "Подтвердить" },
  { review: "false_positive", label: "Ложное" },
  { review: "forwarded", label: "Передать мастеру" },
];

const DETAIL_LABEL: Record<string, string> = {
  fraction: "Доля кадров с нарушением",
  window_s: "Окно, с",
  observations: "Наблюдений",
  closed_by: "Закрыто",
};

function detailValue(key: string, value: unknown): string {
  if (key === "closed_by" && value === "worker_restart") return "при перезапуске воркера";
  if (key === "fraction" && typeof value === "number") return formatPercent(value);
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

export function EventDetail({ event, onChange, onClose }: { event: SafetyEvent; onChange: (e: SafetyEvent) => void; onClose?: () => void }) {
  const [view, setView] = useState<View>("snapshot");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState("");

  useEffect(() => {
    setView("snapshot");
    setError(null);
    setNote("");
  }, [event.id]);

  const media: Record<View, string | null> = { snapshot: event.snapshot_url, clean: event.snapshot_clean_url, clip: event.clip_url };
  const current: View | null = media[view] ? view : (["snapshot", "clean", "clip"] as View[]).find((v) => media[v]) ?? null;

  const mark = async (review: Review) => {
    setBusy(true);
    try {
      const next = event.review === review ? "none" : review;
      onChange(await api.reviewEvent(event.id, next, next === "none" ? undefined : note.trim() || undefined));
      setNote("");
      setError(null);
    } catch (e) {
      setError(errorText(e, "Не удалось сохранить разбор"));
    } finally {
      setBusy(false);
    }
  };

  const cameraLink = `/cameras?camera=${event.camera_id}`;
  const facts: [string, ReactNode][] = [
    ["Разбор", <ReviewChip review={event.review} />],
    ...(event.reviewed_by || event.reviewed_at
      ? [["Разобрал", `${event.reviewed_by ?? "—"}${event.reviewed_at ? `, ${formatDateTime(event.reviewed_at)}` : ""}`] as [string, ReactNode]]
      : []),
    ...(event.review_note ? [["Комментарий", event.review_note] as [string, ReactNode]] : []),
    ["Камера", <a href={cameraLink} onClick={linkHandler(cameraLink)}>{event.camera_name}</a>],
    ["Зона", event.zone_name ?? "вне зон"],
    ["Начало", formatTime(event.started_at)],
    ["Конец", event.ended_at ? formatTime(event.ended_at) : "идёт"],
    ["Длительность", formatDuration(event.started_at, event.ended_at)],
    ["Уверенность", formatPercent(event.confidence)],
    ["Человек на камере", event.track_id !== null ? `№ ${event.track_id}` : "—"],
    ...Object.entries(event.details).map(([key, value]): [string, ReactNode] => [DETAIL_LABEL[key] ?? key, detailValue(key, value)]),
  ];

  return (
    <article className="detail" aria-label={`Нарушение: ${EVENT_LABEL[event.type]}`}>
      <header className="detail-head">
        <Sign type={event.type} size={34} />
        <div>
          <h2>{EVENT_LABEL[event.type]}</h2>
          <p>{event.camera_name}{event.zone_name ? `, ${event.zone_name}` : ""} · {formatDateTime(event.started_at)}</p>
        </div>
        <SeverityChip severity={event.severity} />
        {onClose && (
          <button type="button" className="icon-btn" aria-label="Закрыть" onClick={onClose}>
            <Icon name="close" />
          </button>
        )}
      </header>

      <div className="detail-tabs" role="tablist">
        {([["snapshot", "Кадр"], ["clean", "Без рамок"], ["clip", event.clip_url || event.status === "closed" ? "Клип" : "Клип готовится"]] as [View, string][]).map(([key, label]) => (
          <button key={key} type="button" role="tab" aria-selected={current === key} disabled={!media[key]} onClick={() => setView(key)}>
            {label}
          </button>
        ))}
      </div>
      <div className="detail-media">
        {!current && <span>Кадр не сохранён</span>}
        {current === "clip" && <video key={media.clip} src={media.clip!} controls autoPlay loop muted playsInline />}
        {current && current !== "clip" && (
          <a href={media[current]!} target="_blank" rel="noreferrer" title="Открыть в полном размере">
            <img src={media[current]!} alt={`${EVENT_LABEL[event.type]}, ${event.camera_name}`} />
          </a>
        )}
      </div>
      {event.clip_anon_url && (
        <a className="btn btn-ghost" href={event.clip_anon_url} target="_blank" rel="noreferrer" download>
          Клип без лиц
        </a>
      )}

      <div className="detail-review">
        <span>Разбор</span>
        <div>
          {REVIEW_ACTIONS.map((a) => (
            <button key={a.review} type="button" disabled={busy} aria-pressed={event.review === a.review}
              className={`btn review-btn review-btn-${a.review}`} onClick={() => mark(a.review)}>
              {event.review === a.review && <Icon name="check" size={15} />}
              {a.label}
            </button>
          ))}
        </div>
        <textarea value={note} maxLength={500} rows={2} placeholder="Комментарий к разбору (необязательно)"
          aria-label="Комментарий к разбору" onChange={(e) => setNote(e.target.value)} />
      </div>
      {error && <Notice>{error}</Notice>}

      <dl className="facts">
        {facts.map(([label, value]) => (
          <div key={label}><dt>{label}</dt><dd>{value}</dd></div>
        ))}
      </dl>
    </article>
  );
}
