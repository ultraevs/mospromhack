import { useEffect, useState, type FormEvent } from "react";
import { api, errorText } from "../api";
import { Icon } from "../components/Icons";
import { Loading, Modal, Notice, PageHeader } from "../components/Ui";
import { formatDateTime } from "../lib/format";
import type { AuthUser, UserRole } from "../types";

const ROLE_LABEL: Record<UserRole, string> = { admin: "Администратор", viewer: "Оператор" };
const ROLE_HINT: Record<UserRole, string> = {
  admin: "всё: камеры, зоны, правила, уведомления, пользователи",
  viewer: "камеры и журнал, разбор нарушений",
};

export function UsersPage({ me }: { me: AuthUser }) {
  const [users, setUsers] = useState<AuthUser[] | null>(null);
  const [creating, setCreating] = useState(false);
  const [passwordFor, setPasswordFor] = useState<AuthUser | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    api.users().then(setUsers).catch((e) => setError(errorText(e, "Не удалось загрузить пользователей")));
  }, []);

  const replace = (u: AuthUser) => setUsers((list) => list?.map((x) => (x.id === u.id ? u : x)) ?? null);

  const update = async (user: AuthUser, body: Partial<{ role: UserRole; active: boolean }>, question: string, done: string) => {
    if (!window.confirm(question)) return;
    try {
      replace(await api.updateUser(user.id, body));
      setMessage(done);
      setError(null);
    } catch (e) {
      setError(errorText(e, "Не удалось изменить доступ"));
    }
  };

  return (
    <div className="page">
      <PageHeader title="Пользователи"
        description={<>Администратор: {ROLE_HINT.admin}. Оператор: {ROLE_HINT.viewer}.</>}
        actions={<button type="button" className="btn btn-primary" onClick={() => setCreating(true)}><Icon name="plus" size={16} />Добавить пользователя</button>} />
      {error && <Notice onClose={() => setError(null)}>{error}</Notice>}
      {message && <Notice tone="ok" onClose={() => setMessage(null)}>{message}</Notice>}
      {users === null ? <Loading /> : (
        <section className="panel">
          <table className="table users">
            <thead><tr><th>Имя</th><th>Логин</th><th>Роль</th><th>Доступ</th><th>Создан</th><th /></tr></thead>
            <tbody>
              {users.map((u) => {
                const self = u.id === me.id;
                return (
                  <tr key={u.id} className={u.active ? "" : "is-off"}>
                    <td>{u.display_name}{self && <small>это вы</small>}</td>
                    <td>{u.login}</td>
                    <td>{ROLE_LABEL[u.role]}</td>
                    <td>{u.active ? "Есть" : <span className="text-bad">Отключён</span>}</td>
                    <td className="tnum">{formatDateTime(u.created_at)}</td>
                    <td><div className="row-actions">
                      {!self && (
                        <>
                          <button type="button" className="btn btn-ghost"
                            onClick={() => void update(u, { role: u.role === "admin" ? "viewer" : "admin" },
                              u.role === "admin" ? `Сделать ${u.display_name} оператором? Настройки станут недоступны.` : `Дать ${u.display_name} права администратора?`,
                              "Роль изменена")}>
                            {u.role === "admin" ? "Сделать оператором" : "Сделать администратором"}
                          </button>
                          <button type="button" className="btn btn-ghost"
                            onClick={() => void update(u, { active: !u.active },
                              u.active ? `Отключить ${u.display_name}? Текущие сессии закроются при следующем запросе.` : `Вернуть доступ ${u.display_name}?`,
                              u.active ? "Пользователь отключён" : "Доступ возвращён")}>
                            {u.active ? "Отключить" : "Включить"}
                          </button>
                        </>
                      )}
                      <button type="button" className="btn btn-ghost" onClick={() => setPasswordFor(u)}>Сменить пароль</button>
                    </div></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </section>
      )}
      {creating && (
        <UserForm onClose={() => setCreating(false)}
          onCreated={(u) => { setUsers((list) => [...(list ?? []), u]); setCreating(false); setMessage(`Пользователь ${u.login} создан`); }} />
      )}
      {passwordFor && (
        <PasswordForm user={passwordFor} onClose={() => setPasswordFor(null)}
          onDone={(u) => { replace(u); setPasswordFor(null); setMessage(`Пароль для ${u.login} изменён`); }} />
      )}
    </div>
  );
}

function UserForm({ onClose, onCreated }: { onClose: () => void; onCreated: (u: AuthUser) => void }) {
  const [form, setForm] = useState({ login: "", display_name: "", role: "viewer" as UserRole, password: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      onCreated(await api.createUser({ ...form, login: form.login.trim(), display_name: form.display_name.trim() }));
    } catch (err) {
      setError(errorText(err, "Не удалось создать пользователя"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title="Новый пользователь" onClose={onClose}>
      <form className="form" onSubmit={submit}>
        <label className="field">
          <span>Имя</span>
          <input required value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} placeholder="Иван Петров" autoComplete="off" />
        </label>
        <label className="field">
          <span>Логин</span>
          <input required minLength={3} pattern="[a-zA-Z0-9._\-]+" title="Латиница, цифры, точка, дефис, подчёркивание"
            value={form.login} onChange={(e) => setForm({ ...form, login: e.target.value })} placeholder="i.petrov" autoComplete="off" />
        </label>
        <label className="field">
          <span>Роль</span>
          <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as UserRole })}>
            <option value="viewer">{ROLE_LABEL.viewer}: {ROLE_HINT.viewer}</option>
            <option value="admin">{ROLE_LABEL.admin}: {ROLE_HINT.admin}</option>
          </select>
        </label>
        <label className="field">
          <span>Пароль, от 8 символов</span>
          <input type="password" required minLength={8} value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} autoComplete="new-password" />
        </label>
        {error && <Notice>{error}</Notice>}
        <div className="form-actions">
          <span className="spacer" />
          <button type="button" className="btn btn-ghost" onClick={onClose}>Отмена</button>
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? "Создаём…" : "Создать"}</button>
        </div>
      </form>
    </Modal>
  );
}

function PasswordForm({ user, onClose, onDone }: { user: AuthUser; onClose: () => void; onDone: (u: AuthUser) => void }) {
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      onDone(await api.updateUser(user.id, { password }));
    } catch (err) {
      setError(errorText(err, "Не удалось сменить пароль"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title={`Пароль для ${user.login}`} onClose={onClose}>
      <form className="form" onSubmit={submit}>
        <label className="field">
          <span>Новый пароль, от 8 символов</span>
          <input type="password" required minLength={8} value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" />
          <small>Все сессии пользователя закроются, войти нужно будет заново.</small>
        </label>
        {error && <Notice>{error}</Notice>}
        <div className="form-actions">
          <span className="spacer" />
          <button type="button" className="btn btn-ghost" onClick={onClose}>Отмена</button>
          <button type="submit" className="btn btn-primary" disabled={busy}>Сменить пароль</button>
        </div>
      </form>
    </Modal>
  );
}
