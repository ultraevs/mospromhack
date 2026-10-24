import { PageHeader } from "../components/Ui";
import { setQuery, useLocation } from "../lib/router";
import { RulesPanel } from "./settings/RulesPanel";
import { TelegramPanel } from "./settings/TelegramPanel";
import { WebhooksPanel } from "./settings/WebhooksPanel";

const TABS = [
  { key: "rules", label: "Правила" },
  { key: "telegram", label: "Telegram" },
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
        {tab === "telegram" && <TelegramPanel />}
        {tab === "webhooks" && <WebhooksPanel />}
      </section>
    </div>
  );
}
