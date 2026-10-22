from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.db.models import CameraRow, EventRow, TelegramChatRow
from backend.app.services.bus import BusMessage
from backend.app.services.media import path_for_url
from backend.app.state import get_setting, set_setting
from shared.schemas import Event, EventStatus, EventType, Severity, TelegramSettings

log = logging.getLogger("backend.telegram")

TELEGRAM_KEY = "telegram"

EVENT_TEXT = {
    EventType.no_helmet: "Без каски", EventType.no_mask: "Без маски", EventType.no_vest: "Без жилета",
    EventType.zone_intrusion: "Вход в запретную зону", EventType.smoking: "Курение",
    EventType.fire: "Огонь", EventType.smoke: "Дым",
}
SEVERITY_TEXT = {Severity.high: "🔴 высокая", Severity.medium: "🟠 средняя", Severity.low: "🟡 низкая"}

Key = tuple[int, EventType]


def load_settings(db: Session, default: TelegramSettings) -> TelegramSettings:
    raw = get_setting(db, TELEGRAM_KEY)
    return TelegramSettings.model_validate(raw) if raw else default


def save_settings(db: Session, value: TelegramSettings) -> None:
    set_setting(db, TELEGRAM_KEY, value.model_dump(mode="json"))


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


class Sender(Protocol):
    async def send_message(self, chat_id: int, text: str) -> int: ...
    async def send_photo(self, chat_id: int, photo: Path, caption: str) -> int: ...
    async def send_video(self, chat_id: int, video: Path, caption: str, reply_to: int | None) -> int: ...


@dataclass
class _Out:
    kind: str
    message: BusMessage | None = None
    text: str = ""
    skipped: int = 0


class TelegramNotifier:

    def __init__(self, session_factory: sessionmaker[Session], sender: Sender, media_root: Path,
                 defaults: TelegramSettings | None = None):
        self._sessions = session_factory
        self._sender = sender
        self._media = media_root
        self._defaults = defaults or TelegramSettings()
        self._queues: dict[int, asyncio.Queue[_Out]] = {}
        self._tasks: dict[int, asyncio.Task] = {}
        self._photo_message: dict[tuple[int, str], int] = {}
        self._pending: dict[Key, int] = {}
        self._names: dict[int, str] = {}
        self._last: dict[Key, float] = {}
        self._flush_task: asyncio.Task | None = None
        self.latency_ms: deque[int] = deque(maxlen=100)
        self.sent = 0
        self.errors = 0

    def settings(self) -> TelegramSettings:
        with self._sessions() as db:
            return load_settings(db, self._defaults)

    @property
    def pending(self) -> int:
        return sum(self._pending.values())


    def chats(self) -> list[int]:
        with self._sessions() as db:
            return list(db.execute(select(TelegramChatRow.chat_id)).scalars())

    def command(self, chat_id: int, name: str) -> str:
        with self._sessions() as db:
            row = db.get(TelegramChatRow, chat_id)
            if name == "start":
                if row is None:
                    db.add(TelegramChatRow(chat_id=chat_id))
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
            self._tasks[chat_id] = asyncio.create_task(self._chat_loop(chat_id, queue), name=f"tg-{chat_id}")

    def _schedule_flush(self, delay_s: float) -> None:
        if self._flush_task is None or self._flush_task.done():
            self._flush_task = asyncio.create_task(self._flush_later(delay_s), name="tg-summary")

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
                log.error("телеграм, чат %s: отправка не удалась: %s", chat_id, e)
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


class AiogramSender:

    def __init__(self, token: str):
        from aiogram import Bot

        self.bot = Bot(token)

    async def send_message(self, chat_id: int, text: str) -> int:
        return (await self.bot.send_message(chat_id, text)).message_id

    async def send_photo(self, chat_id: int, photo: Path, caption: str) -> int:
        from aiogram.types import FSInputFile

        return (await self.bot.send_photo(chat_id, FSInputFile(photo), caption=caption)).message_id

    async def send_video(self, chat_id: int, video: Path, caption: str, reply_to: int | None) -> int:
        from aiogram.types import FSInputFile, ReplyParameters

        reply = ReplyParameters(message_id=reply_to, allow_sending_without_reply=True) if reply_to else None
        return (await self.bot.send_video(chat_id, FSInputFile(video), caption=caption, reply_parameters=reply)).message_id


async def run_bot(sender: AiogramSender, notifier: TelegramNotifier, state: dict) -> None:
    from aiogram import Dispatcher
    from aiogram.filters import Command
    from aiogram.types import Message

    dp = Dispatcher()

    for name in ("start", "stop", "status"):
        async def handler(message: Message, _name: str = name) -> None:
            await message.answer(notifier.command(message.chat.id, _name))

        dp.message.register(handler, Command(name))

    try:
        me = await sender.bot.get_me()
        state.update(running=True, username=me.username, error=None)
        log.info("телеграм-бот @%s запущен", me.username)
        await dp.start_polling(sender.bot, handle_signals=False)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        state.update(running=False, error=f"{type(e).__name__}: {e}")
        log.error("телеграм-бот не запустился: %s", e)
    finally:
        state["running"] = False
        await sender.bot.session.close()
