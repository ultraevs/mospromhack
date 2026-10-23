import { useEffect, useState, type FormEvent } from "react";
import { api } from "../api";
import { Icon } from "../components/Icons";
import { PageHeader } from "../App";
import type { AuthUser, UserRole } from "../types";
import type { PageProps } from "./types";

export function UsersPage(_props: PageProps) {
  const [users, setUsers] = useState<AuthUser[]>([]);
  const [showForm, setShowForm] = useState(false);
  const [passwordUser, setPasswordUser] = useState<AuthUser | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  async function load() {
    try { setUsers(await api.authUsers()); setError(null); } catch (reason) { setError(reason instanceof Error ? reason.message : "Не удалось загрузить пользователей"); }
  }
  useEffect(() => { void load(); }, []);

  async function update(user: AuthUser, body: Partial<{ role: UserRole; active: boolean }>) {
    try { const updated = await api.updateAuthUser(user.id, body); setUsers((items) => items.map((item) => item.id === updated.id ? updated : item)); setMessage("Доступ обновлён"); setError(null); } catch (reason) { setError(reason instanceof Error ? reason.message : "Не удалось обновить доступ"); }
  }

  return <div className="page-stack users-page"><PageHeader kicker="Контур / доступ" title="Пользователи" description="Управляйте ролями и доступом к рабочей зоне без общих учётных записей." action={<button className="button button-primary" type="button" onClick={() => { setShowForm(true); setMessage(null); }}><Icon name="plus" size={16} />Добавить пользователя</button>} />{error && <div className="error-notice" role="alert"><Icon name="alert" size={17} /><span>{error}</span></div>}{message && <div className="success-notice" role="status"><Icon name="check" size={16} /><span>{message}</span></div>}<section className="users-panel"><div className="users-panel-heading"><div><span className="panel-kicker">Учетные записи</span><h2>Доступ к системе</h2></div><span>{users.length} пользователей</span></div><div className="users-list">{users.map((user) => <article className={`user-row ${user.active ? "" : "is-disabled"}`} key={user.id}><div className="user-avatar">{initials(user.display_name)}</div><div className="user-main"><strong>{user.display_name}</strong><span>{user.login}</span></div><span className={`user-role user-role-${user.role}`}>{user.role === "admin" ? "Администратор" : "Просмотр камер"}</span><span className={`user-state ${user.active ? "is-active" : ""}`}><i />{user.active ? "Активен" : "Отключён"}</span><div className="user-actions"><button className="text-button" type="button" onClick={() => void update(user, { active: !user.active })}>{user.active ? "Отключить" : "Включить"}</button><button className="text-button" type="button" onClick={() => void update(user, { role: user.role === "admin" ? "viewer" : "admin" })}>{user.role === "admin" ? "Сделать viewer" : "Сделать админом"}</button><button className="text-button" type="button" onClick={() => setPasswordUser(user)}>Пароль</button></div></article>)}</div></section>{showForm && <UserForm onClose={() => setShowForm(false)} onCreated={(user) => { setUsers((items) => [...items, user]); setShowForm(false); setMessage("Пользователь создан"); setError(null); }} onError={setError} />}{passwordUser && <PasswordForm user={passwordUser} onClose={() => setPasswordUser(null)} onUpdated={(user) => { setUsers((items) => items.map((item) => item.id === user.id ? user : item)); setPasswordUser(null); setMessage("Пароль обновлён"); }} onError={setError} />}</div>;
}

function UserForm({ onClose, onCreated, onError }: { onClose: () => void; onCreated: (user: AuthUser) => void; onError: (message: string) => void }) {
  const [form, setForm] = useState({ login: "", display_name: "", role: "viewer" as UserRole, password: "" });
  const [saving, setSaving] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    try { onCreated(await api.createAuthUser(form)); } catch (reason) { onError(reason instanceof Error ? reason.message : "Не удалось создать пользователя"); } finally { setSaving(false); }
  }
  return <div className="modal-layer"><button className="drawer-backdrop" type="button" aria-label="Закрыть форму" onClick={onClose} /><section className="modal user-form-modal" role="dialog" aria-modal="true" aria-labelledby="user-form-title"><div className="drawer-header"><div><span className="panel-kicker">Новая учётная запись</span><h2 id="user-form-title">Добавить пользователя</h2></div><button className="icon-button" type="button" aria-label="Закрыть" onClick={onClose}><Icon name="close" /></button></div><form className="form-stack" onSubmit={submit}><label className="field"><span>Имя пользователя</span><input name="display_name" required value={form.display_name} onChange={(event) => setForm((current) => ({ ...current, display_name: event.target.value }))} autoComplete="name" placeholder="Например, Иван Петров" /></label><label className="field"><span>Логин</span><input name="login" required pattern="[a-zA-Z0-9._-]+" value={form.login} onChange={(event) => setForm((current) => ({ ...current, login: event.target.value }))} autoComplete="username" placeholder="ivan.petrov" /></label><label className="field"><span>Роль</span><select name="role" value={form.role} onChange={(event) => setForm((current) => ({ ...current, role: event.target.value as UserRole }))}><option value="viewer">Просмотр камер</option><option value="admin">Администратор</option></select></label><label className="field"><span>Пароль <small>минимум 8 символов</small></span><input name="password" type="password" minLength={8} required value={form.password} onChange={(event) => setForm((current) => ({ ...current, password: event.target.value }))} autoComplete="new-password" /></label><div className="modal-actions"><button className="button button-secondary" type="button" onClick={onClose}>Отмена</button><button className="button button-primary" type="submit" disabled={saving}>{saving ? "Создаём…" : "Создать пользователя"}</button></div></form></section></div>;
}

function PasswordForm({ user, onClose, onUpdated, onError }: { user: AuthUser; onClose: () => void; onUpdated: (user: AuthUser) => void; onError: (message: string) => void }) {
  const [password, setPassword] = useState("");
  const [saving, setSaving] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    try { onUpdated(await api.updateAuthUser(user.id, { password })); } catch (reason) { onError(reason instanceof Error ? reason.message : "Не удалось обновить пароль"); } finally { setSaving(false); }
  }
  return <div className="modal-layer"><button className="drawer-backdrop" type="button" aria-label="Закрыть форму" onClick={onClose} /><section className="modal user-form-modal" role="dialog" aria-modal="true" aria-labelledby="password-form-title"><div className="drawer-header"><div><span className="panel-kicker">{user.login}</span><h2 id="password-form-title">Новый пароль</h2></div><button className="icon-button" type="button" aria-label="Закрыть" onClick={onClose}><Icon name="close" /></button></div><form className="form-stack" onSubmit={submit}><label className="field"><span>Пароль <small>минимум 8 символов</small></span><input name="password" type="password" minLength={8} required value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="new-password" autoFocus /></label><div className="modal-actions"><button className="button button-secondary" type="button" onClick={onClose}>Отмена</button><button className="button button-primary" type="submit" disabled={saving}>{saving ? "Сохраняем…" : "Обновить пароль"}</button></div></form></section></div>;
}

function initials(name: string) { return name.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]).join("").toUpperCase() || "??"; }
