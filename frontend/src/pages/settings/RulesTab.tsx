import { useEffect, useState } from "react";
import { api } from "../../api";
import type { RuleSettings } from "../../types";

type Section = keyof RuleSettings;

const SECTIONS: [Section, string, string][] = [
  ["no_helmet", "Без каски", "Срабатывает, когда доля кадров с непокрытой головой за окно не ниже порога и больше доли кадров с каской."],
  ["no_mask", "Без маски", "То же для маски."],
  ["no_vest", "Без жилета", "Срабатывает, когда жилет виден не чаще порога, а рамка человека не ниже минимальной высоты."],
  ["zone_intrusion", "Вход в запретную зону", "Срабатывает, когда человек стоит в зоне в заданной доле кадров за окно и наблюдений набралось не меньше минимума."],
  ["smoking", "Курение", "Сигарета найдена у лица человека в K из последних M проверок. По умолчанию правило работает только в зонах с горючими материалами."],
  ["fire", "Огонь", "Найден на кадре в K из последних M проверок."],
  ["smoke", "Дым", "Найден на кадре в K из последних M проверок."],
  ["running", "Бег", "Скорость человека в ростах в секунду не ниже порога в заданной доле наблюдений за окно. В запретной зоне событие высокой важности."],
  ["vehicle_proximity", "Сближение с техникой", "Расстояние от ног человека до рамки техники в ростах человека меньше порога в заданной доле кадров за окно."],
  ["fall", "Падение", "Рамка человека была вертикальной и за окно стала горизонтальной, центр рамки резко опустился. Оценка по рамке, без позы."],
  ["lying_immobile", "Лежит неподвижно", "Рамка горизонтальная почти всё окно и почти не двигается. Срабатывает и без падения перед этим."],
  ["lifecycle", "Жизненный цикл событий", "Закрытие, паузы и защита от повторов."],
];

const FIELD_LABEL: Record<string, string> = {
  window_s: "Окно, с",
  min_observations: "Минимум наблюдений",
  fraction: "Доля кадров, от",
  max_fraction: "Доля кадров с жилетом, до",
  min_person_height_px: "Минимальная высота человека, px",
  k: "K (сколько раз найдено)",
  m: "M (из скольких проверок)",
  close_after_s: "Закрывать, если условие не выполняется, с",
  cooldown_s: "Пауза до нового события того же человека, с",
  retrack_iou: "Смена номера трека: порог пересечения рамок",
  retrack_window_s: "Смена номера трека: окно, с",
  camera_cooldown_s: "Пауза на камеру для огня и дыма, с",
  track_lost_s: "Считать человека ушедшим через, с",
  upper_fraction: "Сигарета в верхней доле рамки человека, до",
  everywhere: "Считать курение везде",
  speed_heights_s: "Скорость, ростов в секунду, от",
  distance_heights: "Расстояние, в ростах, до",
  upright_ratio: "Стоит: высота/ширина рамки, от",
  lying_ratio: "Лежит: ширина/высота рамки, от",
  drop_heights: "Падение центра рамки, в ростах, от",
  max_speed: "Скорость, размеров рамки в секунду, до",
};

const INTEGER = new Set(["min_observations", "min_person_height_px", "k", "m"]);

export function RulesTab() {
  const [rules, setRules] = useState<RuleSettings | null>(null);
  const [state, setState] = useState<"idle" | "saving" | "saved">("idle");
  const [error, setError] = useState("");

  useEffect(() => {
    api.rules().then(setRules).catch((e: Error) => setError(e.message));
  }, []);

  const set = (section: Section, field: string, value: number | boolean) => {
    if (!rules) return;
    setState("idle");
    setRules({ ...rules, [section]: { ...rules[section], [field]: value } });
  };

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!rules) return;
    setState("saving");
    try {
      setRules(await api.saveRules(rules));
      setState("saved");
      setError("");
    } catch (err) {
      setState("idle");
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  if (!rules) return <div className="p-6 text-muted">{error ? `▲ ${error}` : "Загрузка…"}</div>;

  return (
    <form onSubmit={save} className="max-w-5xl">
      <p className="mb-4 text-muted">Изменения применяются без перезапуска, воркер подхватывает их за пару секунд.</p>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(300px,1fr))] gap-3">
        {SECTIONS.map(([section, title, hint]) => (
          <fieldset key={section} className="rounded border border-line bg-panel p-3">
            <legend className="px-1 font-medium">{title}</legend>
            <p className="mb-2 text-xs text-muted">{hint}</p>
            {Object.entries(rules[section]).map(([field, value]) => (
              <label key={field} className="mb-2 flex items-center justify-between gap-2 text-sm">
                <span className="text-muted">{FIELD_LABEL[field] ?? field}</span>
                {typeof value === "boolean" ? (
                  <input type="checkbox" checked={value} onChange={(e) => set(section, field, e.target.checked)}
                         className="h-4 w-4 accent-accent" />
                ) : (
                  <input type="number" required step={INTEGER.has(field) ? 1 : "any"} min={0}
                         value={Number.isNaN(value) ? "" : value}
                         onChange={(e) => set(section, field, e.target.value === "" ? NaN : Number(e.target.value))}
                         className="w-24 rounded border border-line bg-bg px-2 py-1 text-right text-text" />
                )}
              </label>
            ))}
          </fieldset>
        ))}
      </div>
      <div className="mt-4 flex items-center gap-3">
        <button disabled={state === "saving"} className="rounded bg-accent px-4 py-1.5 font-medium text-bg disabled:opacity-40">
          Сохранить
        </button>
        {state === "saved" && <span className="text-ok">● сохранено</span>}
        {error && <span className="text-high">▲ {error}</span>}
      </div>
    </form>
  );
}
