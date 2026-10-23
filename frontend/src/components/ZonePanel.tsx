import { PPE_LABEL } from "../types";
import type { PpeItem, Zone, ZoneType } from "../types";
import { isComplete } from "../lib/polygon";
import type { PolygonDraft } from "../lib/polygon";
import { ZONE_COLOR, ZONE_TYPE_LABEL } from "../lib/zones";

export interface ZoneDraft {
  id?: number;
  name: string;
  type: ZoneType;
  required_ppe: PpeItem[];
  polygon: PolygonDraft;
}

interface Props {
  zones: Zone[];
  draft: ZoneDraft | null;
  error: string;
  onNew: () => void;
  onEdit: (zone: Zone) => void;
  onChange: (draft: ZoneDraft) => void;
  onSave: () => void;
  onDelete: () => void;
  onCancel: () => void;
}

const TYPES: ZoneType[] = ["restricted", "ppe_required", "flammable"];
const ITEMS: PpeItem[] = ["helmet", "mask", "vest"];

export function ZonePanel({ zones, draft, error, onNew, onEdit, onChange, onSave, onDelete, onCancel }: Props) {
  if (!draft) {
    return (
      <div className="flex min-h-0 flex-1 flex-col">
        <div className="border-b border-line p-3">
          <button onClick={onNew} className="w-full rounded bg-accent px-3 py-1.5 font-medium text-bg">Новая зона</button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">
          {zones.length === 0 && <div className="p-3 text-muted">Зон нет</div>}
          {zones.map((z) => (
            <button key={z.id} onClick={() => onEdit(z)}
                    className="block w-full border-b border-line px-3 py-2 text-left hover:bg-panel-2"
                    style={{ borderLeft: `3px solid ${ZONE_COLOR[z.type]}` }}>
              <div className="font-medium">{z.name}</div>
              <div className="text-xs text-muted">
                {ZONE_TYPE_LABEL[z.type]}
                {z.type === "ppe_required" && z.required_ppe.length > 0 && `: ${z.required_ppe.map((p) => PPE_LABEL[p]).join(", ")}`}
              </div>
            </button>
          ))}
        </div>
      </div>
    );
  }

  const complete = isComplete(draft.polygon);
  const hint = draft.polygon.closed
    ? "Перетаскивай вершины. Правый клик по вершине удаляет её."
    : draft.polygon.points.length < 3
      ? `Кликай по видео, чтобы поставить вершины (ещё минимум ${3 - draft.polygon.points.length}).`
      : "Кликни по первой (белой) вершине или нажми «Замкнуть контур».";

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-3">
      <div className="font-medium">{draft.id ? "Изменение зоны" : "Новая зона"}</div>
      <div className="rounded bg-panel-2 p-2 text-xs text-muted">{hint}</div>
      {!draft.polygon.closed && draft.polygon.points.length >= 3 && (
        <button onClick={() => onChange({ ...draft, polygon: { ...draft.polygon, closed: true } })}
                className="rounded border border-line px-3 py-1 hover:border-accent">Замкнуть контур</button>
      )}
      <label className="flex flex-col gap-1 text-xs text-muted">
        Название
        <input value={draft.name} onChange={(e) => onChange({ ...draft, name: e.target.value })}
               placeholder="Зона резки" className="rounded border border-line bg-bg px-2 py-1 text-sm text-text" />
      </label>
      <label className="flex flex-col gap-1 text-xs text-muted">
        Тип
        <select value={draft.type} onChange={(e) => onChange({ ...draft, type: e.target.value as ZoneType })}
                className="rounded border border-line bg-bg px-2 py-1 text-sm text-text">
          {TYPES.map((t) => <option key={t} value={t}>{ZONE_TYPE_LABEL[t]}</option>)}
        </select>
      </label>
      {draft.type === "ppe_required" && (
        <fieldset className="flex flex-col gap-1 text-sm">
          <legend className="mb-1 text-xs text-muted">Обязательные СИЗ в зоне</legend>
          {ITEMS.map((item) => (
            <label key={item} className="flex cursor-pointer items-center gap-2">
              <input type="checkbox" checked={draft.required_ppe.includes(item)}
                     onChange={(e) => onChange({
                       ...draft,
                       required_ppe: e.target.checked ? [...draft.required_ppe, item] : draft.required_ppe.filter((p) => p !== item),
                     })} />
              {PPE_LABEL[item]}
            </label>
          ))}
        </fieldset>
      )}
      {error && <div className="text-high">▲ {error}</div>}
      <div className="mt-auto flex gap-2">
        <button onClick={onSave} disabled={!complete || !draft.name.trim()}
                className="flex-1 rounded bg-accent px-3 py-1.5 font-medium text-bg disabled:opacity-40">Сохранить</button>
        <button onClick={onCancel} className="rounded border border-line px-3 py-1.5 hover:border-accent">Отмена</button>
      </div>
      {draft.id && (
        <button onClick={onDelete} className="rounded border border-line px-3 py-1.5 text-high hover:border-high">▲ Удалить зону</button>
      )}
    </div>
  );
}
