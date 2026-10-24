import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import { Icon, type IconName } from "./components/Icons";
import { useLiveEvents, type LiveState } from "./hooks/useLiveEvents";
import { linkHandler, navigate, useLocation } from "./lib/router";
import { AnalyticsPage } from "./pages/AnalyticsPage";
import { CamerasPage } from "./pages/CamerasPage";
import { EventsPage } from "./pages/EventsPage";
import { LoginPage } from "./pages/LoginPage";
import { OverviewPage } from "./pages/OverviewPage";
import { PerformancePage } from "./pages/PerformancePage";
import { SettingsPage } from "./pages/SettingsPage";
import { UsersPage } from "./pages/UsersPage";
import type { AuthUser } from "./types";

interface Route {
  path: string;
  label: string;
  icon: IconName;
  adminOnly?: boolean;
}

const ROUTES: Route[] = [
  { path: "/", label: "Обзор", icon: "overview", adminOnly: true },
  { path: "/cameras", label: "Камеры", icon: "camera" },
  { path: "/events", label: "Журнал", icon: "journal" },
  { path: "/analytics", label: "Аналитика", icon: "analytics", adminOnly: true },
  { path: "/performance", label: "Производительность", icon: "gauge", adminOnly: true },
  { path: "/settings", label: "Настройки", icon: "settings", adminOnly: true },
  { path: "/users", label: "Пользователи", icon: "users", adminOnly: true },
];

export default function App() {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    api.authMe().then(setUser).catch(() => setUser(null)).finally(() => setChecking(false));
    const expire = () => setUser(null);
    window.addEventListener("auth-expired", expire);
    return () => window.removeEventListener("auth-expired", expire);
  }, []);

  if (checking) return <main className="boot">Проверяем вход…</main>;
  if (!user) return <LoginPage onLoggedIn={setUser} />;
  return <Workspace user={user} onLogout={() => setUser(null)} />;
}

function Workspace({ user, onLogout }: { user: AuthUser; onLogout: () => void }) {
  const { path } = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);
  const [tick, setTick] = useState(0);
  const [openCount, setOpenCount] = useState<number | null>(null);
  const timer = useRef<number | null>(null);

  const bump = useCallback(() => {
    if (timer.current !== null) return;
    timer.current = window.setTimeout(() => {
      timer.current = null;
      setTick((t) => t + 1);
    }, 1000);
  }, []);
  const live = useLiveEvents(bump);

  const isAdmin = user.role === "admin";
  const routes = ROUTES.filter((r) => isAdmin || !r.adminOnly);
  const route = routes.find((r) => r.path === path);

  useEffect(() => {
    if (!route) navigate(isAdmin ? "/" : "/cameras", true);
  }, [route, isAdmin]);

  useEffect(() => {
    api.events({ status: "open", limit: 1 }).then((p) => setOpenCount(p.total)).catch(() => undefined);
  }, [tick]);

  useEffect(() => () => {
    if (timer.current !== null) window.clearTimeout(timer.current);
  }, []);

  useEffect(() => setMenuOpen(false), [path]);

  const logout = async () => {
    await api.authLogout().catch(() => undefined);
    onLogout();
  };

  return (
    <div className="shell">
      <header className="mobile-bar">
        <button type="button" className="icon-btn" aria-label="Меню" aria-expanded={menuOpen} onClick={() => setMenuOpen((v) => !v)}>
          <Icon name="menu" />
        </button>
        <span>{route?.label}</span>
      </header>
      <aside className={`sidebar ${menuOpen ? "is-open" : ""}`}>
        <div className="brand">
          <strong>Контроль безопасности</strong>
          <span>видеоаналитика цеха</span>
        </div>
        <nav aria-label="Разделы">
          {routes.map((r) => (
            <a key={r.path} href={r.path} onClick={linkHandler(r.path)} aria-current={r.path === path ? "page" : undefined}>
              <Icon name={r.icon} />
              <span>{r.label}</span>
              {r.path === "/events" && openCount ? <b className="nav-count" title="Открытых нарушений">{openCount}</b> : null}
            </a>
          ))}
        </nav>
        <div className="sidebar-foot">
          <Connection live={live} />
          <div className="me">
            <span>
              <strong>{user.display_name}</strong>
              <small>{isAdmin ? "Администратор" : "Оператор"}</small>
            </span>
            <button type="button" className="icon-btn" aria-label="Выйти" title="Выйти" onClick={() => void logout()}>
              <Icon name="logout" />
            </button>
          </div>
        </div>
      </aside>
      {menuOpen && <div className="sidebar-backdrop" onClick={() => setMenuOpen(false)} />}
      <main className="content" id="main">
        {route && <Page path={route.path} tick={tick} user={user} />}
      </main>
    </div>
  );
}

function Page({ path, tick, user }: { path: string; tick: number; user: AuthUser }) {
  const isAdmin = user.role === "admin";
  switch (path) {
    case "/": return <OverviewPage tick={tick} />;
    case "/cameras": return <CamerasPage tick={tick} isAdmin={isAdmin} />;
    case "/events": return <EventsPage tick={tick} />;
    case "/analytics": return <AnalyticsPage tick={tick} />;
    case "/performance": return <PerformancePage />;
    case "/settings": return <SettingsPage />;
    case "/users": return <UsersPage me={user} />;
    default: return null;
  }
}

function Connection({ live }: { live: LiveState }) {
  if (live.connected) return <div className="conn conn-ok"><span className="dot dot-live" />Связь с сервером есть</div>;
  return (
    <div className="conn conn-bad">
      <span className="dot dot-down" />
      <span>{live.attempt > 0 ? "Нет связи, переподключаемся" : "Подключаемся…"}</span>
      {live.attempt > 1 && <button type="button" className="link-btn" onClick={live.reconnect}>Сейчас</button>}
    </div>
  );
}
