import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { api } from "../api";
import { EmptyState, PageHeader, PageLoader } from "../App";
import { Icon } from "../components/Icons";
import { LiveVideo } from "../components/LiveVideo";
import type { Camera, PpeItem, Zone, ZoneType } from "../types";
import type { PageProps } from "./types";

const ppeOptions: { value: PpeItem; label: string }[] = [{ value: "helmet", label: "Каска" }, { value: "mask", label: "Маска" }, { value: "vest", label: "Жилет" }];

export function CamerasPage({ refreshKey, userRole }: PageProps) {
  const canManage = userRole === "admin";
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [selected, setSelected] = useState<Camera | null>(null);
  const [zones, setZones] = useState<Zone[]>([]);
  const [viewMode, setViewMode] = useState<"focus" | "grid">("focus");
  const [showForm, setShowForm] = useState(false);
  const [showZoneForm, setShowZoneForm] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const hasLoadedRef = useRef(false);

  const load = useCallback(() => {
    setLoading(!hasLoadedRef.current);
    api.cameras().then((items) => {
      setCameras(items);
      setSelected((current) => current ? items.find((item) => item.id === current.id) ?? null : items.find((item) => item.status === "online") ?? items[0] ?? null);
      setError(null);
    }).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "Не удалось загрузить камеры")).finally(() => {
      hasLoadedRef.current = true;
      setLoading(false);
    });
  }, []);
  useEffect(() => { void load(); }, [load, refreshKey]);
  useEffect(() => {
    const timer = window.setInterval(() => void load(), 3000);
    return () => window.clearInterval(timer);
  }, [load]);
  useEffect(() => { if (!selected) { setZones([]); return; } api.zones(selected.id).then(setZones).catch(() => setZones([])); }, [selected?.id]);

  async function removeCamera(camera: Camera) {
    if (!window.confirm(`Удалить камеру «${camera.name}» вместе с зонами?`)) return;
    try { await api.deleteCamera(camera.id); setSelected(null); load(); } catch (reason) { setError(reason instanceof Error ? reason.message : "Не удалось удалить камеру"); }
  }
  async function toggleCamera(camera: Camera) {
    try { const updated = await api.updateCamera(camera.id, { enabled: !camera.enabled }); setCameras((items) => items.map((item) => item.id === updated.id ? updated : item)); setSelected(updated); } catch (reason) { setError(reason instanceof Error ? reason.message : "Не удалось изменить камеру"); }
  }
  async function removeZone(zone: Zone) {
    try { await api.deleteZone(zone.id); setZones((items) => items.filter((item) => item.id !== zone.id)); } catch (reason) { setError(reason instanceof Error ? reason.message : "Не удалось удалить зону"); }
  }

  const online = cameras.filter((camera) => camera.status === "online").length;
  const selectedCamera = selected ?? cameras[0] ?? null;

  return <div className="page-stack camera-page">
    <PageHeader title="Мониторинг камер" description="Главный экран оператора: live-кадр, угрозы и состояние источников в одном рабочем поле." action={<div className="camera-page-actions"><div className="camera-view-switch" role="group" aria-label="Режим просмотра камер"><button className={viewMode === "focus" ? "camera-view-button is-active" : "camera-view-button"} type="button" aria-pressed={viewMode === "focus"} onClick={() => setViewMode("focus")}><Icon name="camera" size={15} />Фокус</button><button className={viewMode === "grid" ? "camera-view-button is-active" : "camera-view-button"} type="button" aria-pressed={viewMode === "grid"} onClick={() => setViewMode("grid")}><Icon name="grid" size={15} />Сетка</button></div>{canManage && <button className="button button-primary" type="button" onClick={() => setShowForm(true)}><Icon name="plus" size={16} />Добавить камеру</button>}</div>} />
    {error && <div className="error-notice" role="alert"><Icon name="alert" size={17} /><span>{error}</span></div>}
    <section className="camera-command-status" aria-label="Состояние видеоконтура"><span className="camera-command-live"><span className="status-dot status-dot-live" />Live-контур</span><span><strong>{online}</strong> из {cameras.length} источников в сети</span><span className="camera-command-hint">{viewMode === "grid" ? "Нажмите на кадр, чтобы открыть его в фокусе" : "Выберите источник справа, чтобы сменить основной кадр"}</span></section>
    {loading ? <PageLoader kind="camera" label="Подключаем видеостену…" /> : viewMode === "grid" ? <CameraGrid cameras={cameras} selectedCameraId={selectedCamera?.id ?? null} canManage={canManage} onSelect={setSelected} onToggle={toggleCamera} /> : selectedCamera ? <section className="camera-command-center">
      <div className="primary-camera">
        <header className="primary-camera-header"><div><h2>{selectedCamera.name}</h2><span>{selectedCamera.source_type === "rtsp" ? "RTSP-поток" : selectedCamera.source}</span></div><span className={selectedCamera.status === "online" ? "camera-state-online" : "camera-state-offline"}><span className="camera-row-dot is-online" />{selectedCamera.status === "online" ? "В сети" : selectedCamera.enabled ? "Подключение" : "Отключена"}</span></header>
        <div className="primary-camera-stage">{selectedCamera.enabled ? <LiveVideo cameraId={selectedCamera.id} zones={zones} /> : <div className="primary-camera-offline"><Icon name="camera" size={32} /><span>Камера отключена</span></div>}<span className="primary-camera-label">КАДР В РЕАЛЬНОМ ВРЕМЕНИ</span></div>
        <footer className="primary-camera-footer"><div><span>Источник</span><strong>{selectedCamera.source}</strong></div><div><span>Зоны контроля</span><strong>{zones.length ? `${zones.length} настроено` : "Не настроены"}</strong></div><div><span>Открытые угрозы</span><strong className={selectedCamera.open_events ? "text-danger" : "text-green"}>{selectedCamera.open_events || "Нет"}</strong></div>{canManage && <div className="primary-camera-actions"><button className="button button-secondary" type="button" onClick={() => setShowZoneForm(true)}><Icon name="plus" size={14} />Добавить зону</button><button className="button button-danger-ghost" type="button" onClick={() => removeCamera(selectedCamera)}><Icon name="trash" size={14} />Удалить</button></div>}</footer>
      </div>
      <aside className="camera-wall-rail"><div className="camera-wall-rail-header"><div><span className="table-caption">Видеостена</span><h2>Источники</h2></div><span>{cameras.length}</span></div><div className="camera-wall-list">{cameras.map((camera) => <CameraWallRow key={camera.id} camera={camera} active={selectedCamera.id === camera.id} canManage={canManage} onSelect={() => setSelected(camera)} onToggle={() => toggleCamera(camera)} />)}</div><div className="camera-zone-list"><div className="camera-zone-list-head"><span>Зоны выбранной камеры</span>{canManage && <button className="icon-button" type="button" aria-label="Добавить зону" onClick={() => setShowZoneForm(true)}><Icon name="plus" size={14} /></button>}</div>{zones.length ? zones.map((zone) => <div className="camera-zone-row" key={zone.id}><span className={`zone-symbol zone-${zone.type}`} /><span>{zone.name}</span>{canManage && <button className="icon-button" type="button" aria-label={`Удалить зону ${zone.name}`} onClick={() => removeZone(zone)}><Icon name="trash" size={13} /></button>}</div>) : <p>{canManage ? "Нет зон — добавьте контур прямо в рабочее поле." : "Для этой камеры зоны не настроены."}</p>}</div><div className="camera-wall-legend"><span><i className="legend-threat" />угроза</span><span><i className="legend-zone" />зона</span><span><i className="legend-live" />поток</span></div></aside>
    </section> : <section className="panel"><EmptyState icon="camera" title="Контур пока пуст" description={canManage ? "Подключите RTSP-камеру или загрузите видеофайл для первого тестового потока." : "Администратор ещё не подключил источники видео."} action={canManage ? <button className="button button-primary" type="button" onClick={() => setShowForm(true)}><Icon name="plus" size={15} />Подключить источник</button> : undefined} /></section>}
    {showForm && <CameraForm onClose={() => setShowForm(false)} onCreated={(camera) => { setShowForm(false); setCameras((items) => [...items, camera]); setSelected(camera); }} onError={setError} />}
    {showZoneForm && selectedCamera && <ZoneForm camera={selectedCamera} onClose={() => setShowZoneForm(false)} onCreated={(zone) => { setZones((items) => [...items, zone]); setShowZoneForm(false); }} onError={setError} />}
  </div>;
}

