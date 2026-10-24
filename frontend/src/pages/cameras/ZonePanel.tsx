import { Icon } from "../../components/Icons";
import { Empty, Notice } from "../../components/Ui";
import { closeDraft, isComplete, type PolygonDraft } from "../../lib/polygon";
import { PPE_ITEMS, PPE_LABEL, ZONE_COLOR, ZONE_TYPE_HINT, ZONE_TYPE_LABEL, ZONE_TYPES, type PpeItem, type Zone, type ZoneSchedule, type ZoneType } from "../../types";

export interface ZoneDraft {
  id?: number;
  name: string;
  type: ZoneType;
  required_ppe: PpeItem[];
  schedule: ZoneSchedule | null;
  polygon: PolygonDraft;
}

const DAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
const DEFAULT_SCHEDULE: ZoneSchedule = { start: "08:00", end: "17:00", days: [1, 2, 3, 4, 5] };

// "07:55–12:00, пн–пт"
export function scheduleText(s: ZoneSchedule): string {
  const d = [...s.days].sort((a, b) => a - b);
  let days: string;
  if (d.length === 7) days = "каждый день";
  else if (d.length > 2 && d[d.length - 1] - d[0] === d.length - 1) days = `${DAYS[d[0] - 1]}–${DAYS[d[d.length - 1] - 1]}`.toLowerCase();
  else days = d.map((x) => DAYS[x - 1].toLowerCase()).join(", ");
  return `${s.start}–${s.end}, ${days}`;
}

interface Props {
  zones: Zone[];
  draft: ZoneDraft | null;
  error: string | null;
  busy: boolean;
  canEdit: boolean;
  onNew: () => void;
  onEdit: (zone: Zone) => void;
  onChange: (draft: ZoneDraft) => void;
  onSave: () => void;
  onDelete: () => void;
  onCancel: () => void;
}

