import { useEffect, useState } from "react";
import { api } from "../api";
import { EmptyState, PageHeader, PageLoader } from "../App";
import { Icon } from "../components/Icons";
import { eventLabel, formatDateTime, formatPercent, reviewClass, reviewLabel, severityClass, severityLabel } from "../lib/format";
import { EVENT_TYPES, REVIEW_LABEL, SEVERITY_LABEL, type Camera, type Review, type SafetyEvent } from "../types";
import type { PageProps } from "./types";

type FilterState = { search: string; camera: string; type: string; severity: string; status: string; review: string; from: string; to: string };
const emptyFilters: FilterState = { search: "", camera: "", type: "", severity: "", status: "", review: "", from: "", to: "" };

export function EventsPage({ refreshKey }: PageProps) {
  const [filters, setFilters] = useState<FilterState>(() => readFilters());
  const [events, setEvents] = useState<SafetyEvent[]>([]);
  const [total, setTotal] = useState(0);
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [selected, setSelected] = useState<SafetyEvent | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);
  const limit = 30;

  useEffect(() => {
    const next = new URLSearchParams();
    Object.entries(filters).forEach(([key, value]) => { if (value) next.set(key, value); });
    window.history.replaceState({}, "", `/events${next.toString() ? `?${next}` : ""}`);
  }, [filters]);

  useEffect(() => {
    let cancelled = false;
    setLoading(events.length === 0);
    const params: Record<string, string | number | undefined> = { limit, offset, camera_id: filters.camera ? filters.camera : undefined, type: filters.type || undefined, severity: filters.severity || undefined, status: filters.status || undefined, review: filters.review || undefined, from: filters.from ? `${filters.from}T00:00:00Z` : undefined, to: filters.to ? `${filters.to}T23:59:59Z` : undefined };
    Promise.all([api.events(params), api.cameras()]).then(([page, cameraList]) => {
      if (cancelled) return;
      const nextEvents = filters.search ? page.items.filter((event) => `${event.camera_name} ${event.zone_name ?? ""} ${eventLabel(event.type)}`.toLowerCase().includes(filters.search.toLowerCase())) : page.items;
      setEvents(nextEvents);
      setSelected((current) => current && nextEvents.some((event) => event.id === current.id) ? nextEvents.find((event) => event.id === current.id) ?? null : nextEvents[0] ?? null);
      setTotal(page.total); setCameras(cameraList); setError(null);
    }).catch((reason: unknown) => { if (!cancelled) setError(reason instanceof Error ? reason.message : "Не удалось загрузить журнал"); }).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [filters, offset, refreshKey]);

  const update = (key: keyof FilterState, value: string) => { setOffset(0); setFilters((current) => ({ ...current, [key]: value })); };
  const activeFilters = Object.values(filters).filter(Boolean).length;

  async function changeReview(event: SafetyEvent, review: Review) {
    try {
      const updated = await api.reviewEvent(event.id, review);
      setEvents((current) => current.map((item) => item.id === event.id ? updated : item));
      setSelected((current) => current?.id === event.id ? updated : current);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Не удалось обновить разметку"); }
  }

  return (
    <div className="page-stack">
      <PageHeader kicker="Разбор / сигналы" title="Журнал событий" description="Каждый сигнал — с контекстом, доказательством и понятным следующим действием." action={<button className="button button-secondary" type="button" onClick={() => setFilters(emptyFilters)}><Icon name="refresh" size={16} />Сбросить фильтры</button>} />
      {error && <div className="error-notice" role="alert"><Icon name="alert" size={17} /><span>{error}</span></div>}
      <section className="filter-bar" aria-label="Фильтры журнала">
        <label className="search-field"><Icon name="search" size={17} /><span className="sr-only">Поиск по журналу</span><input name="search" value={filters.search} onChange={(event) => update("search", event.target.value)} placeholder="Камера, зона или тип…" autoComplete="off" /></label>
        <FilterSelect label="Камера" value={filters.camera} onChange={(value) => update("camera", value)} options={cameras.map((camera) => ({ value: String(camera.id), label: camera.name }))} />
        <FilterSelect label="Важность" value={filters.severity} onChange={(value) => update("severity", value)} options={Object.entries(SEVERITY_LABEL).map(([value, label]) => ({ value, label }))} />
        <FilterSelect label="Статус" value={filters.status} onChange={(value) => update("status", value)} options={[{ value: "open", label: "Открытые" }, { value: "closed", label: "Закрытые" }]} />
        <FilterSelect label="Разметка" value={filters.review} onChange={(value) => update("review", value)} options={Object.entries(REVIEW_LABEL).map(([value, label]) => ({ value, label }))} />
        <button className={`filter-toggle ${activeFilters ? "filter-toggle-active" : ""}`} type="button" onClick={() => setFilters((current) => ({ ...current, type: current.type ? "" : "no_helmet" }))}><Icon name="filter" size={16} />Тип {activeFilters > 0 ? `· ${activeFilters}` : ""}</button>
      </section>
      {filters.type && <div className="type-filter-row"><span>Тип сигнала</span>{EVENT_TYPES.map((type) => <button key={type} className={filters.type === type ? "type-chip type-chip-active" : "type-chip"} type="button" onClick={() => update("type", filters.type === type ? "" : type)}>{eventLabel(type)}</button>)}</div>}

      <div className={`journal-layout ${selected ? "has-preview" : ""}`}><section className="panel journal-panel"><div className="table-heading"><div><span className="panel-kicker">{total ? `${total} сигналов` : "Сигналы"}</span><h2>Последние события</h2></div><span className="table-caption">обновляется в live-режиме</span></div>
        {loading ? <PageLoader kind="table" label="Загружаем журнал…" /> : events.length ? <div className="event-table" role="table" aria-label="События"><div className="event-table-head" role="row"><span>Сигнал</span><span>Источник</span><span>Время</span><span>Статус</span><span>Разбор</span><span /></div>{events.map((event) => <EventTableRow key={event.id} event={event} onSelect={() => setSelected(event)} onReview={(review) => changeReview(event, review)} />)}</div> : <EmptyState icon="alert" title={activeFilters ? "По этим фильтрам тихо" : "Журнал пока пуст"} description={activeFilters ? "Сбросьте часть фильтров — или дождитесь нового сигнала от камеры." : "События появятся здесь сразу после первой фиксации нарушения."} action={activeFilters ? <button className="button button-secondary" type="button" onClick={() => setFilters(emptyFilters)}>Сбросить фильтры</button> : undefined} />}
        {total > limit && <div className="pagination"><span>{offset + 1}—{Math.min(offset + limit, total)} из {total}</span><div><button className="icon-button" type="button" aria-label="Предыдущая страница" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - limit))}><Icon name="arrow" size={15} style={{ transform: "rotate(180deg)" }} /></button><button className="icon-button" type="button" aria-label="Следующая страница" disabled={offset + limit >= total} onClick={() => setOffset(offset + limit)}><Icon name="arrow" size={15} /></button></div></div>}
      </section>{selected && <JournalPreview event={selected} onReview={(review) => changeReview(selected, review)} />}</div>
    </div>
  );
}

