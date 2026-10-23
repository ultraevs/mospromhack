import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api } from "./api";
import { Icon } from "./components/Icons";
import { useLiveEvents } from "./hooks/useLiveEvents";
import { AnalyticsPage } from "./pages/AnalyticsPage";
import { CamerasPage } from "./pages/CamerasPage";
import { DashboardPage } from "./pages/DashboardPage";
import { EventsPage } from "./pages/EventsPage";
import { LoginPage } from "./pages/LoginPage";
import { SettingsPage } from "./pages/SettingsPage";
import { UsersPage } from "./pages/UsersPage";
import type { AuthUser } from "./types";

type Route = "overview" | "events" | "cameras" | "analytics" | "settings" | "users";

const navigation: { route: Route; label: string; icon: "grid" | "alert" | "camera" | "activity" | "settings" | "users"; adminOnly?: boolean }[] = [
  { route: "overview", label: "Обзор", icon: "grid" },
  { route: "events", label: "Журнал событий", icon: "alert" },
  { route: "cameras", label: "Камеры", icon: "camera" },
  { route: "analytics", label: "Аналитика", icon: "activity" },
  { route: "settings", label: "Настройки", icon: "settings" },
  { route: "users", label: "Пользователи", icon: "users", adminOnly: true },
];

function pathForRoute(route: Route) {
  return route === "overview" ? "/" : `/${route}`;
}

function routeFromLocation(): Route {
  const segment = window.location.pathname.split("/").filter(Boolean)[0];
  if (segment === "events" || segment === "cameras" || segment === "analytics" || segment === "settings" || segment === "users") return segment;
  return "overview";
}

function useRoute() {
  const [route, setRoute] = useState<Route>(routeFromLocation);
  useEffect(() => {
    const onPop = () => setRoute(routeFromLocation());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  const navigate = useCallback((next: Route) => {
    window.history.pushState({}, "", pathForRoute(next));
    setRoute(next);
  }, []);
  return { route, navigate };
}

function App() {
  const { route, navigate } = useRoute();
  const [menuOpen, setMenuOpen] = useState(false);
  const [authUser, setAuthUser] = useState<AuthUser | null>(null);
  const [authLoading, setAuthLoading] = useState(true);
  const [liveTick, setLiveTick] = useState(0);
  const syncTimerRef = useRef<number | null>(null);
  const scheduleSync = useCallback(() => {
    if (syncTimerRef.current !== null) return;
    syncTimerRef.current = window.setTimeout(() => {
      syncTimerRef.current = null;
      setLiveTick((value) => value + 1);
    }, 800);
  }, []);
  const live = useLiveEvents(scheduleSync, authUser?.role === "admin");

  useEffect(() => {
    api.authMe().then(setAuthUser).catch(() => setAuthUser(null)).finally(() => setAuthLoading(false));
  }, []);

  useEffect(() => {
    const expire = () => setAuthUser(null);
    window.addEventListener("auth-expired", expire);
    return () => window.removeEventListener("auth-expired", expire);
  }, []);

  useEffect(() => () => {
    if (syncTimerRef.current !== null) window.clearTimeout(syncTimerRef.current);
  }, []);

  const visibleRoute: Route = authUser?.role === "viewer" ? "cameras" : route;
  const page = useMemo(() => {
    const pageProps = { refreshKey: liveTick, navigate, userRole: authUser?.role ?? ("viewer" as const) };
    if (visibleRoute === "events") return <EventsPage {...pageProps} />;
    if (visibleRoute === "cameras") return <CamerasPage {...pageProps} />;
    if (visibleRoute === "analytics") return <AnalyticsPage {...pageProps} />;
    if (visibleRoute === "settings") return <SettingsPage {...pageProps} />;
    if (visibleRoute === "users") return <UsersPage {...pageProps} />;
    return <DashboardPage {...pageProps} />;
  }, [authUser?.role, liveTick, navigate, visibleRoute]);

  if (authLoading) return <AuthLoadingScreen />;
  if (!authUser) return <LoginPage onLoggedIn={setAuthUser} />;

  async function logout() {
    await api.authLogout().catch(() => undefined);
    setAuthUser(null);
  }

  return (
    <div className="app-frame">
      <AppSidebar active={visibleRoute} open={menuOpen} user={authUser} onNavigate={(next) => { navigate(next); setMenuOpen(false); }} />
      <div className="app-content">
        <header className="topbar">
          <button className="icon-button mobile-menu" type="button" aria-label="Открыть меню" onClick={() => setMenuOpen(true)}>
            <Icon name="menu" />
          </button>
          <div className="topbar-context">
            <span className="eyebrow">Контроль безопасности</span>
            <span className="topbar-divider" aria-hidden="true" />
            <span className="topbar-route">{navigation.find((item) => item.route === visibleRoute)?.label}</span>
          </div>
          <div className="topbar-actions">
            {authUser.role === "admin" && <ConnectionStatus {...live} />}
            <div className="operator-chip" aria-label={`Текущий пользователь: ${authUser.display_name}`}>
              <span className="operator-avatar">{initials(authUser.display_name)}</span>
              <span className="operator-name">{authUser.display_name}</span>
              <span className="operator-role">{authUser.role === "admin" ? "Админ" : "Камеры"}</span>
            </div>
            <button className="operator-logout" type="button" onClick={() => void logout()}>Выйти</button>
          </div>
        </header>
        {authUser.role === "admin" && <ConnectionBanner {...live} />}
        <main id="main-content" className="main-content">{page}</main>
      </div>
    </div>
  );
}

function AppSidebar({ active, open, user, onNavigate }: { active: Route; open: boolean; user: AuthUser; onNavigate: (route: Route) => void }) {
  return (
    <>
      <aside className={`sidebar ${open ? "sidebar-open" : ""}`}>
        <div className="brand-lockup">
          <div className="brand-mark" aria-hidden="true"><span /></div>
          <div>
            <strong>LINQ / SENTRY</strong>
            <span>операционный контур</span>
          </div>
        </div>
        <div className="sidebar-section-label">Рабочая зона</div>
        <nav aria-label="Основная навигация" className="primary-nav">
          {navigation.filter((item) => !item.adminOnly || user.role === "admin").map((item) => (
            <a
              key={item.route}
              className={`nav-item ${active === item.route ? "nav-item-active" : ""}`}
              href={pathForRoute(item.route)}
              aria-current={active === item.route ? "page" : undefined}
              onClick={(event) => {
                if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
                event.preventDefault();
                onNavigate(item.route);
              }}
            >
              <Icon name={item.icon} />
              <span>{item.label}</span>
              {item.route === "events" && <span className="nav-live-dot" aria-hidden="true" />}
            </a>
          ))}
        </nav>
        <div className="sidebar-footer">
          <div className="sidebar-note">
            <span className="status-dot status-dot-warm" />
            <div><strong>Смена открыта</strong><span>08:00 — 20:00 · Москва</span></div>
          </div>
          <div className="sidebar-version">LinQ / Sentry <span>v0.8</span></div>
        </div>
      </aside>
      {open && <button className="sidebar-backdrop" type="button" aria-label="Закрыть меню" onClick={() => onNavigate(active)} />}
    </>
  );
}

function AuthLoadingScreen() {
  return <main className="auth-screen auth-screen-loading"><div className="auth-loading-mark"><span className="brand-mark" aria-hidden="true"><span /></span><span>Проверяем сессию…</span></div></main>;
}

function initials(name: string) { return name.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]).join("").toUpperCase() || "??"; }

