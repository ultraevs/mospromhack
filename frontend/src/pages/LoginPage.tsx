import { useState, type FormEvent } from "react";
import { api, ApiError } from "../api";
import { Icon } from "../components/Icons";
import { Sign } from "../components/Sign";
import { Notice } from "../components/Ui";
import type { AuthUser } from "../types";

export function LoginPage({ onLoggedIn }: { onLoggedIn: (user: AuthUser) => void }) {
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onLoggedIn(await api.authLogin({ login: login.trim(), password }));
    } catch (reason) {
      setError(reason instanceof ApiError && reason.status === 401 ? "Неверный логин или пароль" : "Сервер недоступен, попробуйте ещё раз");
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="login">
      <form className="login-card" onSubmit={submit}>
        <div className="login-signs" aria-hidden="true">
          <Sign type="no_helmet" size={30} />
          <Sign type="zone_intrusion" size={30} />
          <Sign type="fire" size={30} />
        </div>
        <h1>Контроль безопасности</h1>
        <label className="field">
          <span>Логин</span>
          <input name="login" value={login} onChange={(e) => setLogin(e.target.value)} autoComplete="username" autoFocus required />
        </label>
        <label className="field">
          <span>Пароль</span>
          <span className="password">
            <input name="password" type={show ? "text" : "password"} value={password} onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password" required />
            <button type="button" className="icon-btn" onClick={() => setShow((v) => !v)} aria-label={show ? "Скрыть пароль" : "Показать пароль"}>
              <Icon name={show ? "eye-off" : "eye"} size={16} />
            </button>
          </span>
        </label>
        {error && <Notice>{error}</Notice>}
        <button className="btn btn-primary" type="submit" disabled={busy}>{busy ? "Входим…" : "Войти"}</button>
        <small>Доступ выдаёт администратор системы.</small>
      </form>
    </main>
  );
}
