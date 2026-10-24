import { useEffect, useState, type FormEvent } from "react";
import { api, errorText } from "../../api";
import { Loading, Notice } from "../../components/Ui";
import type { RuleSettings } from "../../types";

type Section = keyof RuleSettings;

const SECTIONS: [Section, string, string][] = [
  ["no_helmet", "Без каски", "Голова без каски видна в заданной доле кадров за окно, и таких кадров больше, чем с каской."],
  ["no_mask", "Без маски", "То же для маски."],
  ["no_vest", "Без жилета", "Жилет виден не чаще порога, а человек в кадре не мельче минимальной высоты."],
  ["zone_intrusion", "Запретная зона", "Человек стоит в зоне в заданной доле кадров за окно, наблюдений не меньше минимума."],
  ["smoking", "Курение", "Сигарета у лица в K из последних M проверок. Курилки не считаются, в зонах с горючим — всегда."],
  ["fire", "Огонь", "Огонь найден в K из последних M проверок."],
  ["smoke", "Дым", "Дым найден в K из последних M проверок."],
  ["running", "Бег", "Скорость в ростах в секунду не ниже порога в заданной доле наблюдений. В запретной зоне важность высокая."],
  ["vehicle_proximity", "Рядом с техникой", "Расстояние от ног человека до техники в ростах меньше порога в заданной доле кадров. Стоящую технику можно не учитывать порогом скорости."],
  ["fall", "Падение", "Рамка человека была вертикальной и стала горизонтальной, центр резко опустился. Оценка по рамке, без позы."],
  ["lying_immobile", "Лежит неподвижно", "Рамка горизонтальная почти всё окно и почти не двигается."],
  ["phone_usage", "Телефон", "Телефон в руке у верхней части фигуры (у уха или перед лицом) в заданной доле кадров за окно."],
  ["barrier_crossing", "Перелез через ограждение", "Ноги человека прошли полосу зоны «Ограждение» насквозь быстрее окна."],
  ["under_load", "Под грузом", "Человек стоит в зоне «Под грузом / конвейер» в заданной доле кадров за окно."],
  ["no_harness", "Без привязи на высоте", "В зоне «Работа на высоте» страховочная привязь видна не чаще порога."],
  ["unsafe_carry", "Опасная переноска груза", "Человек несёт груз на ходу в заданной доле кадров, скорость не ниже порога."],
  ["lifecycle", "Жизнь события", "Когда закрывать событие и как не плодить повторы."],
];

const FIELD_LABEL: Record<string, string> = {
  window_s: "Окно, с",
  min_observations: "Минимум наблюдений",
  fraction: "Доля кадров, от",
  max_fraction: "Доля кадров с жилетом, до",
  min_person_height_px: "Высота человека, от, px",
  k: "Найдено раз, K",
  m: "Из проверок, M",
  close_after_s: "Закрывать без нарушения через, с",
  cooldown_s: "Пауза до нового события того же человека, с",
  retrack_iou: "Смена трека: пересечение рамок, от",
  retrack_window_s: "Смена трека: окно, с",
  camera_cooldown_s: "Пауза на камеру для огня и дыма, с",
  track_lost_s: "Человек ушёл, если не виден, с",
  upper_fraction: "Сигарета в верхней доле рамки, до",
  everywhere: "Курение везде, кроме курилок (иначе — только в зонах с горючим)",
  min_vehicle_speed: "Скорость техники, своих высот в секунду, от (0 — и стоящая)",
  min_speed_heights_s: "Скорость человека, ростов в секунду, от",
  speed_heights_s: "Скорость, ростов в секунду, от",
  distance_heights: "Расстояние в ростах, до",
  upright_ratio: "Стоит: высота к ширине, от",
  lying_ratio: "Лежит: ширина к высоте, от",
  drop_heights: "Падение центра в ростах, от",
  max_speed: "Скорость, рамок в секунду, до",
};

// где общая подпись не по смыслу
const SECTION_FIELD_LABEL: Partial<Record<Section, Record<string, string>>> = {
  phone_usage: { upper_fraction: "Телефон в верхней доле рамки, до" },
  no_harness: { max_fraction: "Доля кадров с привязью, до" },
  under_load: { fraction: "Доля кадров в зоне, от" },
};

const INTEGER = new Set(["min_observations", "min_person_height_px", "k", "m"]);

export function RulesPanel() {
  const [rules, setRules] = useState<RuleSettings | null>(null);
  const [state, setState] = useState<"idle" | "dirty" | "saving" | "saved">("idle");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.rules().then(setRules).catch((e) => setError(errorText(e, "Не удалось загрузить правила")));
  }, []);

  const set = (section: Section, field: string, value: number | boolean) => {
    if (!rules) return;
    setState("dirty");
    setRules({ ...rules, [section]: { ...rules[section], [field]: value } });
  };

  const save = async (e: FormEvent) => {
    e.preventDefault();
    if (!rules) return;
    setState("saving");
    try {
      setRules(await api.saveRules(rules));
      setState("saved");
      setError(null);
    } catch (err) {
      setState("dirty");
      setError(errorText(err, "Не удалось сохранить правила"));
    }
  };

  if (!rules) return error ? <Notice>{error}</Notice> : <Loading />;

  return (
    <form className="rules" onSubmit={save}>
      <div className="settings-head">
        <div>
          <h2>Правила срабатывания</h2>
          <p>Общие для всех камер. Воркер подхватывает изменения за пару секунд, перезапуск не нужен.</p>
        </div>
        <div className="save-state">
          {state === "saved" && <span className="text-ok">Сохранено</span>}
          {state === "dirty" && <span className="muted">Есть несохранённые изменения</span>}
          <button type="submit" className="btn btn-primary" disabled={state === "saving" || state === "idle" || state === "saved"}>
            {state === "saving" ? "Сохраняем…" : "Сохранить правила"}
          </button>
        </div>
      </div>
      {error && <Notice>{error}</Notice>}
      <div className="rules-grid">
        {SECTIONS.map(([section, title, hint]) => (
          <fieldset key={section} className="rule">
            <legend>{title}</legend>
            <p>{hint}</p>
            {Object.entries(rules[section] ?? {}).map(([field, value]) => (
              <label key={field} className="rule-field">
                <span>{SECTION_FIELD_LABEL[section]?.[field] ?? FIELD_LABEL[field] ?? field}</span>
                {typeof value === "boolean" ? (
                  <input type="checkbox" checked={value} onChange={(e) => set(section, field, e.target.checked)} />
                ) : (
                  <input type="number" required min={0} step={INTEGER.has(field) ? 1 : "any"} inputMode="decimal"
                    value={Number.isNaN(value) ? "" : value}
                    onChange={(e) => set(section, field, e.target.value === "" ? NaN : Number(e.target.value))} />
                )}
              </label>
            ))}
          </fieldset>
        ))}
      </div>
    </form>
  );
}
