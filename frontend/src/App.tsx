import { lazy, Suspense, useEffect, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { api } from "./api";
import { CameraPage } from "./pages/CameraPage";
import { CamerasPage } from "./pages/CamerasPage";
import { JournalPage } from "./pages/JournalPage";
import { PerformancePage } from "./pages/PerformancePage";
import { SettingsPage } from "./pages/SettingsPage";
import type { ClientConfig } from "./types";

const AnalyticsPage = lazy(() => import("./pages/AnalyticsPage").then((m) => ({ default: m.AnalyticsPage })));

const NAV: [string, string][] = [
  ["/", "Камеры"],
  ["/journal", "Журнал"],
  ["/analytics", "Аналитика"],
  ["/settings", "Настройки"],
  ["/performance", "Производительность"],
];

function HealthBar() {
  const [problems, setProblems] = useState<string[] | null>(null);
  useEffect(() => {
    const load = () => api.health().then((h) => setProblems(h.problems)).catch(() => setProblems(["сервер не отвечает"]));
    load();
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, []);
  if (!problems?.length) return null;
  return (
    <div className="border-b border-line bg-panel-2 px-4 py-1 text-xs text-medium" title={problems.join("\n")}>
      ◆ Есть проблемы: {problems[0]}{problems.length > 1 ? ` (и ещё ${problems.length - 1})` : ""}
    </div>
  );
}

export function App() {
  const [config, setConfig] = useState<ClientConfig | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    const load = () => api.clientConfig().then(setConfig).catch((e: Error) => {
      setError(e.message);
      timer = setTimeout(load, 2000);
    });
    load();
    return () => clearTimeout(timer);
  }, []);

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center gap-6 border-b border-line bg-panel px-4 py-2">
        <span className="font-semibold">Контроль безопасности</span>
        <nav className="flex gap-4">
          {NAV.map(([to, text]) => (
            <NavLink key={to} to={to} end={to === "/"} className={({ isActive }) => (isActive ? "text-accent" : "text-muted hover:text-text")}>
              {text}
            </NavLink>
          ))}
        </nav>
      </header>
      <HealthBar />
      <main className="min-h-0 flex-1 overflow-auto">
        {!config ? (
          <div className="p-6 text-muted">{error ? `Сервер не отвечает (${error}). Повторяю…` : "Загрузка…"}</div>
        ) : (
          <Routes>
            <Route path="/" element={<CamerasPage config={config} />} />
            <Route path="/cameras/:id" element={<CameraPage config={config} />} />
            <Route path="/journal" element={<JournalPage />} />
            <Route path="/analytics" element={<Suspense fallback={<div className="p-6 text-muted">Загрузка…</div>}><AnalyticsPage /></Suspense>} />
            <Route path="/settings/*" element={<SettingsPage />} />
            <Route path="/performance" element={<PerformancePage />} />
          </Routes>
        )}
      </main>
    </div>
  );
}
