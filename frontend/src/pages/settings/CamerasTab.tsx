import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api";
import { PPE_LABEL } from "../../types";
import type { Camera, PpeItem } from "../../types";

const INPUT = "rounded border border-line bg-bg px-2 py-1 text-text";
const ITEMS: PpeItem[] = ["helmet", "mask", "vest"];

interface Draft {
  id?: number;
  name: string;
  source_type: "file" | "rtsp";
  source: string;
  required_ppe: PpeItem[];
  enabled: boolean;
}

const emptyDraft = (file: string): Draft => ({ name: "", source_type: "file", source: file, required_ppe: ["helmet"], enabled: true });

export function draftError(d: Draft): string {
  if (!d.name.trim()) return "Укажи название камеры";
  if (d.source_type === "rtsp" && !/^rtsps?:\/\/\S+$/i.test(d.source.trim())) return "Адрес должен начинаться с rtsp:// или rtsps://";
  if (d.source_type === "file" && !d.source) return "Выбери видеофайл";
  return "";
}

export function CamerasTab() {
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [videos, setVideos] = useState<string[]>([]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = () => api.cameras().then(setCameras).catch((e: Error) => setError(e.message));

  useEffect(() => {
    load();
    api.videos().then((v) => setVideos(v.map((x) => x.name))).catch(() => undefined);
    const timer = setInterval(load, 3000);
    return () => clearInterval(timer);
  }, []);

  const run = async (action: () => Promise<unknown>, done?: () => void) => {
    setBusy(true);
    try {
      await action();
      setError("");
      done?.();
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const save = (e: React.FormEvent) => {
    e.preventDefault();
    if (!draft) return;
    const problem = draftError(draft);
    if (problem) return setError(problem);
    const body = { name: draft.name.trim(), source_type: draft.source_type, source: draft.source.trim(),
                   required_ppe: draft.required_ppe, enabled: draft.enabled };
    run(() => (draft.id ? api.updateCamera(draft.id, body) : api.createCamera(body)), () => setDraft(null));
  };

  const remove = (c: Camera) => {
    if (!window.confirm(`Удалить камеру «${c.name}» вместе с её зонами? События в журнале останутся.`)) return;
    run(() => api.deleteCamera(c.id), () => setDraft(null));
  };

  return (
    <div className="max-w-5xl">
      {error && <div className="mb-3 text-high">▲ {error}</div>}
      <table className="mb-3 w-full border-collapse text-left">
        <thead className="text-xs text-muted">
          <tr className="border-b border-line">
            {["Камера", "Источник", "Обязательные СИЗ", "Состояние", ""].map((h) => <th key={h} className="px-2 py-2 font-normal">{h}</th>)}
          </tr>
        </thead>
        <tbody>
          {cameras.map((c) => (
            <tr key={c.id} className={`border-b border-line ${draft?.id === c.id ? "bg-panel-2" : ""}`}>
              <td className="px-2 py-2"><Link className="hover:underline" to={`/cameras/${c.id}`}>{c.name}</Link></td>
              <td className="max-w-[320px] truncate px-2 py-2 text-muted" title={c.source}>
                {c.source_type === "file" ? "файл: " : "RTSP: "}{c.source}
              </td>
              <td className="px-2 py-2 text-muted">{c.required_ppe.map((p) => PPE_LABEL[p]).join(", ") || "—"}</td>
              <td className="whitespace-nowrap px-2 py-2">
                {!c.enabled ? <span className="text-muted">○ выключена</span>
                  : c.status === "online" ? <span className="text-ok">● на связи</span>
                  : <span className="text-medium">◆ нет кадров</span>}
              </td>
              <td className="whitespace-nowrap px-2 py-2 text-right">
                <button className="mr-3 text-accent hover:underline" disabled={busy}
                        onClick={() => { setError(""); setDraft({ id: c.id, name: c.name, source_type: c.source_type, source: c.source, required_ppe: c.required_ppe, enabled: c.enabled }); }}>
                  Изменить
                </button>
                <button className="mr-3 text-muted hover:text-text" disabled={busy}
                        onClick={() => run(() => api.updateCamera(c.id, { enabled: !c.enabled }))}>
                  {c.enabled ? "Выключить" : "Включить"}
                </button>
                <button className="text-high hover:underline" disabled={busy} onClick={() => remove(c)}>Удалить</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {cameras.length === 0 && <div className="mb-3 text-muted">Камер нет. Добавь первую.</div>}

      {!draft && (
        <button onClick={() => { setError(""); setDraft(emptyDraft(videos[0] ?? "")); }}
                className="rounded bg-accent px-4 py-1.5 font-medium text-bg">
          Добавить камеру
        </button>
      )}

      {draft && (
        <form onSubmit={save} className="max-w-2xl rounded border border-line bg-panel p-3" data-testid="camera-form">
          <h2 className="mb-3 font-medium">{draft.id ? "Изменить камеру" : "Новая камера"}</h2>
          <label className="mb-3 flex flex-col gap-1 text-xs text-muted">
            Название
            <input autoFocus className={INPUT} value={draft.name} placeholder="Цех 2, ворота"
                   onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
          </label>
          <div className="mb-3 flex gap-4 text-sm">
            {([["file", "Видеофайл"], ["rtsp", "Камера по RTSP"]] as const).map(([type, title]) => (
              <label key={type} className="flex items-center gap-2">
                <input type="radio" name="source_type" checked={draft.source_type === type}
                       onChange={() => setDraft({ ...draft, source_type: type, source: type === "file" ? videos[0] ?? "" : "" })} />
                {title}
              </label>
            ))}
          </div>
          {draft.source_type === "file" ? (
            <label className="mb-3 flex flex-col gap-1 text-xs text-muted">
              Файл из data/videos (крутится по кругу)
              <select className={INPUT} value={draft.source} onChange={(e) => setDraft({ ...draft, source: e.target.value })}>
                {!videos.includes(draft.source) && <option value={draft.source}>{draft.source || "файлов нет"}</option>}
                {videos.map((v) => <option key={v}>{v}</option>)}
              </select>
            </label>
          ) : (
            <label className="mb-3 flex flex-col gap-1 text-xs text-muted">
              Адрес потока
              <input className={INPUT} value={draft.source} placeholder="rtsp://user:password@192.168.1.10:554/stream1"
                     onChange={(e) => setDraft({ ...draft, source: e.target.value })} />
            </label>
          )}
          <div className="mb-3 text-xs text-muted">
            Обязательные СИЗ для всего кадра
            <div className="mt-1 flex gap-4 text-sm text-text">
              {ITEMS.map((item) => (
                <label key={item} className="flex items-center gap-2">
                  <input type="checkbox" checked={draft.required_ppe.includes(item)}
                         onChange={(e) => setDraft({ ...draft, required_ppe: e.target.checked
                           ? [...draft.required_ppe, item] : draft.required_ppe.filter((p) => p !== item) })} />
                  {PPE_LABEL[item]}
                </label>
              ))}
            </div>
          </div>
          <label className="mb-3 flex items-center gap-2 text-sm">
            <input type="checkbox" checked={draft.enabled} onChange={(e) => setDraft({ ...draft, enabled: e.target.checked })} />
            Камера включена
          </label>
          <div className="flex gap-2">
            <button disabled={busy} className="rounded bg-accent px-4 py-1.5 font-medium text-bg disabled:opacity-40">Сохранить</button>
            <button type="button" onClick={() => { setDraft(null); setError(""); }} className="rounded border border-line px-4 py-1.5 text-muted hover:text-text">
              Отмена
            </button>
          </div>
        </form>
      )}
    </div>
  );
}
