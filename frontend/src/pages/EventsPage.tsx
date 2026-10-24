import { useEffect, useMemo, useState } from "react";
import { api, errorText } from "../api";
import { EventDetail } from "../components/EventDetail";
import { Icon } from "../components/Icons";
import { Sign } from "../components/Sign";
import { Empty, Loading, Notice, PageHeader, ReviewChip, SeverityChip } from "../components/Ui";
import { formatCount, formatDateTime, localDayEnd, localDayStart, plural } from "../lib/format";
import { setQuery, useLocation } from "../lib/router";
import { EVENT_LABEL, EVENT_TYPES, REVIEW_LABEL, SEVERITY_LABEL, type Camera, type SafetyEvent } from "../types";

const PAGE = 30;
const FILTER_KEYS = ["camera", "type", "severity", "status", "review", "from", "to"] as const;
type FilterKey = (typeof FILTER_KEYS)[number];

export function EventsPage({ tick }: { tick: number }) {
  const { search } = useLocation();
  const q = useMemo(() => new URLSearchParams(search), [search]);
  const filters = Object.fromEntries(FILTER_KEYS.map((k) => [k, q.get(k) ?? ""])) as Record<FilterKey, string>;
  const page = Math.max(1, Number(q.get("page") ?? 1) || 1);
  const selectedId = q.get("event");

  const [items, setItems] = useState<SafetyEvent[]>([]);
  const [total, setTotal] = useState(0);
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [selected, setSelected] = useState<SafetyEvent | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.cameras().then(setCameras).catch(() => undefined);
  }, []);

  const filterKey = FILTER_KEYS.map((k) => filters[k]).join("|");
  useEffect(() => {
    let cancelled = false;
    api.events({
      limit: PAGE,
      offset: (page - 1) * PAGE,
      camera_id: filters.camera || undefined,
      type: filters.type || undefined,
      severity: filters.severity || undefined,
      status: filters.status || undefined,
      review: filters.review || undefined,
      from: filters.from ? localDayStart(filters.from) : undefined,
      to: filters.to ? localDayEnd(filters.to) : undefined,
    }).then((res) => {
      if (cancelled) return;
      setItems(res.items);
      setTotal(res.total);
      setError(null);
    }).catch((e) => !cancelled && setError(errorText(e, "Не удалось загрузить журнал"))).finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [filterKey, page, tick]);

  useEffect(() => {
    if (!selectedId) {
      setSelected(null);
      return;
    }
    const inPage = items.find((e) => e.id === selectedId);
    if (inPage) {
      setSelected(inPage);
      return;
    }
    let cancelled = false;
    api.event(selectedId).then((e) => !cancelled && setSelected(e)).catch(() => !cancelled && setSelected(null));
    return () => {
      cancelled = true;
    };
  }, [selectedId, items]);

  const setFilter = (key: FilterKey, value: string) => setQuery({ [key]: value, page: null });
  const active = FILTER_KEYS.filter((k) => filters[k]).length;
  const pages = Math.max(1, Math.ceil(total / PAGE));

  const onChange = (updated: SafetyEvent) => {
    setItems((list) => list.map((e) => (e.id === updated.id ? updated : e)));
    setSelected(updated);
  };

  return (
    <div className="page">
      <PageHeader title="Журнал нарушений"
        description={loading ? "Загружаем…" : `${formatCount(total)} ${plural(total, "нарушение", "нарушения", "нарушений")}${active ? " по фильтрам" : ""}`} />

      <form className="filters" aria-label="Фильтры" onSubmit={(e) => e.preventDefault()}>
        <Select label="Камера" value={filters.camera} onChange={(v) => setFilter("camera", v)}
          options={cameras.map((c) => [String(c.id), c.name])} />
        <Select label="Нарушение" value={filters.type} onChange={(v) => setFilter("type", v)}
          options={EVENT_TYPES.map((t) => [t, EVENT_LABEL[t]])} />
        <Select label="Важность" value={filters.severity} onChange={(v) => setFilter("severity", v)}
          options={(["high", "medium", "low"] as const).map((s) => [s, SEVERITY_LABEL[s]])} />
        <Select label="Состояние" value={filters.status} onChange={(v) => setFilter("status", v)}
          options={[["open", "Идёт"], ["closed", "Закрыто"]]} />
        <Select label="Разбор" value={filters.review} onChange={(v) => setFilter("review", v)}
          options={Object.entries(REVIEW_LABEL)} />
        <label className="field field-date">
          <span>С</span>
          <input type="date" value={filters.from} max={filters.to || undefined} onChange={(e) => setFilter("from", e.target.value)} />
        </label>
        <label className="field field-date">
          <span>По</span>
          <input type="date" value={filters.to} min={filters.from || undefined} onChange={(e) => setFilter("to", e.target.value)} />
        </label>
        {active > 0 && (
          <button type="button" className="btn btn-ghost"
            onClick={() => setQuery({ ...Object.fromEntries(FILTER_KEYS.map((k) => [k, null])), page: null })}>
            <Icon name="refresh" size={15} />Сбросить
          </button>
        )}
      </form>

      {error && <Notice>{error}</Notice>}

      <div className={`journal ${selectedId ? "has-detail" : ""}`}>
        <section className="panel journal-list">
          {loading ? <Loading label="Загружаем журнал…" /> : items.length === 0 ? (
            <Empty title={active ? "По этим фильтрам нарушений нет" : "Нарушений пока нет"}>
              {active ? "Ослабьте фильтры или выберите другой период." : "Как только камера зафиксирует нарушение, оно появится здесь."}
            </Empty>
          ) : (
            <table className="table events-table">
              <thead>
                <tr><th>Время</th><th>Нарушение</th><th>Где</th><th>Важность</th><th>Состояние</th><th>Разбор</th></tr>
              </thead>
              <tbody>
                {items.map((e) => (
                  <tr key={e.id} aria-selected={e.id === selectedId} className={`sev-row sev-row-${e.severity}`} tabIndex={0}
                    onClick={() => setQuery({ event: e.id })}
                    onKeyDown={(k) => (k.key === "Enter" || k.key === " ") && (k.preventDefault(), setQuery({ event: e.id }))}>
                    <td className="tnum">{formatDateTime(e.started_at)}</td>
                    <td><span className="event-name"><Sign type={e.type} size={22} />{EVENT_LABEL[e.type]}</span></td>
                    <td>{e.camera_name}<small>{e.zone_name ?? "вне зон"}</small></td>
                    <td><SeverityChip severity={e.severity} /></td>
                    <td>{e.status === "open" ? <span className="state-open">Идёт</span> : "Закрыто"}</td>
                    <td><ReviewChip review={e.review} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {total > PAGE && (
            <nav className="pager" aria-label="Страницы">
              <button type="button" className="btn btn-ghost" disabled={page <= 1} onClick={() => setQuery({ page: page - 1 })}>Назад</button>
              <span className="num">{(page - 1) * PAGE + 1}–{Math.min(page * PAGE, total)} из {formatCount(total)}</span>
              <button type="button" className="btn btn-ghost" disabled={page >= pages} onClick={() => setQuery({ page: page + 1 })}>Дальше</button>
            </nav>
          )}
        </section>
        <aside className="journal-detail">
          {selected ? <EventDetail event={selected} onChange={onChange} onClose={() => setQuery({ event: null })} /> : (
            <div className="panel"><Empty title="Выберите нарушение">Кадр, клип и разбор откроются здесь.</Empty></div>
          )}
        </aside>
      </div>
    </div>
  );
}

function Select({ label, value, onChange, options }: { label: string; value: string; onChange: (v: string) => void; options: [string, string][] }) {
  return (
    <label className="field">
      <span>{label}</span>
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">Все</option>
        {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
    </label>
  );
}
