import { useState } from "react";
import { api, errorText } from "../api";
import { Figure, Loading, Notice, PageHeader } from "../components/Ui";
import { usePoll } from "../hooks/usePoll";
import type { Camera, Health, LatencySummary, Stats } from "../types";

const ms = (v?: number) => (v === undefined ? "—" : `${v} мс`);

export function PerformancePage() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [error, setError] = useState<string | null>(null);

  usePoll(() => {
    api.stats().then((s) => { setStats(s); setError(null); }).catch((e) => setError(errorText(e, "Нет данных о работе воркера")));
    api.health().then(setHealth).catch(() => undefined);
  }, 2000);
  usePoll(() => {
    api.cameras().then(setCameras).catch(() => undefined);
  }, 10000);

  if (!stats) return <div className="page"><PageHeader title="Производительность" />{error ? <Notice>{error}</Notice> : <Loading />}</div>;

  const names = new Map(cameras.map((c) => [String(c.id), c.name]));
  const lat = stats.latency?.capture_to_event ?? null;
  const tg = stats.latency?.capture_to_telegram ?? null;
  const models = Object.keys(stats.inference ?? {});
  const targetPpe = stats.target_fps?.ppe;
  const cameraIds = Array.from(new Set([...Object.keys(stats.cameras ?? {}), ...Object.keys(health?.sources ?? {})]));

  return (
    <div className="page">
      <PageHeader title="Производительность" description="Обновляется каждые 2 секунды." />
      {!stats.worker_alive && <Notice tone="warn">Воркер не отвечает: анализа нет, ниже последние известные задержки событий.</Notice>}
      {(stats.warnings ?? []).map((w) => <Notice key={w} tone="warn">{w}</Notice>)}
      {health?.mediamtx && !health.mediamtx.ok && <Notice>Видеосервер MediaMTX недоступен{health.mediamtx.error ? `: ${health.mediamtx.error}` : ""}</Notice>}

      <section className="figures" aria-label="Сводка">
        <Figure label="Потоков в анализе" value={String(stats.streams ?? 0)} />
        <Figure label="Детектор" value={stats.worker_alive ? `${stats.detector}` : "—"} note={stats.device === "cpu" ? "на процессоре" : stats.device} />
        <Figure label="От кадра до события" value={lat ? `${lat.avg_ms} мс` : "—"} note={latencyNote(lat, "событиям")} />
        <Figure label="От кадра до Telegram" value={tg ? `${tg.avg_ms} мс` : "—"} note={tg ? latencyNote(tg, "сообщениям") : "сообщений ещё не было"} />
        <Figure label="Клипы" value={`${stats.clips?.built ?? 0}`} note={`в работе ${stats.clips?.pending ?? 0}, с ошибкой ${stats.clips?.failed ?? 0}`} />
        <Figure label="Вырезок в секунду" value={String(stats.crops_per_s ?? 0)}
          note={`сигарета ${stats.crop_pass?.cigarette ? "вкл" : "выкл"}, головы ${stats.crop_pass?.head ? "вкл" : "выкл"}`} />
      </section>

      <section className="panel">
        <h2 className="panel-title">Модели</h2>
        {models.length === 0 ? <p className="muted">Воркер ещё не прислал замеры.</p> : (
          <table className="table">
            <thead><tr><th>Модель</th><th className="num">Среднее на пачку</th><th className="num">p95 на пачку</th><th className="num">Кадров в пачке</th><th className="num">Кадров/с, цель</th><th className="num">Кадров/с, факт</th></tr></thead>
            <tbody>
              {models.map((m) => {
                const inf = stats.inference?.[m];
                const goal = stats.target_fps?.[m];
                const actual = stats.actual_fps?.[m];
                const slow = actual !== undefined && goal !== undefined && actual < goal * 0.9;
                return (
                  <tr key={m}>
                    <td>{m}</td>
                    <td className="num">{ms(inf?.avg_ms)}</td>
                    <td className="num">{ms(inf?.p95_ms)}</td>
                    <td className="num">{inf?.batch ?? "—"}</td>
                    <td className="num">{goal ?? "—"}</td>
                    <td className={`num ${slow ? "text-warn" : ""}`}>{actual ?? "—"}{slow ? ", не успевает" : ""}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </section>

      <section className="panel">
        <h2 className="panel-title">Камеры</h2>
        <table className="table">
          <thead><tr><th>Камера</th><th>Видеопоток</th><th>Анализ</th><th className="num">Чтение, кадров/с</th><th className="num">Обработка, кадров/с</th></tr></thead>
          <tbody>
            {cameraIds.map((id) => {
              const src = health?.sources?.[id];
              const c = stats.cameras?.[id];
              const slow = c && targetPpe !== undefined && c.online && c.proc_fps < Math.min(targetPpe, c.read_fps) * 0.9;
              return (
                <tr key={id}>
                  <td>{names.get(id) ?? `Камера ${id}`}</td>
                  <td className={src?.publishing ? "text-ok" : "text-bad"}>{src ? (src.publishing ? "публикуется" : `нет${src.error ? `: ${src.error}` : ""}`) : "—"}</td>
                  <td className={c?.online ? "text-ok" : "text-bad"}>{c ? (c.online ? "идёт" : `нет кадров${c.error ? `: ${c.error}` : ""}`) : stats.worker_alive ? "воркер не взял камеру" : "воркер не запущен"}</td>
                  <td className="num">{c?.read_fps ?? "—"}</td>
                  <td className={`num ${slow ? "text-warn" : ""}`}>{c?.proc_fps ?? "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </section>
    </div>
  );
}

function latencyNote(l: LatencySummary | null, what: string): string {
  return l ? `p95 ${l.p95_ms} мс, по ${l.events} ${what}` : "событий с отметкой кадра ещё нет";
}
