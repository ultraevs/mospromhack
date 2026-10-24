from __future__ import annotations

import asyncio
import logging
import ssl
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import certifi
import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.db.models import CameraRow, EventRow, MaxChatRow
from backend.app.services.bus import BusMessage
from backend.app.services.media import path_for_url
from backend.app.state import get_setting, set_setting
from shared.schemas import Event, EventStatus, EventType, Severity, MaxSettings

log = logging.getLogger("backend.max")

MAX_KEY = "max"
MAX_ENABLED_KEY = "max_enabled"

EVENT_TEXT = {
    EventType.no_helmet: "Без каски", EventType.no_mask: "Без маски", EventType.no_vest: "Без жилета",
    EventType.zone_intrusion: "Вход в запретную зону", EventType.smoking: "Курение",
    EventType.fire: "Огонь", EventType.smoke: "Дым",
}
SEVERITY_TEXT = {Severity.high: "🔴 высокая", Severity.medium: "🟠 средняя", Severity.low: "🟡 низкая"}

Key = tuple[int, EventType]


def load_settings(db: Session, default: MaxSettings) -> MaxSettings:
    raw = get_setting(db, MAX_KEY)
    return MaxSettings.model_validate(raw) if raw else default


def save_settings(db: Session, value: MaxSettings) -> None:
    set_setting(db, MAX_KEY, value.model_dump(mode="json"))


def load_enabled(db: Session, default: bool) -> bool:
    value = get_setting(db, MAX_ENABLED_KEY)
    return default if value is None else bool(value)


def save_enabled(db: Session, value: bool) -> None:
    set_setting(db, MAX_ENABLED_KEY, value)


def caption(event: Event, skipped: int = 0) -> str:
    local = event.started_at.astimezone().strftime("%d.%m %H:%M:%S")
    lines = [f"{EVENT_TEXT[event.type]} · важность {SEVERITY_TEXT[event.severity]}", f"Камера: {event.camera_name}"]
    if event.zone_name:
        lines.append(f"Зона: {event.zone_name}")
    lines.append(f"Время: {local}")
    if skipped:
        lines.append(f"Пропущено таких же с прошлого сообщения: {skipped}")
    return "\n".join(lines)


def summary(counts: dict[tuple[str, EventType], int]) -> str:
    lines = [f"Сводка нарушений: {sum(counts.values())}"]
    lines += [f"• {EVENT_TEXT[typ]} — {cam}" + (f" ×{n}" if n > 1 else "")
              for (cam, typ), n in sorted(counts.items(), key=lambda kv: -kv[1])]
    return "\n".join(lines)


# id сообщения в MAX - строка (mid)
class Sender(Protocol):
    async def send_message(self, chat_id: int, text: str) -> str: ...
    async def send_photo(self, chat_id: int, photo: Path, caption: str) -> str: ...
    async def send_video(self, chat_id: int, video: Path, caption: str, reply_to: str | None) -> str: ...


@dataclass
class _Out:
    kind: str
    message: BusMessage | None = None
    text: str = ""
    skipped: int = 0