export function ZonePanel({ zones, draft, error, busy, canEdit, onNew, onEdit, onChange, onSave, onDelete, onCancel }: Props) {
  if (!draft) {
    return (
      <div className="zones">
        {zones.length === 0 ? (
          <Empty title="Зон нет">
            {canEdit ? "Обведите на кадре участок: запретный проход, место с горючими материалами или зону, где нужны СИЗ." : "Администратор ещё не разметил зоны на этой камере."}
          </Empty>
        ) : (
          <ul className="zone-list">
            {zones.map((z) => (
              <li key={z.id}>
                <button type="button" disabled={!canEdit} onClick={() => onEdit(z)} style={{ borderLeftColor: ZONE_COLOR[z.type] }}>
                  <strong>{z.name}</strong>
                  <small>
                    {ZONE_TYPE_LABEL[z.type]}
                    {z.type === "ppe_required" && z.required_ppe.length > 0 && `: ${z.required_ppe.map((p) => PPE_LABEL[p].toLowerCase()).join(", ")}`}
                  </small>
                  {z.schedule && <small>{scheduleText(z.schedule)}</small>}
                  {canEdit && <Icon name="edit" size={15} />}
                </button>
              </li>
            ))}
          </ul>
        )}
        {canEdit && <button type="button" className="btn btn-primary" onClick={onNew}><Icon name="polygon" size={16} />Нарисовать зону</button>}
      </div>
    );
  }

  const complete = isComplete(draft.polygon);
  const n = draft.polygon.points.length;
  const hint = draft.polygon.closed
    ? "Тяните вершины, чтобы поправить контур. Правый клик по вершине удаляет её."
    : n < 3 ? `Кликайте по кадру, ставя вершины контура. Нужно ещё ${3 - n}.` : "Кликните по белой вершине или замкните контур кнопкой.";

  return (
    <form className="zones zone-form" onSubmit={(e) => { e.preventDefault(); onSave(); }}>
      <h3>{draft.id ? "Изменить зону" : "Новая зона"}</h3>
      <p className="hint">{hint}</p>
      {!draft.polygon.closed && n >= 3 && (
        <button type="button" className="btn btn-ghost" onClick={() => onChange({ ...draft, polygon: closeDraft(draft.polygon) })}>Замкнуть контур</button>
      )}
      <label className="field">
        <span>Название</span>
        <input value={draft.name} onChange={(e) => onChange({ ...draft, name: e.target.value })} placeholder="Проход у пресса" required />
      </label>
      <label className="field">
        <span>Тип зоны</span>
        <select value={draft.type} onChange={(e) => onChange({ ...draft, type: e.target.value as ZoneType })}>
          {ZONE_TYPES.map((t) => <option key={t} value={t}>{ZONE_TYPE_LABEL[t]}</option>)}
        </select>
        <small>{ZONE_TYPE_HINT[draft.type]}</small>
      </label>
      <fieldset className="field">
        <legend>Когда действует</legend>
        <div className="checks">
          <label className="check">
            <input type="radio" name="zone-when" checked={!draft.schedule} onChange={() => onChange({ ...draft, schedule: null })} />
            Всегда
          </label>
          <label className="check">
            <input type="radio" name="zone-when" checked={!!draft.schedule} onChange={() => onChange({ ...draft, schedule: draft.schedule ?? DEFAULT_SCHEDULE })} />
            По расписанию
          </label>
        </div>
        {draft.schedule && (() => {
          const sch = draft.schedule;
          const put = (patch: Partial<ZoneSchedule>) => onChange({ ...draft, schedule: { ...sch, ...patch } });
          return (
            <>
              <div className="checks">
                <label className="check">с <input type="time" required value={sch.start} onChange={(e) => put({ start: e.target.value })} /></label>
                <label className="check">по <input type="time" required value={sch.end} onChange={(e) => put({ end: e.target.value })} /></label>
              </div>
              <div className="checks">
                {DAYS.map((label, i) => (
                  <label key={label} className="check">
                    <input type="checkbox" checked={sch.days.includes(i + 1)} onChange={(e) => put({
                      days: e.target.checked ? [...sch.days, i + 1].sort((a, b) => a - b) : sch.days.filter((x) => x !== i + 1),
                    })} />
                    {label}
                  </label>
                ))}
              </div>
              {sch.days.length === 0 && <small>Отметьте хотя бы один день.</small>}
              {sch.start > sch.end && <small>Через полночь: с {sch.start} до {sch.end} следующего дня.</small>}
            </>
          );
        })()}
      </fieldset>
      {draft.type === "ppe_required" && (
        <fieldset className="field">
          <legend>Какие СИЗ обязательны в зоне</legend>
          <div className="checks">
            {PPE_ITEMS.map((item) => (
              <label key={item} className="check">
                <input type="checkbox" checked={draft.required_ppe.includes(item)} onChange={(e) => onChange({
                  ...draft,
                  required_ppe: e.target.checked ? [...draft.required_ppe, item] : draft.required_ppe.filter((p) => p !== item),
                })} />
                {PPE_LABEL[item]}
              </label>
            ))}
          </div>
          {draft.required_ppe.length === 0 && <small>Отметьте хотя бы одно, иначе зона ничего не требует.</small>}
        </fieldset>
      )}
      {error && <Notice>{error}</Notice>}
      <div className="form-actions">
        {draft.id && <button type="button" className="btn btn-danger" disabled={busy} onClick={onDelete}>Удалить</button>}
        <span className="spacer" />
        <button type="button" className="btn btn-ghost" onClick={onCancel}>Отмена</button>
        <button type="submit" className="btn btn-primary"
          disabled={busy || !complete || !draft.name.trim() || (draft.type === "ppe_required" && draft.required_ppe.length === 0)
            || (!!draft.schedule && draft.schedule.days.length === 0)}>
          Сохранить
        </button>
      </div>
    </form>
  );
}
