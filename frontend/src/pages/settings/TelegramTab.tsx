import { useEffect, useState } from "react";
import { api } from "../../api";
import { SEVERITY_ICON, SEVERITY_LABEL } from "../../types";
import type { Severity, TelegramSettings, TelegramStatus } from "../../types";

const NUMBERS: [keyof TelegramSettings, string, string][] = [
  ["summary_interval_s", "Сводка раз в, с", "Остальные события копятся и уходят одним сообщением со счётчиками."],
  ["repeat_interval_s", "Повтор того же типа на камере не чаще, с", "Пропущенные за паузу считаются и попадают счётчиком в следующее сообщение."],
  ["min_interval_s", "Пауза между сообщениями в чат, с", "Ограничение частоты для одного чата."],
];

function statusLine(s: TelegramStatus): { text: string; cls: string } {
  if (s.error) return { text: `▲ ошибка: ${s.error}`, cls: "text-high" };
  if (!s.enabled) {
    const why = s.reason === "switched_off" ? "в .env стоит TELEGRAM_ENABLED=false" : "в .env нет TELEGRAM_BOT_TOKEN";
    return { text: `○ бот выключен: ${why}`, cls: "text-muted" };
  }
  if (!s.running) return { text: "◆ бот запускается или не на связи", cls: "text-medium" };
  return { text: `● работает${s.username ? `, @${s.username}` : ""}`, cls: "text-ok" };
}

export function TelegramTab() {
  const [status, setStatus] = useState<TelegramStatus | null>(null);
  const [form, setForm] = useState<TelegramSettings | null>(null);
  const [state, setState] = useState<"idle" | "saving" | "saved">("idle");
  const [error, setError] = useState("");

  useEffect(() => {
    api.telegramSettings().then(setForm).catch((e: Error) => setError(e.message));
    const load = () => api.telegram().then(setStatus).catch(() => undefined);
    load();
    const timer = setInterval(load, 3000);
    return () => clearInterval(timer);
  }, []);

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!form) return;
    setState("saving");
    try {
      setForm(await api.saveTelegramSettings(form));
      setState("saved");
      setError("");
    } catch (err) {
      setState("idle");
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const line = status ? statusLine(status) : null;

  return (
    <div className="max-w-3xl">
      <section className="mb-4 rounded border border-line bg-panel p-3">
        <h2 className="mb-2 font-medium">Бот</h2>
        {line ? <div className={line.cls}>{line.text}</div> : <div className="text-muted">Загрузка…</div>}
        {status?.enabled && (
          <div className="mt-2 grid grid-cols-4 gap-3 text-sm">
            {([["Подписчиков", status.subscribers], ["Отправлено", status.sent], ["Ошибок", status.errors],
               ["Ждут сводки", status.pending]] as [string, number][]).map(([title, value]) => (
              <div key={title}>
                <div className="text-xs text-muted">{title}</div>
                <div className={`text-lg font-semibold ${title === "Ошибок" && value > 0 ? "text-medium" : ""}`}>{value}</div>
              </div>
            ))}
          </div>
        )}
        <p className="mt-2 text-xs text-muted">Команды бота: /start — подписать чат, /stop — отписать, /status — число камер и открытых нарушений.</p>
      </section>

      {form && (
        <form onSubmit={save} className="rounded border border-line bg-panel p-3">
          <h2 className="mb-1 font-medium">Что отправлять</h2>
          <p className="mb-3 text-xs text-muted">
            Касается только телеграма: в журнал и вебхуки попадают все события. Действует со следующего события.
          </p>
          <label className="mb-3 flex items-center justify-between gap-3 text-sm">
            <span>
              Отправлять сразу, начиная с важности
              <span className="block text-xs text-muted">Фото стоп-кадра с подписью, клип ответом. События ниже уходят сводкой.</span>
            </span>
            <select value={form.instant_min_severity}
                    onChange={(e) => { setState("idle"); setForm({ ...form, instant_min_severity: e.target.value as Severity }); }}
                    className="rounded border border-line bg-bg px-2 py-1 text-text">
              {(["high", "medium", "low"] as Severity[]).map((s) => (
                <option key={s} value={s}>{SEVERITY_ICON[s]} {SEVERITY_LABEL[s]}</option>
              ))}
            </select>
          </label>
          {NUMBERS.map(([field, title, hint]) => (
            <label key={field} className="mb-3 flex items-center justify-between gap-3 text-sm">
              <span>
                {title}
                <span className="block text-xs text-muted">{hint}</span>
              </span>
              <input type="number" required min={0} step="any" value={Number.isNaN(form[field] as number) ? "" : form[field]}
                     onChange={(e) => { setState("idle"); setForm({ ...form, [field]: e.target.value === "" ? NaN : Number(e.target.value) }); }}
                     className="w-24 rounded border border-line bg-bg px-2 py-1 text-right text-text" />
            </label>
          ))}
          <div className="flex items-center gap-3">
            <button disabled={state === "saving"} className="rounded bg-accent px-4 py-1.5 font-medium text-bg disabled:opacity-40">
              Сохранить
            </button>
            {state === "saved" && <span className="text-ok">● сохранено</span>}
            {error && <span className="text-high">▲ {error}</span>}
          </div>
        </form>
      )}
      {!form && error && <div className="text-high">▲ {error}</div>}
    </div>
  );
}
