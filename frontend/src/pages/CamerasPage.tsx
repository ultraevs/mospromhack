import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent, type PointerEvent } from "react";
import { api, errorText } from "../api";
import { Icon } from "../components/Icons";
import { LivePlayer } from "../components/LivePlayer";
import { Sign } from "../components/Sign";
import { Dot, Empty, Loading, Notice, PageHeader, Segmented } from "../components/Ui";
import { usePoll } from "../hooks/usePoll";
import { CAM_STATE_LABEL, camState } from "../lib/cameras";
import { formatTime } from "../lib/format";
import type { Rect } from "../lib/geometry";
import { DEFAULT_LAYERS, drawDraft, drawZones, type Layers } from "../lib/overlay";
import { clickDraft, EMPTY_DRAFT, hitVertex, moveVertex, removeVertex, toPolygon } from "../lib/polygon";
import { linkHandler, navigate, setQuery, useLocation } from "../lib/router";
import { EVENT_LABEL, PPE_LABEL, ZONE_COLOR, type Camera, type Health, type SafetyEvent, type Zone } from "../types";
import { CameraForm } from "./cameras/CameraForm";
import { ZonePanel, type ZoneDraft } from "./cameras/ZonePanel";

const LAYER_LABELS: [keyof Layers, string][] = [["people", "Люди"], ["ppe", "СИЗ"], ["zones", "Зоны"], ["labels", "Подписи"]];

export function CamerasPage({ tick, isAdmin }: { tick: number; isAdmin: boolean }) {
  const { search } = useLocation();
  const q = useMemo(() => new URLSearchParams(search), [search]);
  const mode = q.get("mode") === "wall" ? "wall" : "focus";
  const [cameras, setCameras] = useState<Camera[] | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState<Camera | "new" | null>(null);

  usePoll(() => {
    api.cameras().then((list) => { setCameras(list); setError(null); }).catch((e) => setError(errorText(e, "Не удалось загрузить камеры")));
  }, 3000);
  usePoll(() => {
    api.health().then(setHealth).catch(() => undefined);
  }, 5000);

  const requested = Number(q.get("camera"));
  const selected = cameras?.find((c) => c.id === requested) ?? cameras?.find((c) => c.enabled) ?? cameras?.[0] ?? null;

  const saved = (camera: Camera) => {
    setCameras((list) => (list?.some((c) => c.id === camera.id) ? list.map((c) => (c.id === camera.id ? camera : c)) : [...(list ?? []), camera]));
    setForm(null);
    setQuery({ camera: camera.id });
  };

  return (
    <div className="page page-wide">
      <PageHeader title="Камеры" actions={<>
        <Segmented label="Вид" value={mode} onChange={(v) => setQuery({ mode: v === "wall" ? "wall" : null })}
          options={[{ value: "focus", label: <><Icon name="focus" size={15} />Одна камера</> }, { value: "wall", label: <><Icon name="wall" size={15} />Видеостена</> }]} />
        {isAdmin && <button type="button" className="btn btn-primary" onClick={() => setForm("new")}><Icon name="plus" size={16} />Добавить камеру</button>}
      </>} />
      {error && <Notice>{error}</Notice>}
      {cameras === null ? <Loading label="Загружаем камеры…" /> : cameras.length === 0 ? (
        <div className="panel">
          <Empty title="Камер пока нет" action={isAdmin ? <button type="button" className="btn btn-primary" onClick={() => setForm("new")}>Добавить камеру</button> : undefined}>
            {isAdmin ? "Подключите RTSP-камеру или видеофайл из data/videos." : "Администратор ещё не подключил камеры."}
          </Empty>
        </div>
      ) : mode === "wall" ? (
        <Wall cameras={cameras} health={health} onOpen={(id) => setQuery({ mode: null, camera: id })} />
      ) : selected && (
        <Focus key={selected.id} camera={selected} cameras={cameras} health={health} tick={tick} isAdmin={isAdmin} onEdit={() => setForm(selected)} />
      )}
      {form && (
        <CameraForm camera={form === "new" ? null : form} onClose={() => setForm(null)} onSaved={saved}
          onDeleted={(id) => { setCameras((list) => list?.filter((c) => c.id !== id) ?? null); setForm(null); setQuery({ camera: null }); }} />
      )}
    </div>
  );
}

function Switcher({ cameras, health, current }: { cameras: Camera[]; health: Health | null; current: number }) {
  return (
    <nav className="switcher" aria-label="Выбор камеры">
      {cameras.map((c) => {
        const state = camState(c, health);
        return (
          <button key={c.id} type="button" aria-pressed={c.id === current} title={CAM_STATE_LABEL[state]} onClick={() => setQuery({ camera: c.id })}>
            <Dot state={state} />
            <span>{c.name}</span>
            {c.open_events > 0 && <b className="count-hot" title="Открытых нарушений">{c.open_events}</b>}
          </button>
        );
      })}
    </nav>
  );
}

