import { useEffect, useState } from "react";
import { api, errorText } from "../api";
import { Sign } from "../components/Sign";
import { Dot, Empty, Loading, Notice, PageHeader, SeverityChip } from "../components/Ui";
import { usePoll } from "../hooks/usePoll";
import { CAM_STATE_LABEL, camState } from "../lib/cameras";
import { formatCount, formatDateTime, formatDuration, formatPercent, localDayStart, plural } from "../lib/format";
import { linkHandler } from "../lib/router";
import { EVENT_LABEL, type AnalyticsSummary, type Camera, type Health, type SafetyEvent, type Stats } from "../types";

function today(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export function OverviewPage({ tick }: { tick: number }) {
  const [open, setOpen] = useState<SafetyEvent[] | null>(null);
  const [recent, setRecent] = useState<SafetyEvent[]>([]);
  const [day, setDay] = useState<AnalyticsSummary | null>(null);
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    Promise.all([
      api.events({ status: "open", limit: 10 }),
      api.events({ limit: 6 }),
      api.analytics({ from: localDayStart(today()), tz_offset_min: -new Date().getTimezoneOffset() }),
    ]).then(([o, r, a]) => {
      if (!alive) return;
      setOpen(o.items);
      setRecent(r.items);
      setDay(a);
      setError(null);
    }).catch((e) => alive && setError(errorText(e, "Не удалось загрузить нарушения")));
    return () => {
      alive = false;
    };
  }, [tick]);

  usePoll(() => {
    api.cameras().then(setCameras).catch(() => undefined);
    api.health().then(setHealth).catch(() => undefined);
    api.stats().then(setStats).catch(() => undefined);
  }, 5000);

  const states = cameras.map((c) => camState(c, health));
  const analysing = states.filter((s) => s === "live").length;
  const enabled = cameras.filter((c) => c.enabled).length;
  const latency = stats?.latency?.capture_to_event;

  return (
    <div className="page">
      <PageHeader title="Обзор смены" />
      {error && <Notice>{error}</Notice>}

      <section className="status-line" aria-label="Состояние системы">
        <span><Dot state={health?.worker?.ok ? "live" : "down"} />
          {health?.worker?.ok ? `Анализ идёт на ${analysing} из ${enabled} ${plural(enabled, "камеры", "камер", "камер")}` : "Воркер не отвечает, анализа нет"}
        </span>
        {stats?.detector && <span>Детектор {stats.detector}, {stats.device}</span>}
        {latency && <span>От кадра до события {latency.avg_ms} мс</span>}
        {health?.problems.filter((p) => p !== "воркер не отвечает").map((p) => <span key={p} className="status-problem">{p}</span>)}
        <a href="/performance" onClick={linkHandler("/performance")}>Подробнее</a>
      </section>

      <div className="overview">
        <div className="overview-main">
          <section className="panel">
            <h2 className="panel-title">Идут сейчас{open?.length ? ` · ${open.length}` : ""}</h2>
            {open === null ? <Loading /> : open.length > 0 ? <EventList events={open} live /> : <Empty title="Открытых нарушений нет" />}
          </section>
          {recent.some((e) => e.status === "closed") && (
            <section className="panel">
              <h2 className="panel-title">Последние нарушения</h2>
              <EventList events={recent.filter((e) => e.status === "closed")} />
            </section>
          )}
        </div>

        <div className="overview-side">
          <section className="panel">
            <h2 className="panel-title">Сегодня</h2>
            {!day ? <Loading /> : day.total === 0 ? <Empty title="Сегодня нарушений не было" /> : (
              <>
                <p className="today-sum">
                  {formatCount(day.total)} {plural(day.total, "нарушение", "нарушения", "нарушений")}, разобрано {formatCount(day.total - day.unreviewed)},
                  ложных {formatPercent(day.false_share)}
                </p>
                <ul className="type-bars">
                  {day.by_type.filter((t) => t.total > 0).sort((a, b) => b.total - a.total).map((t) => (
                    <li key={t.type}>
                      <Sign type={t.type} size={20} />
                      <span>{EVENT_LABEL[t.type]}</span>
                      <i style={{ width: `${(t.total / day.by_type.reduce((m, x) => Math.max(m, x.total), 1)) * 100}%` }} />
                      <b className="num">{t.total}</b>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </section>
          <section className="panel">
            <h2 className="panel-title">Камеры</h2>
            <ul className="cam-list">
              {cameras.map((c, i) => (
                <li key={c.id}>
                  <a href={`/cameras?camera=${c.id}`} onClick={linkHandler(`/cameras?camera=${c.id}`)}>
                    <Dot state={states[i]} />
                    <span><strong>{c.name}</strong><small>{CAM_STATE_LABEL[states[i]]}</small></span>
                    {c.open_events > 0 && <b className="count-hot">{c.open_events}</b>}
                  </a>
                </li>
              ))}
            </ul>
          </section>
        </div>
      </div>
    </div>
  );
}

function EventList({ events, live }: { events: SafetyEvent[]; live?: boolean }) {
  return (
    <ul className="event-list">
      {events.map((e) => (
        <li key={e.id}>
          <a href={`/events?event=${e.id}`} onClick={linkHandler(`/events?event=${e.id}`)} className={`sev-row sev-row-${e.severity}`}>
            <Sign type={e.type} size={28} />
            <span className="event-list-main">
              <strong>{EVENT_LABEL[e.type]}</strong>
              <small>{e.camera_name}{e.zone_name ? `, ${e.zone_name}` : ""}</small>
            </span>
            <span className="num event-list-time">{live ? `идёт ${formatDuration(e.started_at, null)}` : formatDateTime(e.started_at)}</span>
            <SeverityChip severity={e.severity} />
          </a>
        </li>
      ))}
    </ul>
  );
}
