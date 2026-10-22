import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../api";
import { periodStart } from "../lib/journal";
import { EVENT_LABEL, SEVERITY_COLOR, SEVERITY_ICON, SEVERITY_LABEL } from "../types";
import type { AnalyticsSummary, Camera } from "../types";

const PERIODS: [string, string][] = [["day", "Сутки"], ["week", "7 дней"], ["month", "30 дней"], ["all", "Всё время"]];
const AXIS = { fill: "#8b96a5", fontSize: 12 };
const TOOLTIP = {
  contentStyle: { background: "#161b22", border: "1px solid #2a323d", color: "#d7dde5" },
  cursor: { fill: "#1c232d" },
};

function Card({ title, value, hint }: { title: string; value: string; hint?: string }) {
  return (
    <div className="rounded border border-line bg-panel p-3">
      <div className="text-xs text-muted">{title}</div>
      <div className="text-xl font-semibold">{value}</div>
      {hint && <div className="text-xs text-muted">{hint}</div>}
    </div>
  );
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded border border-line bg-panel p-3">
      <h2 className="mb-2 font-medium">{title}</h2>
      {children}
    </section>
  );
}

function Bars({ rows, empty }: { rows: { key: string | number; label: string; hint?: string; total: number }[]; empty: string }) {
  const max = Math.max(1, ...rows.map((r) => r.total));
  if (rows.length === 0) return <div className="text-muted">{empty}</div>;
  return (
    <div className="flex flex-col gap-1.5">
      {rows.map((r) => (
        <div key={r.key} className="grid grid-cols-[minmax(120px,1fr)_2fr_40px] items-center gap-2">
          <span className="truncate" title={r.hint}>{r.label}{r.hint && <span className="text-muted"> · {r.hint}</span>}</span>
          <span className="h-3 rounded bg-panel-2"><span className="block h-3 rounded bg-accent" style={{ width: `${(r.total / max) * 100}%` }} /></span>
          <span className="text-right tabular-nums">{r.total}</span>
        </div>
      ))}
    </div>
  );
}

const percent = (share: number | null) => (share === null ? "—" : `${Math.round(share * 100)}%`);

