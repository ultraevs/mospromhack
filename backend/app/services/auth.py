from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request, WebSocket
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.app.db.models import SessionRow, UserRow
from shared.config import Settings

SESSION_COOKIE = "linq_session"
PASSWORD_SCHEME = "pbkdf2_sha256"
PASSWORD_ITERATIONS = 310_000


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PASSWORD_ITERATIONS)
    return f"{PASSWORD_SCHEME}${PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, iterations, salt_hex, digest_hex = encoded.split("$", 3)
        if scheme != PASSWORD_SCHEME:
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations))
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (TypeError, ValueError):
        return False


def public_user(user: UserRow) -> dict:
    return {"id": user.id, "login": user.login, "display_name": user.display_name,
            "role": user.role, "active": user.active, "created_at": user.created_at}


def seed_users(db: Session, settings: Settings) -> None:
    defaults = (
        (settings.auth_admin_login, settings.auth_admin_password, "Администратор", "admin"),
        (settings.auth_viewer_login, settings.auth_viewer_password, "Оператор камер", "viewer"),
    )
    for login, password, display_name, role in defaults:
        if not login or not password:
            continue
        user = db.execute(select(UserRow).where(UserRow.login == login)).scalar_one_or_none()
        if user is None:
            db.add(UserRow(login=login, display_name=display_name, role=role, password_hash=hash_password(password), active=True))
    db.commit()


def create_session(db: Session, user: UserRow, ttl_hours: int) -> str:
    token = secrets.token_urlsafe(48)
    db.add(SessionRow(token_hash=hashlib.sha256(token.encode()).hexdigest(), user_id=user.id,
                      expires_at=now_utc() + timedelta(hours=ttl_hours)))
    db.commit()
    return token


def delete_session(db: Session, token: str | None) -> None:
    if token:
        db.execute(delete(SessionRow).where(SessionRow.token_hash == hashlib.sha256(token.encode()).hexdigest()))
        db.commit()


def user_from_token(db: Session, token: str | None) -> UserRow | None:
    if not token:
        return None
    row = db.execute(select(SessionRow).where(SessionRow.token_hash == hashlib.sha256(token.encode()).hexdigest())).scalar_one_or_none()
    if row is None or row.expires_at <= now_utc() or not row.user.active:
        if row is not None:
            db.delete(row)
            db.commit()
        return None
    return row.user


def current_user(request: Request, session_factory, enabled: bool = True) -> UserRow:
    if not enabled:
        return UserRow(id=0, login="test-admin", display_name="Тестовый администратор", role="admin", active=True)
    with session_factory() as db:
        user = user_from_token(db, request.cookies.get(SESSION_COOKIE))
    if user is None:
        raise HTTPException(status_code=401, detail="требуется вход в систему")
    return user


def websocket_user(ws: WebSocket, session_factory, enabled: bool = True) -> UserRow | None:
    if not enabled:
        return UserRow(id=0, login="test-admin", display_name="Тестовый администратор", role="admin", active=True)
    with session_factory() as db:
        return user_from_token(db, ws.cookies.get(SESSION_COOKIE))