class MaxNotifier:

    def __init__(self, session_factory: sessionmaker[Session], sender: Sender, media_root: Path,
                 defaults: MaxSettings | None = None, enabled: bool = True):
        self._sessions = session_factory
        self._sender = sender
        self._media = media_root
        self._defaults = defaults or MaxSettings()
        self._enabled = enabled
        self._queues: dict[int, asyncio.Queue[_Out]] = {}
        self._tasks: dict[int, asyncio.Task] = {}
        self._photo_message: dict[tuple[int, str], str] = {}
        self._pending: dict[Key, int] = {}
        self._names: dict[int, str] = {}
        self._last: dict[Key, float] = {}
        self._flush_task: asyncio.Task | None = None
        self.latency_ms: deque[int] = deque(maxlen=100)
        self.sent = 0
        self.errors = 0

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        if not enabled:
            for task in self._tasks.values():
                task.cancel()
            self._pending.clear()
            if self._flush_task is not None:
                self._flush_task.cancel()
                self._flush_task = None

    def set_sender(self, sender: Sender) -> None:
        self._sender = sender

    def settings(self) -> MaxSettings:
        with self._sessions() as db:
            return load_settings(db, self._defaults)

    @property
    def pending(self) -> int:
        return sum(self._pending.values())


    def chats(self) -> list[int]:
        with self._sessions() as db:
            return list(db.execute(select(MaxChatRow.chat_id)).scalars())

    def command(self, chat_id: int, name: str) -> str:
        with self._sessions() as db:
            row = db.get(MaxChatRow, chat_id)
            if name == "start":
                if row is None:
                    db.add(MaxChatRow(chat_id=chat_id))
                    db.commit()
                return ("Подписка включена. Важные нарушения приходят сразу со стоп-кадром и клипом, остальные сводкой."
                        "\n/stop — отписаться, /status — состояние.")
            if name == "stop":
                if row is not None:
                    db.delete(row)
                    db.commit()
                return "Подписка выключена. /start — включить снова."
            if name == "status":
                cameras = db.execute(select(func.count()).select_from(CameraRow).where(CameraRow.enabled.is_(True))).scalar_one()
                opened = db.execute(select(func.count()).select_from(EventRow)
                                    .where(EventRow.status == EventStatus.open.value)).scalar_one()
                state = "включена" if row is not None else "выключена"
                return f"Камер в работе: {cameras}\nОткрытых нарушений: {opened}\nПодписка этого чата: {state}"
        return "Команды: /start, /stop, /status"


    async def handle(self, message: BusMessage) -> None:
        if not self._enabled:
            return
        if message.action == "closed":
            return
        chats = self.chats()
        if not chats:
            return
        event = message.event
        if message.action == "clip_added":
            for chat_id in chats:
                if (chat_id, event.id) in self._photo_message:
                    self._enqueue(chat_id, _Out("clip", message))
            return

        cfg = self.settings()
        key = (event.camera_id, event.type)
        self._names[event.camera_id] = event.camera_name
        now = time.monotonic()
        instant = event.severity.rank >= cfg.instant_min_severity.rank
        if instant and now - self._last.get(key, float("-inf")) >= cfg.repeat_interval_s:
            self._last[key] = now
            skipped = self._pending.pop(key, 0)
            for chat_id in chats:
                self._enqueue(chat_id, _Out("photo", message, skipped=skipped))
        else:
            self._pending[key] = self._pending.get(key, 0) + 1
            self._schedule_flush(cfg.summary_interval_s)

    def _enqueue(self, chat_id: int, item: _Out) -> None:
        queue = self._queues.setdefault(chat_id, asyncio.Queue())
        queue.put_nowait(item)
        if chat_id not in self._tasks or self._tasks[chat_id].done():
            self._tasks[chat_id] = asyncio.create_task(self._chat_loop(chat_id, queue), name=f"max-{chat_id}")

    def _schedule_flush(self, delay_s: float) -> None:
        if self._flush_task is None or self._flush_task.done():
            self._flush_task = asyncio.create_task(self._flush_later(delay_s), name="max-summary")

    async def _flush_later(self, delay_s: float) -> None:
        await asyncio.sleep(delay_s)
        self._flush_task = None
        self.flush()

    def flush(self) -> None:
        cfg = self.settings()
        now = time.monotonic()
        ready = {k: n for k, n in self._pending.items() if now - self._last.get(k, float("-inf")) >= cfg.repeat_interval_s}
        if ready:
            counts: dict[tuple[str, EventType], int] = {}
            for (camera_id, typ), n in ready.items():
                del self._pending[(camera_id, typ)]
                self._last[(camera_id, typ)] = now
                name = (self._names.get(camera_id, f"камера {camera_id}"), typ)
                counts[name] = counts.get(name, 0) + n
            text = summary(counts)
            for chat_id in self.chats():
                self._enqueue(chat_id, _Out("text", text=text))
        if self._pending:
            self._schedule_flush(cfg.summary_interval_s)

    async def _chat_loop(self, chat_id: int, queue: asyncio.Queue[_Out]) -> None:
        while not queue.empty():
            item = queue.get_nowait()
            interval = self.settings().min_interval_s
            try:
                if item.kind == "text":
                    await self._sender.send_message(chat_id, item.text)
                    self.sent += 1
                elif item.kind == "photo":
                    await self._send_opened(chat_id, item.message, item.skipped)
                else:
                    event = item.message.event
                    reply_to = self._photo_message.pop((chat_id, event.id), None)
                    video = path_for_url(self._media, event.clip_url)
                    if reply_to is None or video is None:
                        continue
                    await self._sender.send_video(chat_id, video, f"Клип: {EVENT_TEXT[event.type]}, {event.camera_name}", reply_to)
                    self.sent += 1
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                self.errors += 1
                log.error("max, чат %s: отправка не удалась: %s", chat_id, e)
                await asyncio.sleep(interval)

    async def _send_opened(self, chat_id: int, message: BusMessage, skipped: int) -> None:
        event = message.event
        photo = path_for_url(self._media, event.snapshot_url)
        if photo is not None:
            message_id = await self._sender.send_photo(chat_id, photo, caption(event, skipped))
            self._photo_message[(chat_id, event.id)] = message_id
            if len(self._photo_message) > 2000:
                self._photo_message.pop(next(iter(self._photo_message)))
        else:
            await self._sender.send_message(chat_id, caption(event, skipped) + "\n(стоп-кадра нет)")
        self.sent += 1
        if message.captured_ts:
            self.latency_ms.append(int(time.time() * 1000) - message.captured_ts)

    async def wait(self) -> None:
        while any(not t.done() for t in self._tasks.values()):
            await asyncio.gather(*[t for t in self._tasks.values() if not t.done()], return_exceptions=True)

    async def close(self) -> None:
        tasks = list(self._tasks.values()) + ([self._flush_task] if self._flush_task else [])
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


