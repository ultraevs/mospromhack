import { useEffect, useState } from "react";
import { PageHeader, Toggle } from "../components/Ui";
import { loadPrefs, savePrefs, unlockAudio, type AlertPrefs } from "../lib/alerts";
import { setQuery, useLocation } from "../lib/router";
import { RulesPanel } from "./settings/RulesPanel";
import { ShiftsPanel } from "./settings/ShiftsPanel";
import { MaxPanel } from "./settings/MaxPanel";
import { WebhooksPanel } from "./settings/WebhooksPanel";

const TABS = [
  { key: "rules", label: "Правила" },
  { key: "shifts", label: "Смены" },
  { key: "max", label: "MAX" },
  { key: "webhooks", label: "Вебхуки" },
] as const;

type Tab = (typeof TABS)[number]["key"];

export function SettingsPage() {
  const { search } = useLocation();
  const raw = new URLSearchParams(search).get("tab");
  const tab: Tab = TABS.some((t) => t.key === raw) ? (raw as Tab) : "rules";

  return (
    <div className="page">
      <PageHeader title="Настройки" />
      <div className="tabs tabs-page" role="tablist">
        {TABS.map((t) => (
          <button key={t.key} type="button" role="tab" aria-selected={tab === t.key} onClick={() => setQuery({ tab: t.key === "rules" ? null : t.key })}>
            {t.label}
          </button>
        ))}
      </div>
      <section className="panel settings-body">
        {tab === "rules" && <RulesPanel />}
        {tab === "shifts" && <ShiftsPanel />}
        {tab === "max" && <MaxPanel />}
        {tab === "webhooks" && <WebhooksPanel />}
      </section>
      <BrowserAlerts />
    </div>
  );
}

function BrowserAlerts() {
  const [prefs, setPrefs] = useState(loadPrefs);
  useEffect(() => {
    const sync = () => setPrefs(loadPrefs());
    window.addEventListener("alert-prefs", sync);
    return () => window.removeEventListener("alert-prefs", sync);
  }, []);
  const [perm, setPerm] = useState(() => (typeof Notification === "undefined" ? "unsupported" : Notification.permission));
  const set = (p: Partial<AlertPrefs>) => {
    const next = { ...prefs, ...p };
    setPrefs(next);
    savePrefs(next);
  };
  const ask = () => {
    unlockAudio();
    void Notification.requestPermission().then(setPerm).catch(() => undefined);
  };
  const permText = { granted: "разрешены", denied: "запрещены в браузере", default: "не запрошены", unsupported: "не поддерживаются" }[perm] ?? perm;
  return (
    <section className="panel settings-body alert-prefs">
      <h2 className="panel-title">Оповещения в браузере</h2>
      <label className="alert-row"><span>Звук при нарушениях (кнопка есть и в шапке)</span><Toggle checked={prefs.sound} onChange={(v) => { unlockAudio(); set({ sound: v }); }} label="Звук при нарушениях" /></label>
      <label className="alert-row"><span>Плашка критичных</span><Toggle checked={prefs.banner} onChange={(v) => set({ banner: v })} label="Плашка критичных" /></label>
      <div className="alert-row">
        <span>Уведомления: <b>{permText}</b></span>
        <button type="button" className="btn btn-ghost" disabled={perm === "granted" || perm === "denied" || perm === "unsupported"} onClick={ask}>Разрешить уведомления</button>
      </div>
    </section>
  );
}
