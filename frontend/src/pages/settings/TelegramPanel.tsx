import { useEffect, useState, type FormEvent } from "react";
import { api, errorText } from "../../api";
import { Figure, Loading, Notice, Toggle } from "../../components/Ui";
import { SEVERITY_LABEL, type Severity, type TelegramSettings, type TelegramStatus } from "../../types";

export function TelegramPanel() {
  const [status, setStatus] = useState<TelegramStatus | null>(null);
  const [settings, setSettings] = useState<TelegramSettings | null>(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = () => api.telegram().then(setStatus).catch((e) => setError(errorText(e, "Не удалось получить состояние бота")));
  useEffect(() => {
    void load();
    api.telegramSettings().then(setSettings).catch((e) => setError(errorText(e, "Не удалось загрузить настройки")));
    const timer = window.setInterval(load, 5000);
    return () => window.clearInterval(timer);
  }, []);

  const toggle = async (enabled: boolean) => {
    setBusy(true);
    try {
      setStatus(await api.setTelegramEnabled(enabled));
      setError(null);
    } catch (e) {
      setError(errorText(e, "Не удалось переключить бота"));
    } finally {
      setBusy(false);
    }
  };

  const save = async (e: FormEvent) => {
    e.preventDefault();
    if (!settings) return;
    setBusy(true);
    try {
      setSettings(await api.saveTelegramSettings(settings));
      setSaved(true);
      setError(null);
    } catch (err) {
      setError(errorText(err, "Не удалось сохранить настройки"));
    } finally {
      setBusy(false);
    }
  };

  const set = <K extends keyof TelegramSettings>(key: K, value: TelegramSettings[K]) => {
    setSaved(false);
    setSettings((s) => (s ? { ...s, [key]: value } : s));
  };

  if (!status) return error ? <Notice>{error}</Notice> : <Loading />;

  const stateText = status.reason === "no_token" ? "Токен бота не задан"
    : status.reason === "invalid_token" ? "Токен бота не принят Telegram"
    : !status.enabled ? "Бот выключен"
    : status.error ? "Ошибка бота" : status.running ? "Бот работает" : "Бот запускается";

  return (
    <div className="telegram">
      <div className="settings-head">
        <div>
          <h2>Telegram</h2>
          <p>Нарушения высокой важности приходят сразу с кадром и клипом, остальные — сводкой.</p>
        </div>
        <div className="field-inline">
          <Toggle checked={status.enabled} disabled={busy || status.reason === "no_token"} onChange={(v) => void toggle(v)} label="Бот включён" />
          <span>{stateText}</span>
        </div>
      </div>
      {error && <Notice>{error}</Notice>}
      {status.error && <Notice>{status.error}</Notice>}
      {status.reason === "no_token" && (
        <Notice tone="warn">Впишите токен бота в .env (<code>TELEGRAM_BOT_TOKEN=</code>) и перезапустите backend.</Notice>
      )}

      {status.enabled && status.username && (
        <section className="subscribe">
          <h3>Как подписать чат</h3>
          <ol>
            <li>Откройте бота <a href={`https://t.me/${status.username}`} target="_blank" rel="noreferrer">@{status.username}</a> или добавьте его в рабочий чат смены.</li>
            <li>Отправьте ему <code>/start</code>. Отписаться: <code>/stop</code>.</li>
          </ol>
        </section>
      )}

      <section className="figures" aria-label="Статистика бота">
        <Figure label="Подписанных чатов" value={String(status.subscribers)} />
        <Figure label="Отправлено" value={String(status.sent)} />
        <Figure label="Ошибок отправки" value={String(status.errors)} />
        <Figure label="В очереди" value={String(status.pending)} />
      </section>

      {settings && (
        <form className="form form-grid" onSubmit={save}>
          <label className="field">
            <span>Сразу отправлять нарушения важности от</span>
            <select value={settings.instant_min_severity} onChange={(e) => set("instant_min_severity", e.target.value as Severity)}>
              {(["high", "medium", "low"] as const).map((s) => <option key={s} value={s}>{SEVERITY_LABEL[s].toLowerCase()}</option>)}
            </select>
          </label>
          <label className="field">
            <span>Пауза между сообщениями, с</span>
            <input type="number" min={0.05} step={0.05} required value={settings.min_interval_s} onChange={(e) => set("min_interval_s", Number(e.target.value))} />
          </label>
          <label className="field">
            <span>Сводка остальных раз в, с</span>
            <input type="number" min={1} required value={settings.summary_interval_s} onChange={(e) => set("summary_interval_s", Number(e.target.value))} />
          </label>
          <label className="field">
            <span>То же нарушение на той же камере не чаще, с</span>
            <input type="number" min={0} required value={settings.repeat_interval_s} onChange={(e) => set("repeat_interval_s", Number(e.target.value))} />
          </label>
          <div className="form-actions">
            {saved && <span className="text-ok">Сохранено</span>}
            <span className="spacer" />
            <button type="submit" className="btn btn-primary" disabled={busy}>Сохранить</button>
          </div>
        </form>
      )}
    </div>
  );
}
