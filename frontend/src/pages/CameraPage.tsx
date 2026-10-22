import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import { EventRow } from "../components/EventBits";
import { Player } from "../components/Player";
import { ZonePanel } from "../components/ZonePanel";
import type { ZoneDraft } from "../components/ZonePanel";
import { useEvents } from "../hooks/useEvents";
import type { Rect } from "../lib/geometry";
import { DEFAULT_LAYERS } from "../lib/overlay";
import type { Layers } from "../lib/overlay";
import { clickDraft, EMPTY_DRAFT, hitVertex, moveVertex, removeVertex, toPolygon } from "../lib/polygon";
import { drawDraft, drawZones, ZONE_COLOR } from "../lib/zones";
import { PPE_LABEL } from "../types";
import type { Camera, ClientConfig, PpeItem, Zone } from "../types";

const LAYER_LABELS: [keyof Layers, string][] = [
  ["people", "Люди"],
  ["ppe", "СИЗ"],
  ["zones", "Зоны"],
  ["labels", "Подписи"],
];
const ITEMS: PpeItem[] = ["helmet", "mask", "vest"];

export function CameraPage({ config }: { config: ClientConfig }) {
  const cameraId = Number(useParams().id);
  const [camera, setCamera] = useState<Camera | null>(null);
  const [zones, setZones] = useState<Zone[]>([]);
  const [error, setError] = useState("");
  const [layers, setLayers] = useState<Layers>(DEFAULT_LAYERS);
  const [tab, setTab] = useState<"events" | "zones">("events");
  const [draft, setDraft] = useState<ZoneDraft | null>(null);
  const [zoneError, setZoneError] = useState("");
  const { events } = useEvents(cameraId);

  const rectRef = useRef<Rect | null>(null);
  const dragging = useRef(-1);

  const loadZones = useCallback(() => api.zones(cameraId).then(setZones).catch((e: Error) => setZoneError(e.message)), [cameraId]);

  useEffect(() => {
    let alive = true;
    const load = () => api.camera(cameraId).then((c) => alive && setCamera(c)).catch((e: Error) => alive && setError(e.message));
    load();
    loadZones();
    const timer = setInterval(load, 3000);
    return () => { alive = false; clearInterval(timer); };
  }, [cameraId, loadZones]);

  const drawExtra = useCallback((ctx: CanvasRenderingContext2D, rect: Rect) => {
    if (layers.zones || draft) drawZones(ctx, zones, rect, draft?.id);
    if (draft) drawDraft(ctx, draft.polygon, rect, ZONE_COLOR[draft.type]);
  }, [zones, draft, layers.zones]);


  const local = (e: React.PointerEvent | React.MouseEvent): [number, number] => {
    const box = e.currentTarget.getBoundingClientRect();
    return [e.clientX - box.left, e.clientY - box.top];
  };

  const onPointerDown = (e: React.PointerEvent) => {
    if (!draft || !rectRef.current || e.button !== 0) return;
    const [x, y] = local(e);
    const hit = hitVertex(draft.polygon.points, x, y, rectRef.current);
    if (draft.polygon.closed) {
      if (hit >= 0) {
        dragging.current = hit;
        try {
          e.currentTarget.setPointerCapture(e.pointerId);
        } catch {
        }
      }
    } else {
      setDraft({ ...draft, polygon: clickDraft(draft.polygon, x, y, rectRef.current) });
    }
  };

  const onPointerMove = (e: React.PointerEvent) => {
    if (!draft || !rectRef.current || dragging.current < 0) return;
    const [x, y] = local(e);
    setDraft({ ...draft, polygon: moveVertex(draft.polygon, dragging.current, x, y, rectRef.current) });
  };

  const onContextMenu = (e: React.MouseEvent) => {
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
    try {
      if (draft.id) await api.updateZone(draft.id, body);
      else await api.createZone(cameraId, body);
      setDraft(null);
      setZoneError("");
      loadZones();
    } catch (e) {
      setZoneError(e instanceof Error ? e.message : String(e));
    }
  };

  const deleteZone = async () => {
    if (!draft?.id) return;
    try {
      await api.deleteZone(draft.id);
      setDraft(null);
      loadZones();
    } catch (e) {
      setZoneError(e instanceof Error ? e.message : String(e));
    }
  };

  const toggleCameraPpe = async (item: PpeItem, on: boolean) => {
    if (!camera) return;
    const required_ppe = on ? [...camera.required_ppe, item] : camera.required_ppe.filter((p) => p !== item);
    try {
      setCamera(await api.updateCamera(camera.id, { required_ppe }));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  if (error) return <div className="p-6 text-high">▲ Камера недоступна: {error}. <Link className="underline" to="/">К списку</Link></div>;
  if (!camera) return <div className="p-6 text-muted">Загрузка…</div>;

  return (
    <div className="flex h-full min-h-0">
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-4 border-b border-line px-4 py-2">
          <h1 className="text-base font-semibold">{camera.name}</h1>
          <span className={camera.status === "online" ? "text-ok" : "text-muted"}>
            {camera.status === "online" ? "● на связи" : "○ нет кадров"}
          </span>
          <div className="ml-auto flex items-center gap-3">
            {LAYER_LABELS.map(([key, text]) => (
              <label key={key} className="flex cursor-pointer items-center gap-1 text-muted">
                <input type="checkbox" checked={layers[key]} onChange={(e) => setLayers({ ...layers, [key]: e.target.checked })} />
                {text}
              </label>
            ))}
          </div>
        </div>
        <div className="min-h-0 flex-1">
          <Player cameraId={cameraId} config={config} layers={layers} drawExtra={drawExtra} rectRef={rectRef}>
            {draft && (
              <div data-testid="zone-editor" className="absolute inset-0"
                   style={{ cursor: draft.polygon.closed ? "grab" : "crosshair" }}
                   onPointerDown={onPointerDown} onPointerMove={onPointerMove}
                   onPointerUp={() => { dragging.current = -1; }} onContextMenu={onContextMenu} />
            )}
          </Player>
        </div>
      </div>
      <aside className="flex w-80 flex-col border-l border-line bg-panel">
        <div className="flex border-b border-line">
          {([["events", "События"], ["zones", "Зоны и СИЗ"]] as const).map(([key, text]) => (
            <button key={key} onClick={() => { setTab(key); if (key === "events") setDraft(null); }}
                    className={`flex-1 px-3 py-2 font-semibold ${tab === key ? "border-b-2 border-accent text-text" : "text-muted"}`}>
              {text}
            </button>
          ))}
        </div>
        {tab === "events" ? (
          <div className="min-h-0 flex-1 overflow-y-auto">
            {events.length === 0 && <div className="p-3 text-muted">Нарушений пока нет</div>}
            {events.map((e) => <EventRow key={e.id} event={e} />)}
          </div>
        ) : (
          <>
            {!draft && (
              <fieldset className="border-b border-line p-3 text-sm">
                <legend className="text-xs text-muted">Обязательные СИЗ на всём кадре</legend>
                <div className="mt-1 flex gap-4">
                  {ITEMS.map((item) => (
                    <label key={item} className="flex cursor-pointer items-center gap-1">
                      <input type="checkbox" checked={camera.required_ppe.includes(item)}
                             onChange={(e) => toggleCameraPpe(item, e.target.checked)} />
                      {PPE_LABEL[item]}
                    </label>
                  ))}
                </div>
              </fieldset>
            )}
            <ZonePanel
              zones={zones} draft={draft} error={zoneError}
              onNew={() => { setZoneError(""); setDraft({ name: "", type: "restricted", required_ppe: [], polygon: EMPTY_DRAFT }); }}
              onEdit={(z) => { setZoneError(""); setDraft({ id: z.id, name: z.name, type: z.type, required_ppe: z.required_ppe, polygon: { points: z.polygon, closed: true } }); }}
              onChange={setDraft} onSave={saveZone} onDelete={deleteZone} onCancel={() => setDraft(null)}
            />
          </>
        )}
      </aside>
    </div>
  );
}
