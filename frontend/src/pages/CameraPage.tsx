import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import { EventRow } from "../components/EventBits";
import { Player } from "../components/Player";
import { useEvents } from "../hooks/useEvents";
import { DEFAULT_LAYERS } from "../lib/overlay";
import type { Layers } from "../lib/overlay";
import type { Camera, ClientConfig } from "../types";

const LAYER_LABELS: [keyof Layers, string][] = [
  ["people", "Люди"],
  ["ppe", "СИЗ"],
  ["zones", "Зоны"],
  ["labels", "Подписи"],
];

export function CameraPage({ config }: { config: ClientConfig }) {
  const cameraId = Number(useParams().id);
  const [camera, setCamera] = useState<Camera | null>(null);
  const [error, setError] = useState("");
  const [layers, setLayers] = useState<Layers>(DEFAULT_LAYERS);
  const { events } = useEvents(cameraId);

  useEffect(() => {
    let alive = true;
    const load = () => api.camera(cameraId).then((c) => alive && setCamera(c)).catch((e: Error) => alive && setError(e.message));
    load();
    const timer = setInterval(load, 3000);
    return () => { alive = false; clearInterval(timer); };
  }, [cameraId]);

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
          <Player cameraId={cameraId} config={config} layers={layers} />
        </div>
      </div>
      <aside className="flex w-80 flex-col border-l border-line bg-panel">
        <div className="border-b border-line px-3 py-2 font-semibold">События камеры</div>
        <div className="min-h-0 flex-1 overflow-y-auto">
          {events.length === 0 && <div className="p-3 text-muted">Нарушений пока нет</div>}
          {events.map((e) => <EventRow key={e.id} event={e} />)}
        </div>
      </aside>
    </div>
  );
}
