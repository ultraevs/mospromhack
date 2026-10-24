import { useEffect, useState, type FormEvent } from "react";
import { api, errorText } from "../../api";
import { Modal, Notice, Segmented, Toggle } from "../../components/Ui";
import { PPE_ITEMS, PPE_LABEL, type Camera, type CameraInput, type PpeItem } from "../../types";

export function CameraForm({ camera, onClose, onSaved, onDeleted }: {
  camera: Camera | null;
  onClose: () => void;
  onSaved: (camera: Camera) => void;
  onDeleted: (id: number) => void;
}) {
  const [form, setForm] = useState<CameraInput>(() => camera
    ? { name: camera.name, source_type: camera.source_type, source: camera.source, required_ppe: camera.required_ppe, enabled: camera.enabled }
    : { name: "", source_type: "file", source: "", required_ppe: ["helmet"], enabled: true });
  const [videos, setVideos] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.videos().then((list) => setVideos(list.map((v) => v.name))).catch(() => undefined);
  }, []);

  const set = <K extends keyof CameraInput>(key: K, value: CameraInput[K]) => setForm((f) => ({ ...f, [key]: value }));
  const togglePpe = (item: PpeItem) =>
    set("required_ppe", form.required_ppe.includes(item) ? form.required_ppe.filter((p) => p !== item) : [...form.required_ppe, item]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      const body = { ...form, name: form.name.trim(), source: form.source.trim() };
      onSaved(camera ? await api.updateCamera(camera.id, body) : await api.createCamera(body));
    } catch (reason) {
      setError(errorText(reason, "Не удалось сохранить камеру"));
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!camera || !window.confirm(`Удалить камеру «${camera.name}» вместе с её зонами? Журнал нарушений останется.`)) return;
    setBusy(true);
    try {
      await api.deleteCamera(camera.id);
      onDeleted(camera.id);
    } catch (reason) {
      setError(errorText(reason, "Не удалось удалить камеру"));
      setBusy(false);
    }
  };

  const fileMissing = form.source_type === "file" && form.source && videos.length > 0 && !videos.includes(form.source);

  return (
    <Modal title={camera ? `Камера «${camera.name}»` : "Новая камера"} onClose={onClose}>
      <form className="form" onSubmit={submit}>
        <label className="field">
          <span>Название</span>
          <input value={form.name} onChange={(e) => set("name", e.target.value)} placeholder="Цех 2, ворота" required />
        </label>
        <div className="field">
          <span>Источник</span>
          <Segmented label="Тип источника" value={form.source_type} onChange={(v) => setForm((f) => ({ ...f, source_type: v, source: "" }))}
            options={[{ value: "file", label: "Видеофайл" }, { value: "rtsp", label: "RTSP-камера" }]} />
        </div>
        {form.source_type === "file" ? (
          <label className="field">
            <span>Файл из data/videos</span>
            <select value={form.source} onChange={(e) => set("source", e.target.value)} required>
              <option value="" disabled>Выберите видео</option>
              {fileMissing && <option value={form.source}>{form.source} (нет на диске)</option>}
              {videos.map((v) => <option key={v} value={v}>{v}</option>)}
            </select>
            {videos.length === 0 && <small>В data/videos нет видео. Положите туда .mp4 и откройте форму снова.</small>}
          </label>
        ) : (
          <label className="field">
            <span>Адрес потока</span>
            <input value={form.source} onChange={(e) => set("source", e.target.value)} placeholder="rtsp://user:pass@10.0.0.5:554/stream1"
              pattern="rtsps?://.+" title="Адрес вида rtsp://…" required />
          </label>
        )}
        <fieldset className="field">
          <legend>Обязательные СИЗ в кадре</legend>
          <div className="checks">
            {PPE_ITEMS.map((item) => (
              <label key={item} className="check">
                <input type="checkbox" checked={form.required_ppe.includes(item)} onChange={() => togglePpe(item)} />
                {PPE_LABEL[item]}
              </label>
            ))}
          </div>
          <small>В зонах «Обязательны СИЗ» можно потребовать дополнительные.</small>
        </fieldset>
        <div className="field-inline">
          <Toggle checked={form.enabled} onChange={(v) => set("enabled", v)} label="Камера включена" />
          <span>{form.enabled ? "Камера включена" : "Камера выключена, анализа нет"}</span>
        </div>
        {error && <Notice>{error}</Notice>}
        <div className="form-actions">
          {camera && <button type="button" className="btn btn-danger" disabled={busy} onClick={() => void remove()}>Удалить камеру</button>}
          <span className="spacer" />
          <button type="button" className="btn btn-ghost" onClick={onClose}>Отмена</button>
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? "Сохраняем…" : camera ? "Сохранить" : "Добавить камеру"}</button>
        </div>
      </form>
    </Modal>
  );
}