function ConnectionStatus({ connected, lastSync }: { connected: boolean; lastSync: number | null }) {
  return (
    <div className={`connection-status ${connected ? "connection-online" : "connection-offline"}`} title={lastSync ? `Синхронизация ${new Date(lastSync).toLocaleTimeString("ru-RU")}` : "Нет синхронизации"}>
      <span className="status-dot" />
      <span>{connected ? "Live" : "Нет связи"}</span>
    </div>
  );
}

function ConnectionBanner({ connected, attempt, lastSync, reconnect }: { connected: boolean; attempt: number; lastSync: number | null; reconnect: () => void }) {
  if (connected || attempt < 2) return null;
  return (
    <div className="connection-banner" role="status" aria-live="polite">
      <Icon name="alert" size={17} />
      <span>Live-соединение прервано. Последняя синхронизация: {lastSync ? new Date(lastSync).toLocaleTimeString("ru-RU") : "нет данных"}.</span>
      <button type="button" onClick={reconnect}>Переподключить</button>
    </div>
  );
}

export function PageHeader({ kicker, title, description, action }: { kicker?: string; title: string; description?: string; action?: ReactNode }) {
  return (
    <div className="page-header">
      <div><span className="page-kicker">{kicker}</span><h1>{title}</h1>{description && <p>{description}</p>}</div>
      {action && <div className="page-header-action">{action}</div>}
    </div>
  );
}

export function EmptyState({ icon = "spark", title, description, action }: { icon?: "spark" | "camera" | "alert" | "activity"; title: string; description: string; action?: ReactNode }) {
  return <div className="empty-state"><div className="empty-icon"><Icon name={icon} size={22} /></div><h3>{title}</h3><p>{description}</p>{action}</div>;
}

export function PageLoader({ kind = "table", label = "Загружаем данные…" }: { kind?: "table" | "camera"; label?: string }) {
  return <div className={`page-loader page-loader-${kind}`} role="status" aria-live="polite" aria-label={label}>
    <div className="loader-sheen" />
    <div className="loader-heading"><span /><span /><span /></div>
    {kind === "camera" ? <><div className="loader-camera-stage" /><div className="loader-camera-rail"><span /><span /><span /></div></> : <div className="loader-table-lines">{[1, 2, 3, 4, 5, 6].map((row) => <div key={row}><span /><span /><span /><span /></div>)}</div>}
  </div>;
}

export default App;
