import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api";
import { EventCard } from "../components/EventCard";
import { formatTime, SeverityBadge } from "../components/EventBits";
import { useSocket } from "../hooks/useSocket";
import {
  activeCount, EMPTY_FILTERS, filtersFromParams, filtersToParams, formatDuration, pageInfo, PAGE_SIZE, toQuery,
} from "../lib/journal";
import type { JournalFilters } from "../lib/journal";
import { EVENT_LABEL, EVENT_TYPES, REVIEW_LABEL, SEVERITY_LABEL } from "../types";
import type { Camera, EventMessage, EventPage, Review, SafetyEvent, Severity, Zone } from "../types";

const SELECT = "rounded border border-line bg-bg px-2 py-1 text-text";
const REVIEW_MARK: Record<Review, string> = { none: "", confirmed: "✔ ", false_positive: "✖ ", forwarded: "➜ " };

function Filter({ title, children }: { title: string; children: React.ReactNode }) {
  return <label className="flex flex-col gap-1 text-xs text-muted">{title}{children}</label>;
}

export function JournalPage() {
  const [params, setParams] = useSearchParams();
  const filters = filtersFromParams(params);
  const selectedId = params.get("event");
  const key = filtersToParams(filters).toString();

  const [page, setPage] = useState<EventPage | null>(null);
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [selected, setSelected] = useState<SafetyEvent | null>(null);
  const [error, setError] = useState("");
  const [fresh, setFresh] = useState(0);

  const load = useCallback(() => {
    api.events(toQuery(filtersFromParams(new URLSearchParams(key))))
      .then((p) => { setPage(p); setError(""); setFresh(0); })
      .catch((e: Error) => setError(e.message));
  }, [key]);

  useEffect(load, [load]);
  useEffect(() => { api.cameras().then(setCameras).catch(() => undefined); }, []);
  const [zones, setZones] = useState<Zone[]>([]);
  useEffect(() => {
    const ids = filters.camera_id ? [Number(filters.camera_id)] : cameras.map((c) => c.id);
    Promise.all(ids.map((id) => api.zones(id).catch(() => [] as Zone[])))
      .then((all) => setZones(all.flat()));
  }, [filters.camera_id, cameras]);
  const camName = (id: number) => cameras.find((c) => c.id === id)?.name ?? "";

  useEffect(() => {
    if (!selectedId) return setSelected(null);
    const inPage = page?.items.find((e) => e.id === selectedId);
    if (inPage) return setSelected(inPage);
    let cancelled = false;
    api.event(selectedId).then((e) => { if (!cancelled) setSelected(e); })
      .catch(() => { if (!cancelled) setSelected(null); });
    return () => { cancelled = true; };
  }, [selectedId, page]);

  const reload = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const connected = useSocket<EventMessage>("/ws/events", (msg) => {
    setPage((p) => p && { ...p, items: p.items.map((e) => (e.id === msg.event.id ? msg.event : e)) });
    if (msg.type !== "event_created") return;
    if (filters.page === 0) {
      clearTimeout(reload.current);
      reload.current = setTimeout(load, 400);
    } else {
      setFresh((n) => n + 1);
    }
  });
  useEffect(() => () => clearTimeout(reload.current), []);

  const update = (patch: Partial<JournalFilters>) =>
    setParams(filtersToParams({ ...filters, page: 0, ...patch }, selectedId), { replace: true });
  const open = (id: string | null) => setParams(filtersToParams(filters, id));
  const changed = (event: SafetyEvent) => {
    setSelected(event);
    setPage((p) => p && { ...p, items: p.items.map((e) => (e.id === event.id ? event : e)) });
  };

  const info = pageInfo(page?.total ?? 0, filters.page);
  const active = activeCount(filters);

  return (
    <div className="flex h-full min-h-0">
      <div className="min-w-0 flex-1 overflow-auto p-4">
        <div className="mb-3 flex items-center gap-3">
          <h1 className="text-base font-semibold">Журнал нарушений</h1>
          {!connected && <span className="text-xs text-medium">◆ нет связи с сервером, список может отставать</span>}
        </div>

        <div className="mb-3 flex flex-wrap items-end gap-3 rounded border border-line bg-panel p-3">
          <Filter title="Камера">
            <select className={SELECT} value={filters.camera_id} onChange={(e) => update({ camera_id: e.target.value, zone_id: "" })}>
              <option value="">все</option>
              {cameras.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </Filter>
          <Filter title="Зона">
            <select className={SELECT} value={filters.zone_id} onChange={(e) => update({ zone_id: e.target.value })}>
              <option value="">все</option>
              {zones.map((z) => <option key={z.id} value={z.id}>{filters.camera_id ? z.name : `${camName(z.camera_id)} / ${z.name}`}</option>)}
            </select>
          </Filter>
          <Filter title="Нарушение">
            <select className={SELECT} value={filters.type} onChange={(e) => update({ type: e.target.value })}>
              <option value="">все</option>
              {EVENT_TYPES.map((t) => <option key={t} value={t}>{EVENT_LABEL[t]}</option>)}
            </select>
          </Filter>
          <Filter title="Важность">
            <select className={SELECT} value={filters.severity} onChange={(e) => update({ severity: e.target.value })}>
              <option value="">любая</option>
              {(["high", "medium", "low"] as Severity[]).map((s) => <option key={s} value={s}>{SEVERITY_LABEL[s]}</option>)}
            </select>
          </Filter>
          <Filter title="Состояние">
            <select className={SELECT} value={filters.status} onChange={(e) => update({ status: e.target.value })}>
              <option value="">любое</option>
              <option value="open">идёт</option>
              <option value="closed">закрыто</option>
            </select>
          </Filter>
          <Filter title="Отметка">
            <select className={SELECT} value={filters.review} onChange={(e) => update({ review: e.target.value })}>
              <option value="">любая</option>
              {(["none", "confirmed", "false_positive", "forwarded"] as Review[]).map((r) => <option key={r} value={r}>{REVIEW_LABEL[r]}</option>)}
            </select>
          </Filter>
          <Filter title="С">
            <input type="datetime-local" className={SELECT} value={filters.from} onChange={(e) => update({ from: e.target.value })} />
          </Filter>
          <Filter title="По">
            <input type="datetime-local" className={SELECT} value={filters.to} onChange={(e) => update({ to: e.target.value })} />
          </Filter>
          <button disabled={active === 0} onClick={() => update(EMPTY_FILTERS)}
                  className="rounded border border-line px-3 py-1 text-muted hover:text-text disabled:opacity-40">
            Сбросить{active ? ` (${active})` : ""}
          </button>
        </div>

        {error && <div className="mb-3 text-high">▲ {error}</div>}
        {fresh > 0 && (
          <button onClick={() => update({})} className="mb-3 rounded border border-accent px-3 py-1 text-accent">
            Новых событий: {fresh}. Показать
          </button>
        )}

        <table className="w-full border-collapse text-left">
          <thead className="text-xs text-muted">
            <tr className="border-b border-line">
              {["", "Время", "Нарушение", "Важность", "Камера", "Зона", "Длительность", "Отметка"].map((h, i) => (
                <th key={i} className="px-2 py-2 font-normal">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {page?.items.map((e) => (
              <tr key={e.id} onClick={() => open(e.id)} data-testid="journal-row"
                  className={`cursor-pointer border-b border-line hover:bg-panel ${e.id === selectedId ? "bg-panel-2" : ""}`}>
                <td className="w-[76px] px-2 py-1">
                  {e.snapshot_url
                    ? <img src={e.snapshot_url} alt="" loading="lazy" className="h-9 w-16 rounded border border-line object-cover" />
                    : <div className="h-9 w-16 rounded border border-line" />}
                </td>
                <td className="whitespace-nowrap px-2 py-2">{formatTime(e.started_at)}</td>
                <td className="px-2 py-2">{EVENT_LABEL[e.type]}</td>
                <td className="px-2 py-2"><SeverityBadge severity={e.severity} /></td>
                <td className="px-2 py-2">{e.camera_name}</td>
                <td className="px-2 py-2 text-muted">{e.zone_name ?? "—"}</td>
                <td className={`whitespace-nowrap px-2 py-2 ${e.status === "open" ? "text-medium" : "text-muted"}`}>
                  {formatDuration(e.started_at, e.ended_at)}
                </td>
                <td className={`whitespace-nowrap px-2 py-2 ${e.review === "confirmed" ? "text-ok" : e.review === "false_positive" ? "text-medium" : e.review === "forwarded" ? "text-accent" : "text-muted"}`}>
                  {REVIEW_MARK[e.review]}{REVIEW_LABEL[e.review]}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {page && page.items.length === 0 && (
          <div className="p-3 text-muted">{active ? "По этим фильтрам событий нет" : "Событий пока нет"}</div>
        )}
        {!page && !error && <div className="p-3 text-muted">Загрузка…</div>}

        <div className="mt-3 flex items-center gap-3 text-muted">
          <span>{info.first ? `Показаны ${info.first}–${info.last} из ${page?.total}` : "Показано 0"}</span>
          <button disabled={!info.hasPrev} onClick={() => setParams(filtersToParams({ ...filters, page: filters.page - 1 }, selectedId))}
                  className="rounded border border-line px-3 py-1 hover:text-text disabled:opacity-40">← Новее</button>
          <span>стр. {filters.page + 1} из {info.pages}</span>
          <button disabled={!info.hasNext} onClick={() => setParams(filtersToParams({ ...filters, page: filters.page + 1 }, selectedId))}
                  className="rounded border border-line px-3 py-1 hover:text-text disabled:opacity-40">Старее →</button>
          <span className="text-xs">по {PAGE_SIZE} на странице</span>
        </div>
      </div>

      {selected && <EventCard key={selected.id} event={selected} onChange={changed} onClose={() => open(null)} />}
    </div>
  );
}
