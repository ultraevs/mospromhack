from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, HttpUrl
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.db.models import DeliveryRow, MaxChatRow, WebhookRow
from backend.app.services.max_bot import MaxNotifier, MaxSender, load_settings, run_bot, save_enabled, save_settings
from backend.app.services.webhooks import sample_event
from backend.app.state import AppState, get_db, get_state, require_admin
from shared.schemas import EventType, MaxSettings, Severity

router = APIRouter(prefix="/api", tags=["notifications"], dependencies=[Depends(require_admin)])


class WebhookIn(BaseModel):
    url: HttpUrl
    secret: str = Field(min_length=1, max_length=200)
    event_types: list[EventType] = Field(default_factory=list)
    min_severity: Severity = Severity.low
    send_closed: bool = False
    enabled: bool = True


class WebhookPatch(BaseModel):
    url: HttpUrl | None = None
    secret: str | None = Field(default=None, min_length=1, max_length=200)
    event_types: list[EventType] | None = None
    min_severity: Severity | None = None
    send_closed: bool | None = None
    enabled: bool | None = None


class MaxToggle(BaseModel):
    enabled: bool


class WebhookOut(BaseModel):
    id: int
    url: str
    secret: str
    event_types: list[EventType]
    min_severity: Severity
    send_closed: bool
    enabled: bool


class DeliveryOut(BaseModel):
    id: int
    webhook_id: int
    event_id: str
    action: str
    attempt: int
    status_code: int | None
    ok: bool
    error: str | None
    duration_ms: int
    created_at: datetime


def _out(row: WebhookRow) -> WebhookOut:
    return WebhookOut(id=row.id, url=row.url, secret=row.secret, event_types=row.event_types or [],
                      min_severity=row.min_severity, send_closed=row.send_closed, enabled=row.enabled)


def _get(db: Session, webhook_id: int) -> WebhookRow:
    row = db.get(WebhookRow, webhook_id)
    if row is None:
        raise HTTPException(404, "вебхук не найден")
    return row


@router.get("/webhooks", response_model=list[WebhookOut])
async def list_webhooks(db: Session = Depends(get_db)):
    return [_out(r) for r in db.execute(select(WebhookRow).order_by(WebhookRow.id)).scalars()]


@router.post("/webhooks", response_model=WebhookOut, status_code=201)
async def create_webhook(body: WebhookIn, db: Session = Depends(get_db)):
    row = WebhookRow(url=str(body.url), secret=body.secret, event_types=[t.value for t in body.event_types],
                     min_severity=body.min_severity.value, send_closed=body.send_closed, enabled=body.enabled)
    db.add(row)
    db.commit()
    return _out(row)


@router.patch("/webhooks/{webhook_id}", response_model=WebhookOut)
async def update_webhook(webhook_id: int, body: WebhookPatch, db: Session = Depends(get_db)):
    row = _get(db, webhook_id)
    for key, value in body.model_dump(exclude_unset=True, mode="json").items():
        if value is not None:
            setattr(row, key, value)
    db.commit()
    return _out(row)


@router.delete("/webhooks/{webhook_id}", status_code=204)
async def delete_webhook(webhook_id: int, db: Session = Depends(get_db)):
    db.delete(_get(db, webhook_id))
    db.commit()


@router.post("/webhooks/{webhook_id}/test", response_model=DeliveryOut)
async def test_webhook(webhook_id: int, db: Session = Depends(get_db), state: AppState = Depends(get_state)):
    row = _get(db, webhook_id)
    event = sample_event()
    await state.webhooks.deliver(row.id, row.url, row.secret, event, "test", event.model_dump_json().encode(), retries=0)
    last = db.execute(select(DeliveryRow).where(DeliveryRow.webhook_id == row.id)
                      .order_by(DeliveryRow.id.desc()).limit(1)).scalar_one()
    return _delivery(last)


def _delivery(row: DeliveryRow) -> DeliveryOut:
    return DeliveryOut(id=row.id, webhook_id=row.webhook_id, event_id=row.event_id, action=row.action, attempt=row.attempt,
                       status_code=row.status_code, ok=row.ok, error=row.error, duration_ms=row.duration_ms,
                       created_at=row.created_at.replace(tzinfo=timezone.utc))


@router.get("/webhooks/{webhook_id}/deliveries", response_model=list[DeliveryOut])
async def list_deliveries(webhook_id: int, limit: int = Query(default=50, ge=1, le=500), db: Session = Depends(get_db)):
    _get(db, webhook_id)
    rows = db.execute(select(DeliveryRow).where(DeliveryRow.webhook_id == webhook_id)
                      .order_by(DeliveryRow.id.desc()).limit(limit)).scalars()
    return [_delivery(r) for r in rows]


@router.get("/max")
async def max_status(db: Session = Depends(get_db), state: AppState = Depends(get_state)) -> dict:
    subscribers = db.execute(select(func.count()).select_from(MaxChatRow)).scalar_one()
    mx = state.max_state
    return {"enabled": mx["enabled"], "reason": mx["reason"], "running": mx["running"], "username": mx["username"],
            "error": mx["error"],
            "subscribers": subscribers, "sent": state.notifier.sent if state.notifier else 0,
            "errors": state.notifier.errors if state.notifier else 0,
            "pending": state.notifier.pending if state.notifier else 0}


@router.put("/max")
async def set_max_enabled(body: MaxToggle, db: Session = Depends(get_db), state: AppState = Depends(get_state)) -> dict:
    if body.enabled:
        token = state.settings.max_bot_token
        if not token:
            raise HTTPException(status_code=409, detail="не задан MAX_BOT_TOKEN в окружении backend")
        try:
            sender = MaxSender(token, state.settings.max_api_url)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=422, detail=f"токен MAX не принят: {exc}") from exc
        if state.notifier is None:
            state.notifier = MaxNotifier(state.session_factory, sender, state.settings.data_dir / "media", state.config.max)
            state.bus.subscribe("max", state.notifier.handle)
        else:
            state.notifier.set_sender(sender)
            state.notifier.set_enabled(True)
        state.max_sender = sender
        save_enabled(db, True)
        state.max_state.update(enabled=True, reason=None, error=None, running=False)
        if state.max_bot_task is None or state.max_bot_task.done():
            state.max_bot_task = asyncio.create_task(run_bot(sender, state.notifier, state.max_state), name="max-bot")
    else:
        if state.notifier:
            state.notifier.set_enabled(False)
        task = state.max_bot_task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        state.max_bot_task = None
        state.max_sender = None
        save_enabled(db, False)
        state.max_state.update(enabled=False, running=False, username=None, reason="switched_off", error=None)
    db.commit()
    subscribers = db.execute(select(func.count()).select_from(MaxChatRow)).scalar_one()
    return {"enabled": state.max_state["enabled"], "reason": state.max_state["reason"],
            "running": state.max_state["running"], "username": state.max_state["username"],
            "error": state.max_state["error"], "subscribers": subscribers,
            "sent": state.notifier.sent if state.notifier else 0,
            "errors": state.notifier.errors if state.notifier else 0,
            "pending": state.notifier.pending if state.notifier else 0}


@router.get("/settings/max", response_model=MaxSettings)
async def get_max_settings(db: Session = Depends(get_db), state: AppState = Depends(get_state)):
    return load_settings(db, state.config.max)


@router.put("/settings/max", response_model=MaxSettings)
async def put_max_settings(body: MaxSettings, db: Session = Depends(get_db)):
    save_settings(db, body)
    db.commit()
    return body
