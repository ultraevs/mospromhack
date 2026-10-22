import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { EventRow } from "../components/EventBits";
import { Player } from "../components/Player";
import { useEvents } from "../hooks/useEvents";
import { EVENT_LABEL, SEVERITY_COLOR, SEVERITY_ICON } from "../types";
import type { Camera, ClientConfig, SafetyEvent } from "../types";

const HIGHLIGHT_MS = 6000;

export function freshByCamera(events: SafetyEvent[], now: number, windowMs = HIGHLIGHT_MS): Map<number, SafetyEvent> {
  const out = new Map<number, SafetyEvent>();
  for (const e of events) {
    const age = now - new Date(e.started_at).getTime();
    if (age >= 0 && age <= windowMs && !out.has(e.camera_id)) out.set(e.camera_id, e);
  }
  return out;
}

export function CamerasPage({ config }: { config: ClientConfig }) {
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [videos, setVideos] = useState<string[]>([]);
  const [name, setName] = useState("");
  const [source, setSource] = useState("");
  const [error, setError] = useState("");
  const [now, setNow] = useState(Date.now());
  const { events } = useEvents(undefined, 50);
  const loaded = useRef(false);

  const load = () => api.cameras().then((c) => { setCameras(c); loaded.current = true; }).catch((e: Error) => setError(e.message));

  useEffect(() => {
    load();
    api.videos().then((v) => { setVideos(v.map((x) => x.name)); setSource((s) => s || v[0]?.name || ""); }).catch(() => undefined);
    const timer = setInterval(() => { load(); setNow(Date.now()); }, 2000);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => { load(); setNow(Date.now()); }, [events]);

  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await api.createCamera({ name: name || source, source_type: "file", source, required_ppe: ["helmet"] });
      setName("");
      setError("");
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const fresh = freshByCamera(events, now);
  const enabled = cameras.filter((c) => c.enabled);

  return (
    <div className="flex h-full min-h-0">
      <div className="min-w-0 flex-1 overflow-auto p-3">
        {error && <div className="mb-3 text-high">▲ {error}</div>}
        <div className="grid grid-cols-[repeat(auto-fit,minmax(380px,1fr))] gap-3">
          {enabled.map((c) => {
            const hot = fresh.get(c.id);
            return (
              <Link key={c.id} to={`/cameras/${c.id}`} data-testid={`tile-${c.id}`}
                    className="block overflow-hidden rounded border-2 bg-panel transition-colors"
                    style={{ borderColor: hot ? SEVERITY_COLOR[hot.severity] : "var(--color-line)" }}>
                <div className="flex items-center gap-3 px-3 py-1.5">
                  <span className="truncate font-medium">{c.name}</span>
                  <span className={`whitespace-nowrap text-xs ${c.status === "online" ? "text-ok" : "text-muted"}`}>
                    {c.status === "online" ? "● на связи" : "○ нет кадров"}
                  </span>
                  <span className={`ml-auto whitespace-nowrap text-xs ${c.open_events ? "text-medium" : "text-muted"}`}>
                    {c.open_events ? `◆ нарушений: ${c.open_events}` : "нарушений нет"}
                  </span>
                </div>
                <div className="relative aspect-video">
                  <Player cameraId={c.id} config={config} />
                  {hot && (
                    <div className="absolute bottom-0 left-0 right-0 px-3 py-1 text-sm font-medium text-bg"
                         style={{ background: SEVERITY_COLOR[hot.severity] }}>
                      {SEVERITY_ICON[hot.severity]} новое: {EVENT_LABEL[hot.type]}
                    </div>
                  )}
                </div>
              </Link>
            );
          })}
        </div>
        {loaded.current && enabled.length === 0 && <div className="text-muted">Камер нет. Добавь первую ниже.</div>}

        <form onSubmit={add} className="mt-4 flex max-w-2xl items-end gap-2 rounded border border-line bg-panel p-3">
          <label className="flex flex-1 flex-col gap-1 text-xs text-muted">
            Название
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Цех 2, ворота"
                   className="rounded border border-line bg-bg px-2 py-1 text-sm text-text" />
          </label>
          <label className="flex flex-1 flex-col gap-1 text-xs text-muted">
            Видеофайл из data/videos
            <select value={source} onChange={(e) => setSource(e.target.value)}
                    className="rounded border border-line bg-bg px-2 py-1 text-sm text-text">
              {videos.map((v) => <option key={v}>{v}</option>)}
            </select>
          </label>
          <button disabled={!source} className="rounded bg-accent px-3 py-1 text-sm font-medium text-bg disabled:opacity-40">
            Добавить камеру
          </button>
        </form>
      </div>

      <aside className="flex w-80 flex-col border-l border-line bg-panel">
        <div className="border-b border-line px-3 py-2 font-semibold">Последние события</div>
        <div className="min-h-0 flex-1 overflow-y-auto">
          {events.length === 0 && <div className="p-3 text-muted">Нарушений пока нет</div>}
          {events.map((e) => <EventRow key={e.id} event={e} />)}
        </div>
      </aside>
    </div>
  );
}
