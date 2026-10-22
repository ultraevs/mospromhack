import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { CamerasTab } from "./settings/CamerasTab";
import { RulesTab } from "./settings/RulesTab";
import { TelegramTab } from "./settings/TelegramTab";
import { WebhooksTab } from "./settings/WebhooksTab";

const TABS: [string, string, () => React.JSX.Element][] = [
  ["cameras", "Камеры", CamerasTab],
  ["rules", "Правила", RulesTab],
  ["webhooks", "Вебхуки", WebhooksTab],
  ["telegram", "Телеграм", TelegramTab],
];

export function SettingsPage() {
  return (
    <div className="p-4">
      <h1 className="mb-3 text-base font-semibold">Настройки</h1>
      <nav className="mb-4 flex gap-1 border-b border-line">
        {TABS.map(([path, title]) => (
          <NavLink key={path} to={`/settings/${path}`}
                   className={({ isActive }) => `-mb-px border-b-2 px-3 py-1.5 ${isActive ? "border-accent text-text" : "border-transparent text-muted hover:text-text"}`}>
            {title}
          </NavLink>
        ))}
      </nav>
      <Routes>
        <Route index element={<Navigate to={`/settings/${TABS[0][0]}`} replace />} />
        {TABS.map(([path, , Tab]) => <Route key={path} path={path} element={<Tab />} />)}
      </Routes>
    </div>
  );
}
