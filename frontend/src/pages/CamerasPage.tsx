import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import type { Camera } from "../types";

export function CamerasPage() {
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [videos, setVideos] = useState<string[]>([]);
  const [name, setName] = useState("");
  const [source, setSource] = useState("");
  const [error, setError] = useState("");

  const load = () => api.cameras().then(setCameras).catch((e: Error) => setError(e.message));

  useEffect(() => {
    load();
    api.videos().then((v) => { setVideos(v.map((x) => x.name)); setSource((s) => s || v[0]?.name || ""); }).catch(() => undefined);
    const timer = setInterval(load, 3000);
    return () => clearInterval(timer);
  }, []);

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

  return (
    <div className="p-4">
      <h1 className="mb-3 text-base font-semibold">Камеры</h1>
      {error && <div className="mb-3 text-high">▲ {error}</div>}
      <div className="grid grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-3">
        {cameras.map((c) => (
          <Link key={c.id} to={`/cameras/${c.id}`} className="rounded border border-line bg-panel p-3 hover:border-accent">
            <div className="font-medium">{c.name}</div>
            <div className="text-xs text-muted">{c.source_type === "file" ? "файл" : "RTSP"}: {c.source}</div>
            <div className="mt-2 flex justify-between text-xs">
              <span className={c.status === "online" ? "text-ok" : "text-muted"}>{c.status === "online" ? "● на связи" : "○ нет кадров"}</span>
              <span className={c.open_events ? "text-medium" : "text-muted"}>открытых нарушений: {c.open_events}</span>
            </div>
          </Link>
        ))}
        {cameras.length === 0 && <div className="text-muted">Камер нет. Добавь первую ниже.</div>}
      </div>

      <form onSubmit={add} className="mt-6 flex max-w-2xl items-end gap-2 rounded border border-line bg-panel p-3">
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
  );
}