function CameraGrid({ cameras, selectedCameraId, canManage, onSelect, onToggle }: { cameras: Camera[]; selectedCameraId: number | null; canManage: boolean; onSelect: (camera: Camera) => void; onToggle: (camera: Camera) => void }) {
  if (!cameras.length) {
    return <section className="camera-grid-empty panel"><Icon name="camera" size={24} /><strong>Нет источников для видеостены</strong><span>Добавьте камеру, чтобы открыть сеточный просмотр.</span></section>;
  }

  return <section className="camera-grid-view" aria-label="Сетка камер">
    <header className="camera-grid-header"><div><span className="table-caption">Видеостена</span><h2>Все источники</h2></div><span>{cameras.length} камер · {cameras.filter((camera) => camera.enabled).length} включено</span></header>
    <div className="camera-grid-list">{cameras.map((camera) => <article className={`camera-grid-tile ${selectedCameraId === camera.id ? "is-selected" : ""}`} key={camera.id}>
      <button className="camera-grid-frame" type="button" onClick={() => onSelect(camera)} aria-label={`Открыть камеру ${camera.name}`}>
        {camera.enabled ? <LiveVideo cameraId={camera.id} className="camera-grid-video" overlays={false} /> : <div className="camera-grid-offline"><Icon name="camera" size={22} /><span>Источник отключён</span></div>}
        <span className="camera-grid-frame-label">{camera.source_type === "rtsp" ? "RTSP" : "ФАЙЛ"}</span>
      </button>
      <div className="camera-grid-tile-meta"><div><strong>{camera.name}</strong><span>{camera.source}</span></div><div className="camera-grid-tile-actions"><span className={`camera-grid-status ${camera.status === "online" ? "is-online" : camera.enabled ? "is-connecting" : "is-offline"}`}><i />{camera.status === "online" ? "В сети" : camera.enabled ? "Подключается" : "Отключена"}</span>{canManage && <button className={`toggle ${camera.enabled ? "toggle-on" : ""}`} type="button" aria-label={`${camera.enabled ? "Отключить" : "Включить"} камеру ${camera.name}`} aria-pressed={camera.enabled} onClick={() => onToggle(camera)}><span /></button>}</div></div>
      <button className="camera-grid-open" type="button" onClick={() => onSelect(camera)}>Открыть в фокусе <Icon name="arrow" size={14} /></button>
    </article>)}</div>
  </section>;
}

