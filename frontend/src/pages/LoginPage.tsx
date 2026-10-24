import { useState, type FormEvent } from "react";
import { api } from "../api";
import { Icon } from "../components/Icons";
import type { AuthUser } from "../types";

export function LoginPage({ onLoggedIn }: { onLoggedIn: (user: AuthUser) => void }) {
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      onLoggedIn(await api.authLogin({ login: login.trim(), password }));
    } catch (reason) {
      setError(reason instanceof Error && reason.message !== "401" ? reason.message : "Неверный логин или пароль");
    } finally {
      setSaving(false);
    }
  }

  return <main className="auth-screen">
    <section className="auth-context" aria-label="О системе">
      <div className="auth-context-top">
        <div className="auth-brand"><span className="brand-mark" aria-hidden="true"><span /></span><span><strong>LINQ / SENTRY</strong><small>контроль производства</small></span></div>
        <span className="auth-context-index">операционный контур / 01</span>
      </div>
      <div className="auth-context-main">
        <span className="auth-kicker">Рабочая зона</span>
        <h1>Камеры и сигналы смены — в одном месте.</h1>
        <p>Следите за источниками, разбирайте нарушения и быстро возвращайтесь к нужной камере.</p>
      </div>
      <div className="auth-context-status">
        <div><strong>24/7</strong><span>контроль источников</span></div>
        <div><strong>LIVE</strong><span>события поступают онлайн</span></div>
      </div>
      <div className="auth-context-footer"><span className="status-dot status-dot-live" />Система готова к работе<span>Москва · UTC+3</span></div>
    </section>
    <section className="auth-panel" aria-labelledby="auth-title">
      <div className="auth-panel-inner">
        <div className="auth-heading"><span className="auth-kicker">Контроль доступа</span><h2 id="auth-title">Вход в систему</h2><p>Используйте рабочую учётную запись, чтобы открыть мониторинг.</p></div>
        <form className="auth-form" onSubmit={submit}><label className="field"><span>Логин</span><input name="login" value={login} onChange={(event) => setLogin(event.target.value)} autoComplete="username" autoFocus required /></label><label className="field"><span>Пароль</span><span className="password-field"><input name="password" type={showPassword ? "text" : "password"} value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" required /><button type="button" className="password-toggle" onClick={() => setShowPassword((current) => !current)} aria-label={showPassword ? "Скрыть пароль" : "Показать пароль"}><Icon name={showPassword ? "eye-off" : "eye"} size={16} /></button></span></label>{error && <div className="error-notice" role="alert"><Icon name="alert" size={16} /><span>{error}</span></div>}<button className="button button-primary auth-submit" type="submit" disabled={saving}>{saving ? "Проверяем…" : "Войти в систему"}<Icon name="arrow" size={15} /></button></form>
        <p className="auth-footnote">Нет доступа? Обратитесь к администратору смены.</p>
      </div>
      <div className="auth-panel-meta"><span>LINQ / SENTRY</span><span>Сессия защищена</span></div>
    </section>
  </main>;
}
