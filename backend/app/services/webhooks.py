from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import time
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.db.models import DeliveryRow, WebhookRow
from backend.app.services.bus import BusMessage
from shared.schemas import Event, EventType, Review, Severity

log = logging.getLogger("backend.webhooks")

TIMEOUT_S = 5.0
MAX_RETRIES = 3
RETRY_PAUSES_S = (1.0, 4.0, 10.0)


def sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def matches(hook: WebhookRow, message: BusMessage) -> bool:
    if not hook.enabled:
        return False
    if message.action == "closed" and not hook.send_closed:
        return False
    if hook.event_types and message.event.type.value not in hook.event_types:
        return False
    return message.event.severity.rank >= Severity(hook.min_severity).rank


def sample_event() -> Event:
    return Event(id="evt_TEST000000", type=EventType.no_helmet, severity=Severity.medium, camera_id=0,
                 camera_name="Тестовая отправка", track_id=1, started_at=datetime.now(timezone.utc), confidence=0.5,
                 bbox=[0.4, 0.2, 0.1, 0.3], review=Review.none, details={"test": True})


class WebhookDispatcher:
    def __init__(self, session_factory: sessionmaker[Session], client: httpx.AsyncClient | None = None,
                 retry_pauses: tuple[float, ...] = RETRY_PAUSES_S):
        self._sessions = session_factory
        self._client = client or httpx.AsyncClient(timeout=TIMEOUT_S)
        self._pauses = retry_pauses
        self._tasks: set[asyncio.Task] = set()

    async def handle(self, message: BusMessage) -> None:
        with self._sessions() as db:
            hooks = [(h.id, h.url, h.secret) for h in db.execute(select(WebhookRow)).scalars() if matches(h, message)]
        body = message.event.model_dump_json().encode()
        for hook_id, url, secret in hooks:
            task = asyncio.create_task(self.deliver(hook_id, url, secret, message.event, message.action, body))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def deliver(self, hook_id: int, url: str, secret: str, event: Event, action: str, body: bytes,
                      retries: int = MAX_RETRIES) -> dict:
        headers = {"Content-Type": "application/json", "X-Event-Type": event.type.value, "X-Event-Action": action,
                   "X-Signature": sign(body, secret)}
        last: dict = {}
        for attempt in range(1, retries + 2):
            started = time.perf_counter()
            status, error = None, None
            try:
                response = await self._client.post(url, content=body, headers=headers, timeout=TIMEOUT_S)
                status = response.status_code
                if status >= 400:
                    error = f"HTTP {status}"
            except httpx.TimeoutException:
                error = f"таймаут {TIMEOUT_S:.0f} с"
            except httpx.HTTPError as e:
                error = f"{type(e).__name__}: {e}"
            ok = error is None
            last = {"webhook_id": hook_id, "event_id": event.id, "action": action, "attempt": attempt,
                    "status_code": status, "ok": ok, "error": error,
                    "duration_ms": round((time.perf_counter() - started) * 1000)}
            with self._sessions() as db:
                db.add(DeliveryRow(**last))
                db.commit()
            if ok:
                return last
            log.warning("вебхук %s, событие %s, попытка %s: %s", hook_id, event.id, attempt, error)
            if attempt <= retries:
                await asyncio.sleep(self._pauses[min(attempt - 1, len(self._pauses) - 1)])
        return last

    async def wait(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*list(self._tasks), return_exceptions=True)
        await self._client.aclose()
