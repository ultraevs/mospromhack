import { useEffect, useState, type FormEvent } from "react";
import { api, errorText } from "../../api";
import { Loading, Notice } from "../../components/Ui";
import type { Shift } from "../../types";

export function ShiftsPanel() {
  const [shifts, setShifts] = useState<Shift[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.shifts().then(setShifts).catch((e) => setError(errorText(e, "Не удалось загрузить смены")));
  }, []);

  const set = (i: number, patch: Partial<Shift>) => {
    setSaved(false);
    setShifts((s) => s && s.map((x, j) => (j === i ? { ...x, ...patch } : x)));
  };

  const save = async (e: FormEvent) => {
    e.preventDefault();
    if (!shifts) return;
    setBusy(true);
    try {
      setShifts(await api.saveShifts(shifts));
      setSaved(true);
      setError(null);
    } catch (err) {
      setError(errorText(err, "Не удалось сохранить смены"));
    } finally {
      setBusy(false);
    }
  };

  if (!shifts) return error ? <Notice>{error}</Notice> : <Loading />;

  return (
    <div className="shifts">
      <div className="settings-head">
        <div>
          <h2>Смены</h2>
          <p className="muted">По ним аналитика делит нарушения. Смена может переходить через полночь, например 20:00–08:00.</p>
        </div>
      </div>
      {error && <Notice>{error}</Notice>}
      <form className="form" onSubmit={save}>
        {shifts.map((s, i) => (
          <div key={i} className="shift-row">
            <label className="field">
              <span>Название</span>
              <input value={s.name} maxLength={40} required onChange={(e) => set(i, { name: e.target.value })} />
            </label>
            <label className="field">
              <span>Начало</span>
              <input type="time" value={s.start} required onChange={(e) => set(i, { start: e.target.value })} />
            </label>
            <label className="field">
              <span>Конец</span>
              <input type="time" value={s.end} required onChange={(e) => set(i, { end: e.target.value })} />
            </label>
            <button type="button" className="btn" disabled={shifts.length <= 1}
              onClick={() => { setSaved(false); setShifts(shifts.filter((_, j) => j !== i)); }}>Удалить</button>
          </div>
        ))}
        <div className="form-actions">
          <button type="button" className="btn" disabled={shifts.length >= 4}
            onClick={() => { setSaved(false); setShifts([...shifts, { name: `Смена ${shifts.length + 1}`, start: "08:00", end: "20:00" }]); }}>
            Добавить смену
          </button>
          {saved && <span className="text-ok">Сохранено</span>}
          <span className="spacer" />
          <button type="submit" className="btn btn-primary" disabled={busy}>Сохранить</button>
        </div>
      </form>
    </div>
  );
}
