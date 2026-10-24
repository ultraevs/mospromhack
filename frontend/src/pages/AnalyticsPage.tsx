import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { EmptyState, PageHeader } from "../App";
import { Icon } from "../components/Icons";
import { eventLabel, formatCount, formatPercent } from "../lib/format";
import type { AnalyticsSummary, Camera } from "../types";
import type { PageProps } from "./types";

export function AnalyticsPage({ refreshKey }: PageProps) {
  const [summary, setSummary] = useState<AnalyticsSummary | null>(null);
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [camera, setCamera] = useState("");
  const [range, setRange] = useState("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const requestIdRef = useRef(0);

  useEffect(() => {
    let cancelled = false;
    const requestId = ++requestIdRef.current;
    setLoading(true);
    const period = range === "all" ? {} : { days: Number(range) };
    Promise.all([api.analytics({ ...period, camera_id: camera || undefined, tz_offset_min: -new Date().getTimezoneOffset() }), api.cameras()]).then(([next, cameraList]) => { if (!cancelled && requestId === requestIdRef.current) { setSummary(next); setCameras(cameraList); setError(null); } }).catch((reason: unknown) => { if (!cancelled && requestId === requestIdRef.current) setError(reason instanceof Error ? reason.message : "Не удалось загрузить аналитику"); }).finally(() => { if (!cancelled && requestId === requestIdRef.current) setLoading(false); });
    return () => { cancelled = true; };
  }, [camera, range, refreshKey]);

  const peakHour = useMemo(() => summary?.by_hour.reduce((peak, item) => item.total > peak.total ? item : peak, { hour: 0, total: 0 }), [summary]);
  const maxHour = Math.max(...(summary?.by_hour.map((item) => item.total) ?? [0]), 1);
  const topTypes = summary?.by_type.filter((item) => item.total).sort((a, b) => b.total - a.total).slice(0, 5) ?? [];
  const rangeLabel = range === "all" ? "всё время" : `${range} дней`;
  const cameraLabel = cameras.find((item) => String(item.id) === camera)?.name ?? "все камеры";

  return <div className={`page-stack analytics-page ${loading ? "is-loading" : ""}`}><PageHeader kicker="Сигналы / закономерности" title="Аналитика" description="Не просто количество событий — точки напряжения, которые помогают менять процесс." action={<div className="range-control"><button type="button" aria-pressed={range === "all"} className={range === "all" ? "range-active" : ""} onClick={() => setRange("all")}>Всё время</button><button type="button" aria-pressed={range === "30"} className={range === "30" ? "range-active" : ""} onClick={() => setRange("30")}>30 дней</button><button type="button" aria-pressed={range === "7"} className={range === "7" ? "range-active" : ""} onClick={() => setRange("7")}>7 дней</button></div>} />{error && <div className="error-notice" role="alert"><Icon name="alert" size={17} /><span>{error}</span></div>}<section className="analytics-filters"><AnalyticsCameraSelect value={camera} cameras={cameras} onChange={setCamera} /><span className="analytics-note"><Icon name="clock" size={15} />Москва (UTC+3)</span><span className="analytics-filter-summary" aria-live="polite">Срез: {rangeLabel} · {cameraLabel}{loading && " · обновляем"}</span></section>{summary && summary.total ? <><section className="analytics-kpis"><AnalyticsKpi label="Всего событий" value={formatCount(summary.total)} note="за выбранный период" /><AnalyticsKpi label="Подтверждено" value={formatCount(summary.confirmed)} note={`${formatPercent(summary.total ? summary.confirmed / summary.total : 0)} от всех`} tone="green" /><AnalyticsKpi label="Ложные срабатывания" value={formatCount(summary.false_positive)} note={`${formatPercent(summary.false_share)} среди размеченных`} tone="danger" /><AnalyticsKpi label="Пик активности" value={peakHour ? `${String(peakHour.hour).padStart(2, "0")}:00` : "—"} note={peakHour ? `${peakHour.total} событий в час` : "нет данных"} tone="amber" /></section><div className="analytics-grid"><section className="panel chart-panel"><div className="panel-heading"><div><span className="panel-kicker">Ритм смены</span><h2>События по часам</h2></div><span className="chart-unit">событий / час</span></div><div className="bar-chart" aria-label="График событий по часам">{summary.by_hour.map((item) => <div className="bar-column" key={item.hour}><div className="bar-track"><span className="bar-fill" style={{ height: `${Math.max(item.total ? (item.total / maxHour) * 100 : 3, 3)}%` }} title={`${item.hour}:00 · ${item.total}`} /></div><span>{item.hour % 4 === 0 ? String(item.hour).padStart(2, "0") : ""}</span></div>)}</div></section><section className="panel ranking-panel"><div className="panel-heading"><div><span className="panel-kicker">Распределение</span><h2>Типы сигналов</h2></div><Icon name="activity" size={18} /></div>{topTypes.length ? <div className="ranking-list">{topTypes.map((item, index) => <div className="ranking-row" key={item.type}><span className="ranking-index">{String(index + 1).padStart(2, "0")}</span><div className="ranking-name"><strong>{eventLabel(item.type)}</strong><span className="ranking-track"><i style={{ width: `${(item.total / topTypes[0].total) * 100}%` }} /></span></div><strong className="ranking-value">{item.total}</strong></div>)}</div> : <EmptyState title="Данных пока мало" description="Распределение появится после первых событий." />}</section></div><section className="panel camera-ranking"><div className="panel-heading"><div><span className="panel-kicker">Нагрузка</span><h2>Камеры и зоны</h2></div><button className="text-button" type="button" onClick={() => setCamera("")}>Сбросить источник <Icon name="refresh" size={14} /></button></div><div className="camera-ranking-grid"><div><span className="ranking-subtitle">Камеры</span>{summary.by_camera.length ? summary.by_camera.slice(0, 5).map((item) => <RankingLine key={item.camera_id} label={item.camera_name} value={item.total} max={summary.by_camera[0].total} />) : <span className="muted-copy">Нет данных</span>}</div><div><span className="ranking-subtitle">Зоны</span>{summary.by_zone.length ? summary.by_zone.slice(0, 5).map((item) => <RankingLine key={item.zone_id} label={item.zone_name ?? "Без названия"} detail={item.camera_name} value={item.total} max={summary.by_zone[0].total} />) : <span className="muted-copy">События вне зон: {summary.without_zone}</span>}</div></div></section></> : <section className="panel"><EmptyState icon="activity" title="За выбранный срез событий нет" description="Измените период или источник, чтобы посмотреть другой срез." /></section>}</div>;
}

function AnalyticsKpi({ label, value, note, tone = "neutral" }: { label: string; value: string; note: string; tone?: "neutral" | "green" | "danger" | "amber" }) { return <div className={`analytics-kpi kpi-${tone}`}><span className="metric-label">{label}</span><strong>{value}</strong><span>{note}</span></div>; }
function RankingLine({ label, detail, value, max }: { label: string; detail?: string; value: number; max: number }) { return <div className="ranking-line"><div><strong>{label}</strong>{detail && <small>{detail}</small>}</div><span className="ranking-line-track"><i style={{ width: `${(value / Math.max(max, 1)) * 100}%` }} /></span><b>{value}</b></div>; }

function AnalyticsCameraSelect({ value, cameras, onChange }: { value: string; cameras: Camera[]; onChange: (value: string) => void }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const selected = cameras.find((item) => String(item.id) === value)?.name ?? "Все камеры";
  const options = [{ value: "", label: "Все камеры" }, ...cameras.map((item) => ({ value: String(item.id), label: item.name }))];

  useEffect(() => {
    const handlePointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, []);

  return <div className="analytics-select" ref={rootRef}>
    <span className="analytics-select-label">Источник</span>
    <button className="analytics-select-trigger" type="button" aria-label="Источник камеры" aria-haspopup="listbox" aria-expanded={open} onClick={() => setOpen((current) => !current)}><span>{selected}</span><Icon name="chevron" size={14} /></button>
    {open && <div className="analytics-select-menu" role="listbox" aria-label="Источник камеры">{options.map((option) => <button className="analytics-select-option" key={option.value || "all"} type="button" role="option" aria-selected={option.value === value} onClick={() => { onChange(option.value); setOpen(false); }}>{option.label}</button>)}</div>}
  </div>;
}
