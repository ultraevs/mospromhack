from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.app.db.models import SessionRow, UserRow
from backend.app.services import auth
from backend.app.state import AppState, get_db, get_state

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginBody(BaseModel):
    login: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class UserCreate(BaseModel):
    login: str = Field(min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9._-]+$")
    display_name: str = Field(min_length=1, max_length=120)
    role: Literal["admin", "viewer"] = "viewer"
    password: str = Field(min_length=8, max_length=256)


class UserPatch(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    role: Literal["admin", "viewer"] | None = None
    password: str | None = Field(default=None, min_length=8, max_length=256)
    active: bool | None = None


def require_admin_user(request: Request, state: AppState = Depends(get_state)) -> UserRow:
    user = auth.current_user(request, state.session_factory, state.settings.auth_enabled, state.settings.internal_token)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="требуются права администратора")
    return user


@router.post("/login")
async def login(body: LoginBody, request: Request, response: Response, state: AppState = Depends(get_state)) -> dict:
    with state.session_factory() as db:
        user = db.execute(select(UserRow).where(UserRow.login == body.login)).scalar_one_or_none()
        if user is None or not user.active or not auth.verify_password(body.password, user.password_hash):
            raise HTTPException(status_code=401, detail="неверный логин или пароль")
        token = auth.create_session(db, user, state.settings.auth_session_ttl_hours)
        payload = auth.public_user(user)
    response.set_cookie(auth.SESSION_COOKIE, token, max_age=state.settings.auth_session_ttl_hours * 3600,
                        httponly=True, secure=request.url.scheme == "https", samesite="lax", path="/")
    return payload


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response, state: AppState = Depends(get_state)) -> None:
    with state.session_factory() as db:
        auth.delete_session(db, request.cookies.get(auth.SESSION_COOKIE))
    response.delete_cookie(auth.SESSION_COOKIE, path="/")


@router.get("/me")
async def me(request: Request, state: AppState = Depends(get_state)) -> dict:
    return auth.public_user(auth.current_user(request, state.session_factory, state.settings.auth_enabled))


@router.get("/users")
async def list_users(user: UserRow = Depends(require_admin_user), state: AppState = Depends(get_state)) -> list[dict]:
    with state.session_factory() as db:
        return [auth.public_user(row) for row in db.execute(select(UserRow).order_by(UserRow.id)).scalars().all()]


@router.post("/users", status_code=201)
async def create_user(body: UserCreate, user: UserRow = Depends(require_admin_user), db: Session = Depends(get_db)) -> dict:
    if db.execute(select(UserRow).where(UserRow.login == body.login)).scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="такой логин уже занят")
    row = UserRow(login=body.login, display_name=body.display_name, role=body.role,
                  password_hash=auth.hash_password(body.password), active=True)
    db.add(row)
    db.commit()
    return auth.public_user(row)


@router.patch("/users/{user_id}")
async def update_user(user_id: int, body: UserPatch, actor: UserRow = Depends(require_admin_user), db: Session = Depends(get_db)) -> dict:
    row = db.get(UserRow, user_id)
    if row is None:
        raise HTTPException(status_code=404, detail="пользователь не найден")
    data = body.model_dump(exclude_unset=True)
    if actor.id == row.id and data.get("active") is False:
        raise HTTPException(status_code=400, detail="нельзя отключить текущую учётную запись")
    if actor.id == row.id and data.get("role") == "viewer":
        raise HTTPException(status_code=400, detail="нельзя снять роль администратора у текущей учётной записи")
    if data.get("role") == "viewer" and row.role == "admin":
        admins = db.execute(select(UserRow).where(UserRow.role == "admin", UserRow.active.is_(True))).scalars().all()
        if len(admins) <= 1:
            raise HTTPException(status_code=400, detail="в системе должен остаться активный администратор")
    if "password" in data:
        row.password_hash = auth.hash_password(data.pop("password"))
        db.execute(delete(SessionRow).where(SessionRow.user_id == row.id))
    for key, value in data.items():
        setattr(row, key, value)
    db.commit()
    return auth.public_user(row)
