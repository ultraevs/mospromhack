import { useEffect, useState } from "react";
import { api } from "../../api";
import { formatTime } from "../../components/EventBits";
import { EVENT_LABEL, EVENT_TYPES, SEVERITY_ICON, SEVERITY_LABEL } from "../../types";
import type { Delivery, EventType, Severity, Webhook, WebhookInput } from "../../types";

const INPUT = "rounded border border-line bg-bg px-2 py-1 text-text";
const ACTION_LABEL: Record<string, string> = { opened: "открытие", clip_added: "клип", closed: "закрытие", test: "тест" };

type Draft = WebhookInput & { id?: number };

const EMPTY: Draft = { url: "", secret: "", event_types: [], min_severity: "low", send_closed: false, enabled: true };

export function webhookError(d: Draft): string {
  if (!/^https?:\/\/\S+$/i.test(d.url.trim())) return "Адрес должен начинаться с http:// или https://";
  if (!d.secret.trim()) return "Укажи секрет: им подписывается каждое сообщение";
  return "";
}

export function deliveryText(d: Delivery): string {
  if (d.ok) return `✔ доставлено, ответ ${d.status_code}, ${d.duration_ms} мс`;
  return `✖ не доставлено: ${d.status_code ? `ответ ${d.status_code}` : d.error ?? "нет ответа"}, ${d.duration_ms} мс`;
}