export function AnalyticsPage() {
  const [period, setPeriod] = useState("day");
  const [cameraId, setCameraId] = useState("");
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [data, setData] = useState<AnalyticsSummary | null>(null);
  const [error, setError] = useState("");

  useEffect(() => { api.cameras().then(setCameras).catch(() => undefined); }, []);

  useEffect(() => {
    const load = () => api.analytics({
      from: periodStart(period), camera_id: cameraId || undefined, tz_offset_min: -new Date().getTimezoneOffset(),
    }).then((d) => { setData(d); setError(""); }).catch((e: Error) => setError(e.message));
    load();
    const timer = setInterval(load, 10_000);
    return () => clearInterval(timer);
  }, [period, cameraId]);

  if (!data) return <div className="p-6 text-muted">{error ? `▲ ${error}` : "Загрузка…"}</div>;

  const types = data.by_type.map((t) => ({
    name: EVENT_LABEL[t.type], "подтверждено": t.confirmed, "ложное": t.false_positive, "не размечено": t.unreviewed,
  }));
  const hours = data.by_hour.map((h) => ({ name: String(h.hour).padStart(2, "0"), "нарушений": h.total }));
  const marked = data.confirmed + data.false_positive;
  const journal = (query: string) => `/journal?${query}${cameraId ? `&camera_id=${cameraId}` : ""}`;

  return (
    <div className="max-w-7xl p-4">
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <h1 className="text-base font-semibold">Аналитика</h1>
        <div className="flex gap-1">
          {PERIODS.map(([key, title]) => (
            <button key={key} onClick={() => setPeriod(key)}
                    className={`rounded border px-3 py-1 ${period === key ? "border-accent text-text" : "border-line text-muted hover:text-text"}`}>
              {title}
            </button>
          ))}
        </div>
        <select value={cameraId} onChange={(e) => setCameraId(e.target.value)} className="rounded border border-line bg-bg px-2 py-1 text-text">
          <option value="">все камеры</option>
          {cameras.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
        {error && <span className="text-high">▲ {error}</span>}
      </div>

      <div className="mb-3 grid grid-cols-[repeat(auto-fill,minmax(200px,1fr))] gap-3">
        <Card title="Нарушений за период" value={String(data.total)} />
        {data.by_severity.map((s) => (
          <Card key={s.severity} title={`${SEVERITY_ICON[s.severity]} Важность ${SEVERITY_LABEL[s.severity]}`} value={String(s.total)} />
        ))}
        <Card title="Доля ложных" value={percent(data.false_share)}
              hint={marked ? `${data.false_positive} ложных из ${marked} размеченных` : "нет размеченных событий"} />
        <Card title="Не размечено" value={String(data.unreviewed)} hint="отметки ставятся в журнале" />
      </div>

      {data.total === 0 ? (
        <div className="rounded border border-line bg-panel p-6 text-muted">За этот период нарушений нет.</div>
      ) : (
        <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
          <Panel title="По типам">
            <div className="h-64">
              <ResponsiveContainer>
                <BarChart data={types} margin={{ top: 4, right: 8, bottom: 0, left: -16 }}>
                  <CartesianGrid stroke="#2a323d" vertical={false} />
                  <XAxis dataKey="name" tick={AXIS} interval={0} angle={-20} textAnchor="end" height={56} />
                  <YAxis allowDecimals={false} tick={AXIS} />
                  <Tooltip {...TOOLTIP} />
                  <Legend />
                  <Bar dataKey="подтверждено" stackId="a" fill="#3fb97f" isAnimationActive={false} />
                  <Bar dataKey="ложное" stackId="a" fill={SEVERITY_COLOR.medium} isAnimationActive={false} />
                  <Bar dataKey="не размечено" stackId="a" fill="#4c9aff" isAnimationActive={false} />
                </BarChart>
              </ResponsiveContainer>
            </div>
            <table className="mt-2 w-full text-left text-sm">
              <thead className="text-xs text-muted">
                <tr>{["Тип", "Всего", "Подтверждено", "Ложных", "Доля ложных", ""].map((h) => <th key={h} className="py-1 font-normal">{h}</th>)}</tr>
              </thead>
              <tbody>
                {data.by_type.filter((t) => t.total > 0).map((t) => (
                  <tr key={t.type} className="border-t border-line">
                    <td className="py-1">{EVENT_LABEL[t.type]}</td>
                    <td className="tabular-nums">{t.total}</td>
                    <td className="tabular-nums">{t.confirmed}</td>
                    <td className="tabular-nums">{t.false_positive}</td>
                    <td className="tabular-nums">{percent(t.false_share)}</td>
                    <td className="text-right"><Link className="text-accent hover:underline" to={journal(`type=${t.type}`)}>в журнал</Link></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Panel>

          <Panel title="По часам суток">
            <div className="h-64">
              <ResponsiveContainer>
                <BarChart data={hours} margin={{ top: 4, right: 8, bottom: 0, left: -16 }}>
                  <CartesianGrid stroke="#2a323d" vertical={false} />
                  <XAxis dataKey="name" tick={AXIS} interval={1} />
                  <YAxis allowDecimals={false} tick={AXIS} />
                  <Tooltip {...TOOLTIP} labelFormatter={(h) => `${h}:00–${h}:59`} />
                  <Bar dataKey="нарушений" fill="#4c9aff" isAnimationActive={false} />
                </BarChart>
              </ResponsiveContainer>
            </div>
            <p className="mt-2 text-xs text-muted">Час начала события по местному времени этого компьютера.</p>
          </Panel>

          <Panel title="По зонам">
            <Bars empty="Событий, привязанных к зонам, нет"
                  rows={data.by_zone.map((z) => ({ key: z.zone_id, label: z.zone_name ?? `зона ${z.zone_id}`, hint: z.camera_name, total: z.total }))} />
            {data.without_zone > 0 && <p className="mt-2 text-xs text-muted">Без зоны (требование камеры или событие всего кадра): {data.without_zone}</p>}
          </Panel>

          <Panel title="По камерам">
            <Bars empty="Событий нет"
                  rows={data.by_camera.map((c) => ({ key: c.camera_id, label: c.camera_name, total: c.total }))} />
          </Panel>
        </div>
      )}
    </div>
  );
}
