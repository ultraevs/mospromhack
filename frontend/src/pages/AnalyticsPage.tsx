import { useEffect, useState } from "react";
import { api, errorText } from "../api";
import { Sign } from "../components/Sign";
import { Empty, Figure, Loading, Notice, PageHeader, Segmented } from "../components/Ui";
import { formatCount, formatDay, formatPercent, localDayStart, timezoneLabel } from "../lib/format";
import { linkHandler } from "../lib/router";
import { EVENT_LABEL, type AnalyticsSummary, type Camera } from "../types";

type Period = "today" | "7" | "30" | "all";

function todayIso(): string {
  const d = new Date();
  return localDayStart(`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`);
}

export function AnalyticsPage({ tick }: { tick: number }) {
  const [period, setPeriod] = useState<Period>("7");
  const [camera, setCamera] = useState("");
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [data, setData] = useState<AnalyticsSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.cameras().then(setCameras).catch(() => undefined);
  }, []);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    const range = period === "all" ? {} : period === "today" ? { from: todayIso() } : { days: Number(period) };
    api.analytics({ ...range, camera_id: camera || undefined, tz_offset_min: -new Date().getTimezoneOffset() })
      .then((d) => { if (alive) { setData(d); setError(null); } })
      .catch((e) => alive && setError(errorText(e, "Не удалось загрузить аналитику")))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [period, camera, tick]);

  const reviewed = data ? data.total - data.unreviewed : 0;
  const types = data?.by_type.filter((t) => t.total > 0).sort((a, b) => b.total - a.total) ?? [];

  return (
    <div className="page">
      <PageHeader title="Аналитика" description={`Время по часовому поясу браузера, ${timezoneLabel()}`} actions={<>
        <label className="field field-inline-label">
          <span>Камера</span>
          <select value={camera} onChange={(e) => setCamera(e.target.value)}>
            <option value="">Все камеры</option>
            {cameras.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </label>
        <Segmented label="Период" value={period} onChange={setPeriod}
          options={[{ value: "today", label: "Сегодня" }, { value: "7", label: "7 дней" }, { value: "30", label: "30 дней" }, { value: "all", label: "Всё время" }]} />
      </>} />
      {error && <Notice>{error}</Notice>}
      {!data ? <Loading /> : data.total === 0 ? (
        <div className="panel"><Empty title="За этот период нарушений нет">Выберите период длиннее или другую камеру.</Empty></div>
      ) : (
        <div className={`analytics ${loading ? "is-loading" : ""}`}>
          <section className="figures" aria-label="Итоги периода">
            <Figure label="Нарушений" value={formatCount(data.total)} />
            <Figure label="Разобрано" value={formatCount(reviewed)} note={formatPercent(reviewed / data.total)} />
            <Figure label="Подтверждено" value={formatCount(data.confirmed)} />
            <Figure label="Ложных" value={formatCount(data.false_positive)} note={data.false_share !== null ? `${formatPercent(data.false_share)} от разобранных` : "ещё не размечали"} />
            <Figure label="Передано мастеру" value={formatCount(data.forwarded)} />
          </section>

          <section className="panel">
            <h2 className="panel-title">По часам</h2>
            <Bars items={data.by_hour.map((h) => ({ key: String(h.hour), value: h.total, label: h.hour % 3 === 0 ? String(h.hour).padStart(2, "0") : "", title: `${h.hour}:00–${h.hour + 1}:00` }))} />
          </section>

          {data.by_day.length > 1 && (
            <section className="panel">
              <h2 className="panel-title">По дням</h2>
              <Bars items={data.by_day.map((d, i) => ({ key: d.day, value: d.total, label: data.by_day.length <= 14 || i % 3 === 0 ? formatDay(d.day) : "", title: formatDay(d.day) }))} />
            </section>
          )}

          <section className="panel">
            <h2 className="panel-title">Типы нарушений и ложные срабатывания</h2>
            <table className="table">
              <thead><tr><th>Нарушение</th><th className="num">Всего</th><th className="num">Подтверждено</th><th className="num">Ложных</th><th className="num">Доля ложных</th></tr></thead>
              <tbody>
                {types.map((t) => (
                  <tr key={t.type}>
                    <td><a className="event-name" href={`/events?type=${t.type}`} onClick={linkHandler(`/events?type=${t.type}`)}><Sign type={t.type} size={20} />{EVENT_LABEL[t.type]}</a></td>
                    <td className="num">{formatCount(t.total)}</td>
                    <td className="num">{formatCount(t.confirmed)}</td>
                    <td className="num">{formatCount(t.false_positive)}</td>
                    <td className="num">{formatPercent(t.false_share)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          <div className="analytics-pair">
            <section className="panel">
              <h2 className="panel-title">Камеры</h2>
              <Ranking items={data.by_camera.map((c) => ({ key: String(c.camera_id), label: c.camera_name, value: c.total }))} />
            </section>
            <section className="panel">
              <h2 className="panel-title">Зоны</h2>
              {data.by_zone.length === 0 ? <Empty title="В зонах нарушений не было">Вне зон: {formatCount(data.without_zone)}</Empty> : (
                <Ranking items={[
                  ...data.by_zone.map((z) => ({ key: String(z.zone_id), label: `${z.zone_name ?? "без названия"}, ${z.camera_name}`, value: z.total })),
                  ...(data.without_zone ? [{ key: "none", label: "Вне зон", value: data.without_zone }] : []),
                ]} />
              )}
            </section>
          </div>
        </div>
      )}
    </div>
  );
}


function Bars({ items }: { items: { key: string; value: number; label: string; title: string }[] }) {
  const max = Math.max(1, ...items.map((i) => i.value));
  return (
    <div className="bars" role="img" aria-label={items.map((i) => `${i.title}: ${i.value}`).join(", ")}>
      {items.map((i) => (
        <div key={i.key} className="bar" title={`${i.title}: ${i.value}`}>
          <span className="bar-value num">{i.value || ""}</span>
          <i style={{ height: `${(i.value / max) * 100}%` }} />
          <small>{i.label}</small>
        </div>
      ))}
    </div>
  );
}

function Ranking({ items }: { items: { key: string; label: string; value: number }[] }) {
  const max = Math.max(1, ...items.map((i) => i.value));
  return (
    <ul className="ranking">
      {items.map((i) => (
        <li key={i.key}>
          <span>{i.label}</span>
          <i style={{ width: `${(i.value / max) * 100}%` }} />
          <b className="num">{formatCount(i.value)}</b>
        </li>
      ))}
    </ul>
  );
}
