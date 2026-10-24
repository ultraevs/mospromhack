import { useEffect, useState, type FormEvent } from "react";
import { api, errorText } from "../../api";
import { Icon } from "../../components/Icons";
import { Empty, Loading, Modal, Notice, Toggle } from "../../components/Ui";
import { formatDateTime } from "../../lib/format";
import { EVENT_LABEL, EVENT_TYPES, SEVERITY_LABEL, type Delivery, type EventType, type Severity, type Webhook, type WebhookInput } from "../../types";

const SEVERITY_FROM: Record<Severity, string> = { high: "только высокая", medium: "средняя и высокая", low: "любая" };

export function WebhooksPanel() {
  const [hooks, setHooks] = useState<Webhook[] | null>(null);
  const [form, setForm] = useState<Webhook | "new" | null>(null);
  const [openId, setOpenId] = useState<number | null>(null);
  const [deliveries, setDeliveries] = useState<Delivery[]>([]);
  const [test, setTest] = useState<{ id: number; result: Delivery | string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = () => api.webhooks().then(setHooks).catch((e) => setError(errorText(e, "Не удалось загрузить вебхуки")));
  useEffect(() => {
    void load();
  }, []);

  useEffect(() => {
    if (openId === null) return;
    api.deliveries(openId).then(setDeliveries).catch(() => setDeliveries([]));
  }, [openId, test]);

  const replace = (hook: Webhook) => setHooks((list) => list?.map((h) => (h.id === hook.id ? hook : h)) ?? null);

  const runTest = async (hook: Webhook) => {
    setTest({ id: hook.id, result: "…" });
    try {
      setTest({ id: hook.id, result: await api.testWebhook(hook.id) });
    } catch (e) {
      setTest({ id: hook.id, result: errorText(e, "Проверка не выполнилась") });
    }
  };

  const toggle = async (hook: Webhook, enabled: boolean) => {
    try {
      replace(await api.updateWebhook(hook.id, { enabled }));
    } catch (e) {
      setError(errorText(e, "Не удалось переключить вебхук"));
    }
  };

  const remove = async (hook: Webhook) => {
    if (!window.confirm(`Удалить вебхук ${hook.url}? История доставок тоже удалится.`)) return;
    try {
      await api.deleteWebhook(hook.id);
      setHooks((list) => list?.filter((h) => h.id !== hook.id) ?? null);
      if (openId === hook.id) setOpenId(null);
    } catch (e) {
      setError(errorText(e, "Не удалось удалить вебхук"));
    }
  };

  return (
    <div className="webhooks">
      <div className="settings-head">
        <div>
          <h2>Вебхуки</h2>
          <p>POST с JSON нарушения во внешнюю систему: СЭД, диспетчерскую, свой сервис. Тело подписано HMAC-SHA256 секретом.</p>
        </div>
        <button type="button" className="btn btn-primary" onClick={() => setForm("new")}><Icon name="plus" size={16} />Добавить вебхук</button>
      </div>
      {error && <Notice onClose={() => setError(null)}>{error}</Notice>}
      {hooks === null ? <Loading /> : hooks.length === 0 ? (
        <Empty title="Вебхуков нет">Добавьте адрес, куда отправлять нарушения.</Empty>
      ) : (
        <ul className="hook-list">
          {hooks.map((h) => (
            <li key={h.id} className={h.enabled ? "" : "is-off"}>
              <div className="hook-main">
                <Toggle checked={h.enabled} onChange={(v) => void toggle(h, v)} label={`Вебхук ${h.url} включён`} />
                <div>
                  <strong>{h.url}</strong>
                  <small>
                    {h.event_types.length ? h.event_types.map((t) => EVENT_LABEL[t].toLowerCase()).join(", ") : "все нарушения"}
                    {", важность: "}{SEVERITY_FROM[h.min_severity]}{h.send_closed ? ", и при закрытии" : ""}
                  </small>
                </div>
                <div className="hook-actions">
                  <button type="button" className="btn btn-ghost" onClick={() => void runTest(h)}>Проверить</button>
                  <button type="button" className="btn btn-ghost" onClick={() => setOpenId(openId === h.id ? null : h.id)} aria-expanded={openId === h.id}>История</button>
                  <button type="button" className="icon-btn" aria-label={`Изменить ${h.url}`} onClick={() => setForm(h)}><Icon name="edit" size={16} /></button>
                  <button type="button" className="icon-btn" aria-label={`Удалить ${h.url}`} onClick={() => void remove(h)}><Icon name="trash" size={16} /></button>
                </div>
              </div>
              {test?.id === h.id && <TestResult result={test.result} />}
              {openId === h.id && (
                deliveries.length === 0 ? <p className="muted hook-history">Доставок ещё не было.</p> : (
                  <table className="table hook-history">
                    <thead><tr><th>Когда</th><th>Что</th><th>Попытка</th><th>Ответ</th><th className="num">Время</th></tr></thead>
                    <tbody>
                      {deliveries.map((d) => (
                        <tr key={d.id}>
                          <td className="tnum">{formatDateTime(d.created_at)}</td>
                          <td>{d.action === "test" ? "проверка" : d.action}</td>
                          <td className="num">{d.attempt}</td>
                          <td className={d.ok ? "text-ok" : "text-bad"}>{d.ok ? `HTTP ${d.status_code}` : d.error ?? (d.status_code ? `HTTP ${d.status_code}` : "ошибка")}</td>
                          <td className="num">{d.duration_ms} мс</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )
              )}
            </li>
          ))}
        </ul>
      )}
      {form && (
        <WebhookForm hook={form === "new" ? null : form} onClose={() => setForm(null)}
          onSaved={(h) => { setForm(null); setHooks((list) => (list?.some((x) => x.id === h.id) ? list.map((x) => (x.id === h.id ? h : x)) : [...(list ?? []), h])); }} />
      )}
    </div>
  );
}

function TestResult({ result }: { result: Delivery | string }) {
  if (result === "…") return <p className="muted hook-test">Отправляем проверочное нарушение…</p>;
  if (typeof result === "string") return <Notice>{result}</Notice>;
  return result.ok
    ? <Notice tone="ok">Получатель ответил HTTP {result.status_code} за {result.duration_ms} мс.</Notice>
    : <Notice>Не доставлено{result.status_code ? `: HTTP ${result.status_code}` : ""}{result.error ? `: ${result.error}` : ""}. Проверьте адрес и что сервис доступен с этого сервера.</Notice>;
}

function WebhookForm({ hook, onClose, onSaved }: { hook: Webhook | null; onClose: () => void; onSaved: (h: Webhook) => void }) {
  const [form, setForm] = useState<WebhookInput>(() => hook
    ? { url: hook.url, secret: hook.secret, event_types: hook.event_types, min_severity: hook.min_severity, send_closed: hook.send_closed, enabled: hook.enabled }
    : { url: "", secret: "", event_types: [], min_severity: "medium", send_closed: false, enabled: true });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const toggleType = (t: EventType) =>
    setForm((f) => ({ ...f, event_types: f.event_types.includes(t) ? f.event_types.filter((x) => x !== t) : [...f.event_types, t] }));

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      onSaved(hook ? await api.updateWebhook(hook.id, form) : await api.createWebhook(form));
    } catch (err) {
      setError(errorText(err, "Не удалось сохранить вебхук"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title={hook ? "Изменить вебхук" : "Новый вебхук"} onClose={onClose} wide>
      <form className="form" onSubmit={submit}>
        <label className="field">
          <span>Адрес</span>
          <input type="url" required value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} placeholder="https://dispatch.example.ru/hooks/safety" />
        </label>
        <label className="field">
          <span>Секрет для подписи</span>
          <input required value={form.secret} onChange={(e) => setForm({ ...form, secret: e.target.value })} autoComplete="off" />
          <small>Заголовок X-Signature: sha256=HMAC-SHA256 от тела запроса этим секретом.</small>
        </label>
        <label className="field">
          <span>Важность</span>
          <select value={form.min_severity} onChange={(e) => setForm({ ...form, min_severity: e.target.value as Severity })}>
            {(["high", "medium", "low"] as const).map((s) => <option key={s} value={s}>{SEVERITY_LABEL[s]}: {SEVERITY_FROM[s]}</option>)}
          </select>
        </label>
        <fieldset className="field">
          <legend>Какие нарушения отправлять</legend>
          <div className="checks checks-wide">
            {EVENT_TYPES.map((t) => (
              <label key={t} className="check">
                <input type="checkbox" checked={form.event_types.includes(t)} onChange={() => toggleType(t)} />
                {EVENT_LABEL[t]}
              </label>
            ))}
          </div>
          <small>Ничего не отмечено — отправляются все.</small>
        </fieldset>
        <label className="check">
          <input type="checkbox" checked={form.send_closed} onChange={(e) => setForm({ ...form, send_closed: e.target.checked })} />
          Отправлять ещё и при закрытии нарушения
        </label>
        {error && <Notice>{error}</Notice>}
        <div className="form-actions">
          <span className="spacer" />
          <button type="button" className="btn btn-ghost" onClick={onClose}>Отмена</button>
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? "Сохраняем…" : hook ? "Сохранить" : "Добавить вебхук"}</button>
        </div>
      </form>
    </Modal>
  );
}