function CameraWallRow({ camera, active, canManage, onSelect, onToggle }: { camera: Camera; active: boolean; canManage: boolean; onSelect: () => void; onToggle: () => void }) {
  return <article className={`camera-wall-row ${active ? "camera-wall-row-active" : ""}`}><button className="camera-wall-row-main" type="button" onClick={onSelect}><span className={`camera-wall-thumb ${camera.status === "online" ? "camera-wall-thumb-live" : ""}`}>{camera.enabled ? <LiveVideo cameraId={camera.id} className="camera-wall-preview" overlays={false} /> : <><span className="camera-wall-thumb-grid" /><Icon name="camera" size={17} /></>}</span><span><strong>{camera.name}</strong><small>{camera.source_type === "rtsp" ? "RTSP" : "Видео-файл"}</small></span></button><div className="camera-wall-row-meta"><span className={camera.status === "online" ? "camera-state-online" : "camera-state-offline"}>{camera.status === "online" ? "Live" : camera.enabled ? "Подключение" : "Offline"}</span><span className={camera.open_events ? "camera-events camera-events-hot" : "camera-events"}>{camera.open_events || "—"}</span>{canManage && <button className={`toggle ${camera.enabled ? "toggle-on" : ""}`} type="button" aria-label={`${camera.enabled ? "Отключить" : "Включить"} камеру ${camera.name}`} aria-pressed={camera.enabled} onClick={onToggle}><span /></button>}</div></article>;
}

