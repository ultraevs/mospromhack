import { useEffect, useState } from "react";
import { api, errorText } from "../api";
import { Figure, Loading, Notice, PageHeader } from "../components/Ui";
import { usePoll } from "../hooks/usePoll";
import type { Camera, Capacity, Health, LatencySummary, Stats } from "../types";

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
  const mx = stats.latency?.capture_to_max ?? null;
  const models = Object.keys(stats.inference ?? {});
  const targetPpe = stats.target_fps?.ppe;
  const cameraIds = Array.from(new Set([...Object.keys(stats.cameras ?? {}), ...Object.keys(health?.sources ?? {})]));

  return (
    <div className="page">
      <PageHeader title="Производительность" />
      {!stats.worker_alive && <Notice tone="warn">Воркер не отвечает: анализа нет, ниже последние известные задержки событий.</Notice>}
      {(stats.warnings ?? []).map((w) => <Notice key={w} tone="warn">{w}</Notice>)}
      {health?.mediamtx && !health.mediamtx.ok && <Notice>Видеосервер MediaMTX недоступен{health.mediamtx.error ? `: ${health.mediamtx.error}` : ""}</Notice>}

      <section className="figures" aria-label="Сводка">
        <Figure label="Потоков в анализе" value={String(stats.streams ?? 0)} />
        <Figure label="Детектор" value={stats.worker_alive ? `${stats.detector}` : "—"} note={stats.device === "cpu" ? "на процессоре" : stats.device} />
        <Figure label="От кадра до события" value={lat ? `${lat.avg_ms} мс` : "—"} note={latencyNote(lat, "событиям")} />
        <Figure label="От кадра до MAX" value={mx ? `${mx.avg_ms} мс` : "—"} note={mx ? latencyNote(mx, "сообщениям") : "сообщений ещё не было"} />
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

      <CapacityPanel />

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

function CapacityPanel() {
  const [cameras, setCameras] = useState(20);
  const [fps, setFps] = useState(6);
  const [tiles, setTiles] = useState(true);
  const [data, setData] = useState<Capacity | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!(cameras >= 1 && fps > 0)) return;
    const t = window.setTimeout(() => {
      api.capacity({ cameras, fps, tiles: String(tiles) })
        .then((d) => { setData(d); setError(null); })
        .catch((e) => setError(errorText(e, "Не удалось посчитать")));
    }, 300);
    return () => window.clearTimeout(t);
  }, [cameras, fps, tiles]);

  return (
    <section className="panel">
      <h2 className="panel-title">Сколько камер потянет</h2>
      <div className="form form-grid">
        <label className="field"><span>Камер</span>
          <input type="number" min={1} max={1000} value={cameras} onChange={(e) => setCameras(Number(e.target.value))} /></label>
        <label className="field"><span>Кадров/с на камеру</span>
          <input type="number" min={1} max={30} value={fps} onChange={(e) => setFps(Number(e.target.value))} /></label>
        <label className="field"><span>Головы по плиткам</span>
          <select value={tiles ? "1" : "0"} onChange={(e) => setTiles(e.target.value === "1")}>
            <option value="1">вкл</option><option value="0">выкл</option>
          </select></label>
      </div>
      {error && <Notice>{error}</Notice>}
      {data && (
        <>
          <section className="figures" aria-label="Расчёт">
            <Figure label="Инференсов/с на камеру" value={String(data.infer_per_s)}
              note={data.models.map((m) => `${m.model} ${m.per_s}`).join(", ")} />
            <Figure label="Камер на ускоритель" value={String(data.per_accelerator)}
              note={`${data.chip_s_per_camera_s} с чипа на секунду камеры`} />
            <Figure label="Нужно ускорителей" value={data.accelerators === null ? "—" : String(data.accelerators)} note={`для ${data.cameras} камер`} />
            <Figure label="Время инференса" value={data.source === "measured" ? "замер сейчас" : "справочно"}
              note={data.source === "measured" ? `детектор ${data.detector ?? "?"}${data.detector === "linq" ? "" : ", это не LinQ"}: ${data.models.map((m) => `${m.model} ${m.ms} мс`).join(", ")}` : data.note ?? "3 мс на инференс"} />
          </section>
          <p className="muted">Справочно: {data.reference.join("; ")}.</p>
        </>
      )}
    </section>
  );
}