export function WebhooksTab() {
  const [hooks, setHooks] = useState<Webhook[]>([]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [logFor, setLogFor] = useState<number | null>(null);
  const [log, setLog] = useState<Delivery[]>([]);
  const [tests, setTests] = useState<Record<number, Delivery>>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = () => api.webhooks().then(setHooks).catch((e: Error) => setError(e.message));
  useEffect(() => { load(); }, []);

  useEffect(() => {
    if (logFor === null) return;
    const loadLog = () => api.deliveries(logFor).then(setLog).catch(() => undefined);
    loadLog();
    const timer = setInterval(loadLog, 2000);
    return () => clearInterval(timer);
  }, [logFor]);

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
    const problem = webhookError(draft);
    if (problem) return setError(problem);
    const { id, ...body } = { ...draft, url: draft.url.trim(), secret: draft.secret.trim() };
    run(() => (id ? api.updateWebhook(id, body) : api.createWebhook(body)), () => setDraft(null));
  };

  const test = (hook: Webhook) => run(async () => {
    const result = await api.testWebhook(hook.id);
    setTests((t) => ({ ...t, [hook.id]: result }));
  });

  const remove = (hook: Webhook) => {
    if (!window.confirm(`Удалить вебхук ${hook.url} вместе с журналом доставок?`)) return;
    run(() => api.deleteWebhook(hook.id), () => { if (logFor === hook.id) setLogFor(null); });
  };

  const toggleType = (type: EventType, on: boolean) => draft && setDraft({
    ...draft, event_types: on ? [...draft.event_types, type] : draft.event_types.filter((t) => t !== type),
  });

  return (
    <div className="max-w-5xl">
      <p className="mb-3 text-muted">
        На адрес уходит POST с JSON события. Заголовки: X-Event-Type, X-Event-Action и X-Signature с подписью
        HMAC-SHA256 тела секретом вебхука. Таймаут 5 секунд, до 3 повторов.
      </p>
      {error && <div className="mb-3 text-high">▲ {error}</div>}

      {hooks.map((h) => (
        <div key={h.id} className="mb-3 rounded border border-line bg-panel" data-testid="webhook">
          <div className="flex flex-wrap items-center gap-3 px-3 py-2">
            <span className={h.enabled ? "text-ok" : "text-muted"}>{h.enabled ? "● включён" : "○ выключен"}</span>
            <span className="min-w-0 flex-1 truncate font-medium" title={h.url}>{h.url}</span>
            <button className="text-accent hover:underline" disabled={busy} onClick={() => test(h)}>Тест</button>
            <button className="text-accent hover:underline" onClick={() => setLogFor(logFor === h.id ? null : h.id)}>
              {logFor === h.id ? "Скрыть журнал" : "Журнал доставок"}
            </button>
            <button className="text-accent hover:underline" disabled={busy} onClick={() => { setError(""); setDraft({ ...h }); }}>Изменить</button>
            <button className="text-high hover:underline" disabled={busy} onClick={() => remove(h)}>Удалить</button>
          </div>
          <div className="border-t border-line px-3 py-1.5 text-xs text-muted">
            Типы: {h.event_types.length ? h.event_types.map((t) => EVENT_LABEL[t]).join(", ") : "все"}
            {" · "}важность от: {SEVERITY_ICON[h.min_severity]} {SEVERITY_LABEL[h.min_severity]}
            {" · "}закрытие события: {h.send_closed ? "отправляется" : "не отправляется"}
          </div>
          {tests[h.id] && (
            <div className={`border-t border-line px-3 py-1.5 text-sm ${tests[h.id].ok ? "text-ok" : "text-high"}`} data-testid="webhook-test">
              Тестовая отправка: {deliveryText(tests[h.id])}
            </div>
          )}
          {logFor === h.id && (
            <div className="max-h-72 overflow-y-auto border-t border-line">
              {log.length === 0 && <div className="px-3 py-2 text-muted">Доставок ещё не было</div>}
              <table className="w-full text-left text-sm">
                <tbody>
                  {log.map((d) => (
                    <tr key={d.id} className="border-b border-line">
                      <td className="whitespace-nowrap px-3 py-1 text-muted">{formatTime(d.created_at)}</td>
                      <td className="px-2 py-1">{ACTION_LABEL[d.action] ?? d.action}</td>
                      <td className="px-2 py-1 text-muted"><code className="text-xs">{d.event_id}</code></td>
                      <td className="px-2 py-1 text-muted">попытка {d.attempt}</td>
                      <td className={`px-2 py-1 ${d.ok ? "text-ok" : "text-high"}`}>{deliveryText(d)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      ))}
      {hooks.length === 0 && !draft && <div className="mb-3 text-muted">Вебхуков нет.</div>}

      {!draft && (
        <button onClick={() => { setError(""); setDraft({ ...EMPTY }); }} className="rounded bg-accent px-4 py-1.5 font-medium text-bg">
          Добавить вебхук
        </button>
      )}

      {draft && (
        <form onSubmit={save} className="max-w-2xl rounded border border-line bg-panel p-3" data-testid="webhook-form">
          <h2 className="mb-3 font-medium">{draft.id ? "Изменить вебхук" : "Новый вебхук"}</h2>
          <label className="mb-3 flex flex-col gap-1 text-xs text-muted">
            Адрес
            <input autoFocus className={INPUT} value={draft.url} placeholder="https://example.com/hooks/safety"
                   onChange={(e) => setDraft({ ...draft, url: e.target.value })} />
          </label>
          <label className="mb-3 flex flex-col gap-1 text-xs text-muted">
            Секрет для подписи
            <input className={INPUT} value={draft.secret} onChange={(e) => setDraft({ ...draft, secret: e.target.value })} />
          </label>
          <div className="mb-3 text-xs text-muted">
            Типы событий (ничего не отмечено — все)
            <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-sm text-text">
              {EVENT_TYPES.map((t) => (
                <label key={t} className="flex items-center gap-2">
                  <input type="checkbox" checked={draft.event_types.includes(t)} onChange={(e) => toggleType(t, e.target.checked)} />
                  {EVENT_LABEL[t]}
                </label>
              ))}
            </div>
          </div>
          <label className="mb-3 flex items-center gap-3 text-sm">
            Важность от
            <select className={INPUT} value={draft.min_severity} onChange={(e) => setDraft({ ...draft, min_severity: e.target.value as Severity })}>
              {(["low", "medium", "high"] as Severity[]).map((s) => <option key={s} value={s}>{SEVERITY_ICON[s]} {SEVERITY_LABEL[s]}</option>)}
            </select>
          </label>
          <label className="mb-2 flex items-center gap-2 text-sm">
            <input type="checkbox" checked={draft.send_closed} onChange={(e) => setDraft({ ...draft, send_closed: e.target.checked })} />
            Отправлять и закрытие события
          </label>
          <label className="mb-3 flex items-center gap-2 text-sm">
            <input type="checkbox" checked={draft.enabled} onChange={(e) => setDraft({ ...draft, enabled: e.target.checked })} />
            Вебхук включён
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