function ZoneForm({ camera, onClose, onCreated, onError }: { camera: Camera; onClose: () => void; onCreated: (zone: Zone) => void; onError: (message: string) => void }) { const [form, setForm] = useState({ name: "", type: "restricted" as ZoneType, required_ppe: [] as PpeItem[] }); const submit = async (event: FormEvent) => { event.preventDefault(); try { onCreated(await api.createZone(camera.id, { ...form, polygon: [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]] })); } catch (reason) { onError(reason instanceof Error ? reason.message : "Не удалось создать зону"); } }; return <Modal title={`Новая зона · ${camera.name}`} onClose={onClose}><form className="form-stack" onSubmit={submit}><label className="field"><span>Название зоны</span><input name="name" required value={form.name} onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))} placeholder="Например, линия резки…" autoComplete="off" /></label><label className="field"><span>Назначение</span><select name="type" value={form.type} onChange={(event) => setForm((current) => ({ ...current, type: event.target.value as ZoneType }))}><option value="restricted">Вход запрещён</option><option value="ppe_required">Обязательные СИЗ</option><option value="flammable">Горючие материалы</option></select></label><p className="form-hint">Зона будет подсвечиваться прямо поверх live-кадра.</p><div className="modal-actions"><button className="button button-secondary" type="button" onClick={onClose}>Отмена</button><button className="button button-primary" type="submit">Создать зону</button></div></form></Modal>; }

function CameraForm({ onClose, onCreated, onError }: { onClose: () => void; onCreated: (camera: Camera) => void; onError: (message: string) => void }) { const [form, setForm] = useState({ name: "", source_type: "rtsp" as "rtsp" | "file", source: "", required_ppe: [] as PpeItem[] }); const [saving, setSaving] = useState(false); const set = (key: "name" | "source", value: string) => setForm((current) => ({ ...current, [key]: value })); const togglePpe = (item: PpeItem) => setForm((current) => ({ ...current, required_ppe: current.required_ppe.includes(item) ? current.required_ppe.filter((value) => value !== item) : [...current.required_ppe, item] })); const submit = async (event: FormEvent) => { event.preventDefault(); setSaving(true); try { onCreated(await api.createCamera({ ...form, enabled: true })); } catch (reason) { onError(reason instanceof Error ? reason.message : "Не удалось создать камеру"); } finally { setSaving(false); } }; return <Modal title="Новый источник" onClose={onClose}><form className="form-stack" onSubmit={submit}><label className="field"><span>Название камеры</span><input name="name" required value={form.name} onChange={(event) => set("name", event.target.value)} placeholder="Например, Цех 2 · ворота…" autoComplete="off" /></label><div className="field"><span>Тип источника</span><div className="segmented-control"><button type="button" className={form.source_type === "rtsp" ? "segment-active" : ""} onClick={() => setForm((current) => ({ ...current, source_type: "rtsp" }))}>RTSP</button><button type="button" className={form.source_type === "file" ? "segment-active" : ""} onClick={() => setForm((current) => ({ ...current, source_type: "file" }))}>Видео-файл</button></div></div><label className="field"><span>{form.source_type === "rtsp" ? "RTSP-адрес" : "Имя видеофайла"}</span><input name="source" required value={form.source} onChange={(event) => set("source", event.target.value)} placeholder={form.source_type === "rtsp" ? "rtsp://…" : "factory_floor.mp4"} autoComplete="off" /></label><fieldset className="field"><legend>Обязательные СИЗ</legend><div className="check-grid">{ppeOptions.map((item) => <label className="check-pill" key={item.value}><input type="checkbox" checked={form.required_ppe.includes(item.value)} onChange={() => togglePpe(item.value)} /><span>{item.label}</span></label>)}</div></fieldset><div className="modal-actions"><button className="button button-secondary" type="button" onClick={onClose}>Отмена</button><button className="button button-primary" type="submit" disabled={saving}>{saving ? "Сохраняем…" : "Добавить камеру"}</button></div></form></Modal>; }

function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) { return <div className="modal-layer"><button className="drawer-backdrop" type="button" aria-label="Закрыть окно" onClick={onClose} /><section className="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title"><div className="drawer-header"><h2 id="modal-title">{title}</h2><button className="icon-button" type="button" aria-label="Закрыть" onClick={onClose}><Icon name="close" /></button></div>{children}</section></div>; }
