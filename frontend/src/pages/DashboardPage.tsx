import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { EmptyState, PageHeader } from "../App";
import { Icon } from "../components/Icons";
import { eventLabel, formatCount, formatDateTime, formatPercent, severityClass, severityLabel } from "../lib/format";
import type { AnalyticsSummary, Camera, EventPage, Health, SafetyEvent, Stats } from "../types";
import type { PageProps } from "./types";

export function DashboardPage({ navigate, refreshKey }: PageProps) {
  const [data, setData] = useState<{ events: EventPage; cameras: Camera[]; analytics: AnalyticsSummary; health: Health; stats: Stats } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.allSettled([api.events({ limit: 6 }), api.cameras(), api.analytics(), api.health(), api.stats()]).then((results) => {
      if (cancelled) return;
      const [events, cameras, analytics, health, stats] = results;
      const rejected = results.find((result) => result.status === "rejected");
      if (rejected?.status === "rejected") setError(rejected.reason instanceof Error ? rejected.reason.message : "Не удалось загрузить состояние системы");
      if (events.status === "fulfilled" && cameras.status === "fulfilled" && analytics.status === "fulfilled" && health.status === "fulfilled" && stats.status === "fulfilled") {
        setData({ events: events.value, cameras: cameras.value, analytics: analytics.value, health: health.value, stats: stats.value });
        setError(null);
      }
    });
    return () => { cancelled = true; };
  }, [refreshKey]);

  const openEvents = data?.events.items.filter((event) => event.status === "open").length ?? 0;
  const onlineCameras = data?.cameras.filter((camera) => camera.status === "online").length ?? 0;
  const falseShare = data?.analytics.false_share;
  const topCamera = useMemo(() => data?.analytics.by_camera[0], [data]);
  const healthProblem = data?.health.problems[0];

  return (
    <div className="page-stack">
      <PageHeader title="Обзор" description="Состояние источников и сигналы, требующие внимания оператора." action={<button className="button button-primary" type="button" onClick={() => navigate("events")}><Icon name="alert" size={16} />Открыть журнал</button>} />
      {error && <ErrorNotice message={error} />}
      <section className="overview-status" aria-label="Состояние системы">
        <div className="overview-status-main"><span className={`status-dot ${healthProblem ? "status-dot-warm" : "status-dot-live"}`} /><div><span className="table-caption">Состояние системы</span><strong>{!data ? "Синхронизация…" : healthProblem ? "Нужна проверка" : "Работает штатно"}</strong></div></div>
        <div className="overview-facts">
          <div><span>Сигналы</span><strong className={openEvents ? "text-danger" : ""}>{data ? formatCount(openEvents) : "—"}</strong><small>открыто</small></div>
          <div><span>Камеры</span><strong className="text-green">{data ? `${onlineCameras}/${data.cameras.length}` : "—"}</strong><small>в сети</small></div>
          <div><span>Ложные</span><strong>{formatPercent(falseShare)}</strong><small>размечено</small></div>
          <div><span>Детектор</span><strong>{data?.stats.detector ?? "—"}</strong><small>{data?.stats.device ?? "устройство"}</small></div>
        </div>
      </section>
      <div className="overview-workspace">
        <section className="panel overview-events"><div className="section-heading"><div><span className="table-caption">Последние 6</span><h2>Журнал сигналов</h2></div><button className="text-button" type="button" onClick={() => navigate("events")}>Весь журнал <Icon name="arrow" size={15} /></button></div>{data?.events.items.length ? <div className="event-list compact-list">{data.events.items.slice(0, 6).map((event) => <EventRow key={event.id} event={event} onClick={() => navigate("events")} />)}</div> : <EmptyState icon="alert" title="Сигналов нет" description="Новые события появятся здесь автоматически." />}</section>
        <section className="panel overview-sources"><div className="section-heading"><div><span className="table-caption">Источники</span><h2>Камеры</h2></div><button className="text-button" type="button" onClick={() => navigate("cameras")}>Управление <Icon name="arrow" size={15} /></button></div>{data?.cameras.length ? <div className="source-list">{data.cameras.slice(0, 5).map((camera) => <CameraMini key={camera.id} camera={camera} />)}</div> : <EmptyState icon="camera" title="Источники не подключены" description="Добавьте камеру, чтобы открыть live-контур." action={<button className="button button-secondary" type="button" onClick={() => navigate("cameras")}><Icon name="plus" size={15} />Добавить камеру</button>} />}{topCamera && <div className="source-note"><span>Больше всего событий</span><strong>{topCamera.camera_name} · {topCamera.total}</strong></div>}</section>
      </div>
      {healthProblem && <section className="overview-alert" role="status"><Icon name="alert" size={17} /><div><strong>Система сообщает об отклонении</strong><span>{healthProblem}</span></div><button className="text-button" type="button" onClick={() => navigate("settings")}>Диагностика <Icon name="arrow" size={15} /></button></section>}
    </div>
  );
}

function EventRow({ event, onClick }: { event: SafetyEvent; onClick: () => void }) { return <button className="event-row" type="button" onClick={onClick}><span className={`severity-marker ${severityClass(event.severity)}`} /><span className="event-row-main"><strong>{eventLabel(event.type)}</strong><span>{event.camera_name}{event.zone_name ? ` · ${event.zone_name}` : ""}</span></span><span className="event-row-time">{formatDateTime(event.started_at)}</span><span className={`status-chip ${severityClass(event.severity)}`}>{severityLabel(event.severity)}</span><Icon name="chevron" size={15} className="event-row-chevron" /></button>; }
function CameraMini({ camera }: { camera: Camera }) { return <div className="source-row"><span className={`camera-live-dot ${camera.status === "online" ? "is-online" : ""}`} /><div className="source-row-main"><strong>{camera.name}</strong><span>{camera.source_type === "rtsp" ? "RTSP-поток" : "Видео-файл"}</span></div><span className={`source-state ${camera.status === "online" ? "source-state-online" : ""}`}>{camera.status === "online" ? "В сети" : "Offline"}</span><span className={camera.open_events ? "camera-events camera-events-hot" : "camera-events"}>{camera.open_events || "—"}</span></div>; }
function ErrorNotice({ message }: { message: string }) { return <div className="error-notice" role="alert"><Icon name="alert" size={17} /><span>{message}</span><span className="error-hint">Проверьте доступность backend на порту 8000.</span></div>; }