MAX_API_URL = "https://platform-api2.max.ru"
# platform-api2 подписан сертом минцифры, в certifi его нет
CA_FILE = Path(__file__).parent / "certs" / "russian_trusted_root_ca.pem"
COMMANDS = {"start": "подписать чат на нарушения", "stop": "отписаться", "status": "камеры и открытые нарушения"}
RETRY_S = 5


class MaxApiError(Exception):

    def __init__(self, status: int, code: str, message: str):
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code


class MaxSender:

    def __init__(self, token: str, base_url: str = MAX_API_URL, ready_pauses: tuple[float, ...] = (1, 2, 4, 8),
                 transport: httpx.AsyncBaseTransport | None = None):
        if not token.strip():
            raise ValueError("пустой токен")
        ctx = ssl.create_default_context(cafile=certifi.where())
        ctx.load_verify_locations(CA_FILE)
        # updates висит до 30 с, клипы грузятся не мгновенно - таймаут с запасом
        self.client = httpx.AsyncClient(base_url=base_url, headers={"Authorization": token}, verify=ctx,
                                        timeout=httpx.Timeout(60, connect=10), transport=transport)
        self._ready_pauses = ready_pauses

    async def _call(self, method: str, path: str, **kw) -> dict:
        r = await self.client.request(method, path, **kw)
        if r.status_code >= 400:
            try:
                body = r.json()
            except ValueError:
                body = {}
            raise MaxApiError(r.status_code, body.get("code", ""), body.get("message", r.text[:200]))
        return r.json()

    async def me(self) -> dict:
        return await self._call("GET", "/me")

    async def set_commands(self) -> None:
        commands = [{"name": n, "description": d} for n, d in COMMANDS.items()]
        await self._call("PATCH", "/me/commands", json={"commands": commands})

    async def updates(self, marker: int | None, timeout: int = 30) -> dict:
        params: dict = {"timeout": timeout, "types": "message_created,bot_started"}
        if marker is not None:
            params["marker"] = marker
        return await self._call("GET", "/updates", params=params)

    async def _upload(self, kind: str, path: Path) -> dict:
        slot = await self._call("POST", "/uploads", params={"type": kind})
        r = await self.client.post(slot["url"], files={"data": (path.name, path.read_bytes())})
        r.raise_for_status()
        # у картинки токены приходят в ответе загрузки, у видео токен дают сразу вместе с url
        return r.json() if kind == "image" else {"token": slot["token"]}

    async def _send(self, chat_id: int, text: str, attachments: list[dict] | None = None,
                    reply_to: str | None = None) -> str:
        body: dict = {"text": text}
        if attachments:
            body["attachments"] = attachments
        if reply_to:
            body["link"] = {"type": "reply", "mid": reply_to}
        pauses = list(self._ready_pauses)
        while True:
            try:
                res = await self._call("POST", "/messages", params={"chat_id": chat_id}, json=body)
                return res["message"]["body"]["mid"]
            except MaxApiError as e:
                # видео еще перекодируется у них, ждем и шлем снова
                if e.code != "attachment.not.ready" or not pauses:
                    raise
                await asyncio.sleep(pauses.pop(0))

    async def send_message(self, chat_id: int, text: str) -> str:
        return await self._send(chat_id, text)

    async def send_photo(self, chat_id: int, photo: Path, caption: str) -> str:
        payload = await self._upload("image", photo)
        return await self._send(chat_id, caption, [{"type": "image", "payload": payload}])

    async def send_video(self, chat_id: int, video: Path, caption: str, reply_to: str | None) -> str:
        payload = await self._upload("video", video)
        return await self._send(chat_id, caption, [{"type": "video", "payload": payload}], reply_to)

    async def close(self) -> None:
        await self.client.aclose()


