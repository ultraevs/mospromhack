import { useEffect, useState } from "react";
import { api } from "../api";
import type { Camera, Stats } from "../types";

function Card({ title, value, hint, warn }: { title: string; value: string; hint?: string; warn?: boolean }) {
  return (
    <div className="rounded border border-line bg-panel p-3">
      <div className="text-xs text-muted">{title}</div>
      <div className={`text-xl font-semibold ${warn ? "text-medium" : ""}`}>{value}</div>
      {hint && <div className="text-xs text-muted">{hint}</div>}
    </div>
  );
}

const ms = (v?: number) => (v === undefined ? "—" : `${v} мс`);

export function PerformancePage() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    const load = () => {
      api.stats().then((s) => { setStats(s); setError(""); }).catch((e: Error) => setError(e.message));
      api.cameras().then(setCameras).catch(() => undefined);
    };
    load();
    const timer = setInterval(load, 1000);
    return () => clearInterval(timer);
  }, []);

  if (!stats) return <div className="p-6 text-muted">{error ? `▲ ${error}` : "Загрузка…"}</div>;

  const names = new Map(cameras.map((c) => [String(c.id), c.name]));
  const target = stats.target_fps?.ppe;
  const lat = stats.latency?.capture_to_event;
  const tg = stats.latency?.capture_to_telegram;
  const onCpu = stats.device === "cpu";

  return (
    <div className="max-w-6xl p-4">
      <h1 className="mb-3 text-base font-semibold">Производительность</h1>
      {!stats.worker_alive && <div className="mb-3 text-high">▲ Воркер не отвечает: цифры ниже недоступны.</div>}
      {(stats.warnings ?? []).map((w) => <div key={w} className="mb-2 text-medium">◆ {w}</div>)}

      <div className="grid grid-cols-[repeat(auto-fill,minmax(210px,1fr))] gap-3">
        <Card title="Потоков" value={String(stats.streams ?? 0)} />
        <Card title="Детектор и устройство" value={stats.worker_alive ? `${stats.detector} · ${stats.device}` : "—"}
              warn={onCpu} hint={onCpu ? "инференс идёт на процессоре" : undefined} />
        <Card title="Вырезок в секунду" value={String(stats.crops_per_s ?? 0)}
              hint={`сигарета: ${stats.crop_pass?.cigarette ? "вкл" : "выкл"}, головы: ${stats.crop_pass?.head ? "вкл" : "выкл"}`} />
        <Card title="От кадра до события" value={lat ? `${lat.avg_ms} мс` : "—"}
              hint={lat ? `p95 ${lat.p95_ms} мс, по ${lat.events} событиям` : "событий со временем кадра ещё нет"} />
        <Card title="От кадра до телеграма" value={tg ? `${tg.avg_ms} мс` : "—"}
              hint={tg ? `p95 ${tg.p95_ms} мс, по ${tg.events} сообщениям` : "сообщений ещё не было или бот выключен"} />
        <Card title="Клипы" value={`${stats.clips?.built ?? 0} собрано`}
              hint={`в работе ${stats.clips?.pending ?? 0}, с ошибкой ${stats.clips?.failed ?? 0}`} warn={(stats.clips?.failed ?? 0) > 0} />
      </div>

      <h2 className="mb-2 mt-5 font-semibold">Инференс</h2>
      <table className="w-full max-w-3xl border-collapse text-left">
        <thead className="text-xs text-muted">
          <tr className="border-b border-line">
            {["Модель", "Среднее на батч", "p95 на батч", "Размер батча", "Частота, цель", "Частота, факт"].map((h) => (
              <th key={h} className="px-2 py-2 font-normal">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {(["ppe", "hazard"] as const).map((m) => {
            const inf = stats.inference?.[m];
            const actual = stats.actual_fps?.[m];
            const goal = stats.target_fps?.[m];
            const slow = actual !== undefined && goal !== undefined && actual < goal * 0.9;
            return (
              <tr key={m} className="border-b border-line">
                <td className="px-2 py-2 font-medium">{m}</td>
                <td className="px-2 py-2">{ms(inf?.avg_ms)}</td>
                <td className="px-2 py-2">{ms(inf?.p95_ms)}</td>
                <td className="px-2 py-2">{inf?.batch ?? "—"}</td>
                <td className="px-2 py-2">{goal ?? "—"}</td>
                <td className={`px-2 py-2 ${slow ? "text-medium" : ""}`}>{actual ?? "—"}{slow ? " ◆ не успевает" : ""}</td>
              </tr>
            );
          })}
        </tbody>
      </table>

      <h2 className="mb-2 mt-5 font-semibold">Камеры</h2>
      <table className="w-full max-w-3xl border-collapse text-left">
        <thead className="text-xs text-muted">
          <tr className="border-b border-line">
            {["Камера", "Состояние", "Чтение, кадров/с", "Обработка, кадров/с"].map((h) => (
              <th key={h} className="px-2 py-2 font-normal">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {Object.entries(stats.cameras ?? {}).map(([id, c]) => {
            const slow = target !== undefined && c.online && c.proc_fps < Math.min(target, c.read_fps) * 0.9;
            return (
              <tr key={id} className="border-b border-line">
                <td className="px-2 py-2">{names.get(id) ?? `камера ${id}`}</td>
                <td className={`px-2 py-2 ${c.online ? "text-ok" : "text-high"}`}>
                  {c.online ? "● на связи" : `▲ нет кадров${c.error ? `: ${c.error}` : ""}`}
                </td>
                <td className="px-2 py-2">{c.read_fps}</td>
                <td className={`px-2 py-2 ${slow ? "text-medium" : ""}`}>{c.proc_fps}{slow ? " ◆" : ""}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {Object.keys(stats.cameras ?? {}).length === 0 && <div className="p-2 text-muted">Камер в обработке нет</div>}
    </div>
  );
}