function Focus({ camera, cameras, health, tick, isAdmin, onEdit }: {
  camera: Camera; cameras: Camera[]; health: Health | null; tick: number; isAdmin: boolean; onEdit: () => void;
}) {
  const { search } = useLocation();
  const tab = new URLSearchParams(search).get("tab") === "zones" ? "zones" : "events";
  const [layers, setLayers] = useState<Layers>(DEFAULT_LAYERS);
  const [zones, setZones] = useState<Zone[]>([]);
  const [events, setEvents] = useState<SafetyEvent[] | null>(null);
  const [draft, setDraft] = useState<ZoneDraft | null>(null);
  const [zoneError, setZoneError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const rectRef = useRef<Rect | null>(null);
  const dragging = useRef(-1);
  const state = camState(camera, health);

  const loadZones = useCallback(() => {
    api.zones(camera.id).then(setZones).catch((e) => setZoneError(errorText(e, "Не удалось загрузить зоны")));
  }, [camera.id]);
  useEffect(loadZones, [loadZones]);

  useEffect(() => {
    let alive = true;
    api.events({ camera_id: camera.id, limit: 12 }).then((p) => alive && setEvents(p.items)).catch(() => alive && setEvents([]));
    return () => {
      alive = false;
    };
  }, [camera.id, tick]);

  const drawExtra = useCallback((ctx: CanvasRenderingContext2D, rect: Rect) => {
    if (layers.zones || draft) drawZones(ctx, zones, rect, draft?.id);
    if (draft) drawDraft(ctx, draft.polygon, rect, ZONE_COLOR[draft.type]);
  }, [zones, draft, layers.zones]);

  const local = (e: PointerEvent | MouseEvent): [number, number] => {
    const box = e.currentTarget.getBoundingClientRect();
    return [e.clientX - box.left, e.clientY - box.top];
  };
  const onPointerDown = (e: PointerEvent) => {
    if (!draft || !rectRef.current || e.button !== 0) return;
    const [x, y] = local(e);
    if (draft.polygon.closed) {
      const hit = hitVertex(draft.polygon.points, x, y, rectRef.current);
      if (hit >= 0) {
        dragging.current = hit;
        e.currentTarget.setPointerCapture(e.pointerId);
      }
    } else {
      setDraft({ ...draft, polygon: clickDraft(draft.polygon, x, y, rectRef.current) });
    }
  };
  const onPointerMove = (e: PointerEvent) => {
    if (!draft || !rectRef.current || dragging.current < 0) return;
    const [x, y] = local(e);
    setDraft({ ...draft, polygon: moveVertex(draft.polygon, dragging.current, x, y, rectRef.current) });
  };
  const onContextMenu = (e: MouseEvent) => {
    e.preventDefault();
    if (!draft || !rectRef.current) return;
    const [x, y] = local(e);
    const hit = hitVertex(draft.polygon.points, x, y, rectRef.current);
    if (hit >= 0) setDraft({ ...draft, polygon: removeVertex(draft.polygon, hit) });
  };

  const saveZone = async () => {
    if (!draft) return;
    const body = {
      name: draft.name.trim(), type: draft.type, polygon: toPolygon(draft.polygon),
      required_ppe: draft.type === "ppe_required" ? draft.required_ppe : [],
    };
    setBusy(true);
    try {
      if (draft.id) await api.updateZone(draft.id, body);
      else await api.createZone(camera.id, body);
      setDraft(null);
      setZoneError(null);
      loadZones();
    } catch (e) {
      setZoneError(errorText(e, "Не удалось сохранить зону"));
    } finally {
      setBusy(false);
    }
  };
  const deleteZone = async () => {
    if (!draft?.id || !window.confirm(`Удалить зону «${draft.name}»?`)) return;
    setBusy(true);
    try {
      await api.deleteZone(draft.id);
      setDraft(null);
      loadZones();
    } catch (e) {
      setZoneError(errorText(e, "Не удалось удалить зону"));
    } finally {
      setBusy(false);
    }
  };

  const openTab = (next: "events" | "zones") => {
    if (next === "events") setDraft(null);
    setQuery({ tab: next === "zones" ? "zones" : null });
  };

  return (
    <>
      <Switcher cameras={cameras} health={health} current={camera.id} />
      <div className="focus">
        <section className="stage">
          <div className="stage-video">
            {camera.enabled ? (
              <LivePlayer cameraId={camera.id} layers={layers} drawExtra={drawExtra} rectRef={rectRef}>
                {draft && (
                  <div className={`zone-editor ${draft.polygon.closed ? "is-closed" : ""}`} onPointerDown={onPointerDown} onPointerMove={onPointerMove}
                    onPointerUp={() => { dragging.current = -1; }} onContextMenu={onContextMenu} />
                )}
              </LivePlayer>
            ) : (
              <div className="player player-off">
                <span>Камера выключена</span>
                {isAdmin && <button type="button" className="btn btn-ghost" onClick={onEdit}>Включить в настройках камеры</button>}
              </div>
            )}
          </div>
          <div className="stage-bar">
            <div className="stage-title">
              <h2>{camera.name}</h2>
              <span><Dot state={state} />{CAM_STATE_LABEL[state]}</span>
            </div>
            <fieldset className="layers">
              <legend className="sr-only">Что рисовать поверх видео</legend>
              {LAYER_LABELS.map(([key, label]) => (
                <label key={key} className="check">
                  <input type="checkbox" checked={layers[key]} onChange={(e) => setLayers({ ...layers, [key]: e.target.checked })} />
                  {label}
                </label>
              ))}
            </fieldset>
            {isAdmin && <button type="button" className="btn btn-ghost" onClick={onEdit}><Icon name="settings" size={15} />Настроить камеру</button>}
          </div>
          <p className="stage-meta">
            {camera.source_type === "rtsp" ? "RTSP" : "Файл"}: {camera.source}
            {camera.required_ppe.length > 0 && <> · Обязательно: {camera.required_ppe.map((p) => PPE_LABEL[p].toLowerCase()).join(", ")}</>}
          </p>
        </section>

        <aside className="side panel">
          <div className="tabs" role="tablist">
            <button type="button" role="tab" aria-selected={tab === "events"} onClick={() => openTab("events")}>Нарушения</button>
            <button type="button" role="tab" aria-selected={tab === "zones"} onClick={() => openTab("zones")}>Зоны{zones.length ? ` · ${zones.length}` : ""}</button>
          </div>
          {tab === "events" ? (
            <div className="side-events">
              {events === null ? <Loading /> : events.length === 0 ? (
                <Empty title="Нарушений на этой камере нет" />
              ) : (
                <ul className="mini-events">
                  {events.map((e) => (
                    <li key={e.id}>
                      <a href={`/events?event=${e.id}`} onClick={linkHandler(`/events?event=${e.id}`)} className={`sev-row sev-row-${e.severity}`}>
                        <Sign type={e.type} size={24} />
                        <span><strong>{EVENT_LABEL[e.type]}</strong><small>{e.zone_name ?? "вне зон"}</small></span>
                        <span className="num">{e.status === "open" ? <b className="state-open">идёт</b> : formatTime(e.started_at)}</span>
                      </a>
                    </li>
                  ))}
                </ul>
              )}
              <button type="button" className="btn btn-ghost" onClick={() => navigate(`/events?camera=${camera.id}`)}>Все нарушения камеры</button>
            </div>
          ) : (
            <ZonePanel zones={zones} draft={draft} error={zoneError} busy={busy} canEdit={isAdmin && camera.enabled}
              onNew={() => { setZoneError(null); setDraft({ name: "", type: "restricted", required_ppe: [], polygon: EMPTY_DRAFT }); }}
              onEdit={(z) => { setZoneError(null); setDraft({ id: z.id, name: z.name, type: z.type, required_ppe: z.required_ppe, polygon: { points: z.polygon, closed: true } }); }}
              onChange={setDraft} onSave={() => void saveZone()} onDelete={() => void deleteZone()} onCancel={() => setDraft(null)} />
          )}
        </aside>
      </div>
    </>
  );
}

function Wall({ cameras, health, onOpen }: { cameras: Camera[]; health: Health | null; onOpen: (id: number) => void }) {
  const on = cameras.filter((c) => c.enabled);
  const off = cameras.filter((c) => !c.enabled);
  return (
    <>
      {on.length === 0 ? <div className="panel"><Empty title="Все камеры выключены">Включите камеру в её настройках.</Empty></div> : (
        <div className="wall">
          {on.map((c) => {
            const state = camState(c, health);
            return (
              <article key={c.id} className="wall-tile">
                <button type="button" className="wall-open" onClick={() => onOpen(c.id)} aria-label={`Открыть камеру ${c.name}`}>
                  <LivePlayer cameraId={c.id} layers={{ ...DEFAULT_LAYERS, zones: false }} />
                </button>
                <footer>
                  <Dot state={state} />
                  <strong>{c.name}</strong>
                  <small>{CAM_STATE_LABEL[state]}</small>
                  {c.open_events > 0 && <b className="count-hot">{c.open_events}</b>}
                </footer>
              </article>
            );
          })}
        </div>
      )}
      {off.length > 0 && (
        <p className="wall-off">
          <span>Выключены:</span>
          {off.map((c) => <button key={c.id} type="button" className="link-btn" onClick={() => onOpen(c.id)}>{c.name}</button>)}
        </p>
      )}
    </>
  );
}