def command_of(update: dict) -> tuple[int, str] | None:
    if update.get("update_type") == "bot_started":  # нажали "Начать" в диалоге с ботом
        return update["chat_id"], "start"
    if update.get("update_type") != "message_created":
        return None
    message = update.get("message") or {}
    if (message.get("sender") or {}).get("is_bot"):
        return None
    text = ((message.get("body") or {}).get("text") or "").strip()
    chat_id = (message.get("recipient") or {}).get("chat_id")
    if not text.startswith("/") or chat_id is None:
        return None
    # /status@bot в группах
    return chat_id, (text[1:].split() or [""])[0].split("@")[0].lower()


async def run_bot(sender: MaxSender, notifier: MaxNotifier, state: dict) -> None:
    try:
        while True:
            try:
                me = await sender.me()
                break
            except MaxApiError as e:
                if e.status == 401:
                    state["reason"] = "invalid_token"
                    raise
                state["error"] = str(e)
            except httpx.HTTPError as e:
                state["error"] = f"{type(e).__name__}: {e}"
            log.warning("max: /me не ответил, повтор через %s с: %s", RETRY_S, state["error"])
            await asyncio.sleep(RETRY_S)
        name = me.get("username") or me.get("name")
        state.update(running=True, username=name, error=None)
        log.info("max-бот %s запущен", name)
        try:
            await sender.set_commands()
        except (MaxApiError, httpx.HTTPError) as e:
            log.warning("max: меню команд не обновилось: %s", e)

        marker = None
        while True:
            try:
                data = await sender.updates(marker)
            except (MaxApiError, httpx.HTTPError) as e:
                if isinstance(e, MaxApiError) and e.status == 401:
                    raise
                log.warning("max: updates упал, повтор через %s с: %s", RETRY_S, e)
                await asyncio.sleep(RETRY_S)
                continue
            if data.get("marker") is not None:
                marker = data["marker"]
            for update in data.get("updates") or []:
                cmd = command_of(update)
                if cmd is None:
                    continue
                chat_id, name = cmd
                try:
                    await sender.send_message(chat_id, notifier.command(chat_id, name))
                except (MaxApiError, httpx.HTTPError) as e:
                    log.error("max, чат %s: ответ на /%s не ушел: %s", chat_id, name, e)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        state.update(running=False, error=f"{type(e).__name__}: {e}")
        log.error("max-бот не запустился: %s", e)
    finally:
        state["running"] = False
        await sender.close()