function FilterSelect({ label, value, onChange, options }: { label: string; value: string; onChange: (value: string) => void; options: { value: string; label: string }[] }) { return <label className="filter-select"><span>{label}</span><select name={label} value={value} onChange={(event) => onChange(event.target.value)}><option value="">Все</option>{options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select><Icon name="chevron" size={14} /></label>; }

function EventTableRow({ event, onSelect, onReview }: { event: SafetyEvent; onSelect: () => void; onReview: (review: Review) => void }) {
  return <div className="event-table-row" role="row"><button className="event-table-signal" type="button" onClick={onSelect}><span className={`severity-marker ${severityClass(event.severity)}`} /><span><strong>{eventLabel(event.type)}</strong><small>{event.zone_name ?? "Вне зоны"}</small></span></button><span className="table-camera">{event.camera_name}</span><span className="table-time">{formatDateTime(event.started_at)}</span><span><span className={`status-chip ${severityClass(event.severity)} ${event.status === "open" ? "status-open" : "status-closed"}`}>{event.status === "open" ? "Открыто" : "Закрыто"}</span></span><button className={`review-status review-status-button ${reviewClass(event.review)}`} type="button" onClick={() => onReview(event.review === "confirmed" ? "none" : "confirmed")} title="Переключить подтверждение">{reviewLabel(event.review)}</button><button className="icon-button row-open" type="button" aria-label={`Открыть ${eventLabel(event.type)}`} onClick={onSelect}><Icon name="arrow" size={15} /></button></div>;
}

function JournalPreview({ event, onReview }: { event: SafetyEvent; onReview: (review: Review) => void }) {
  return <aside className="journal-preview" aria-label="Выбранное событие"><div className="journal-preview-header"><div><span className="table-caption">Выбранный сигнал</span><h2>{eventLabel(event.type)}</h2></div><span className={`status-chip ${severityClass(event.severity)}`}>{severityLabel(event.severity)}</span></div><div className={`journal-evidence ${event.snapshot_url ? "has-media" : ""}`}>{event.snapshot_url ? <img width="960" height="540" src={event.snapshot_url} alt={`Стоп-кадр: ${eventLabel(event.type)}`} /> : <><Icon name="camera" size={32} /><span>Стоп-кадр не приложен</span></>}</div><div className="journal-preview-facts"><div><span>Камера</span><strong>{event.camera_name}</strong></div><div><span>Время</span><strong>{formatDateTime(event.started_at)}</strong></div><div><span>Зона</span><strong>{event.zone_name ?? "Вне зоны"}</strong></div><div><span>Уверенность</span><strong>{formatPercent(event.confidence)}</strong></div></div><div className="journal-preview-actions"><button className={event.review === "confirmed" ? "review-button review-button-active" : "review-button"} type="button" onClick={() => onReview("confirmed")}>Подтвердить</button><button className={event.review === "false_positive" ? "review-button review-button-active review-button-danger" : "review-button"} type="button" onClick={() => onReview("false_positive")}>Ложное</button></div></aside>;
}

function readFilters(): FilterState { const params = new URLSearchParams(window.location.search); return { search: params.get("search") ?? "", camera: params.get("camera") ?? params.get("camera_id") ?? "", type: params.get("type") ?? "", severity: params.get("severity") ?? "", status: params.get("status") ?? "", review: params.get("review") ?? "", from: params.get("from") ?? "", to: params.get("to") ?? "" }; }
