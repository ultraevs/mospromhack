from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter, deque
from pathlib import Path
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.db.models import CameraRow, EventRow, TelegramChatRow
from backend.app.services.bus import BusMessage
from backend.app.services.media import path_for_url
from shared.schemas import Event, EventStatus, EventType, Severity

log = logging.getLogger("backend.telegram")

MIN_INTERVAL_S = 1.0

EVENT_TEXT = {
    EventType.no_helmet: "Без каски", EventType.no_mask: "Без маски", EventType.no_vest: "Без жилета",
    EventType.zone_intrusion: "Вход в запретную зону", EventType.smoking: "Курение",
    EventType.fire: "Огонь", EventType.smoke: "Дым",
}
SEVERITY_TEXT = {Severity.high: "🔴 высокая", Severity.medium: "🟠 средняя", Severity.low: "🟡 низкая"}


def caption(event: Event) -> str:
    local = event.started_at.astimezone().strftime("%d.%m %H:%M:%S")
    lines = [f"{EVENT_TEXT[event.type]} · важность {SEVERITY_TEXT[event.severity]}", f"Камера: {event.camera_name}"]
    if event.zone_name:
        lines.append(f"Зона: {event.zone_name}")
    lines.append(f"Время: {local}")
    return "\n".join(lines)


def summary(events: list[Event]) -> str:
    counts = Counter((e.camera_name, e.type) for e in events)
    lines = [f"Нарушений за последние секунды: {len(events)}"]
    lines += [f"• {EVENT_TEXT[typ]} — {cam}" + (f" ×{n}" if n > 1 else "") for (cam, typ), n in counts.most_common()]
    return "\n".join(lines)


class Sender(Protocol):
    async def send_message(self, chat_id: int, text: str) -> int: ...
    async def send_photo(self, chat_id: int, photo: Path, caption: str) -> int: ...
    async def send_video(self, chat_id: int, video: Path, caption: str, reply_to: int | None) -> int: ...


class TelegramNotifier:
    def __init__(self, session_factory: sessionmaker[Session], sender: Sender, media_root: Path,
                 min_interval_s: float = MIN_INTERVAL_S):
        self._sessions = session_factory
        self._sender = sender
        self._media = media_root
        self._interval = min_interval_s
        self._queues: dict[int, asyncio.Queue[BusMessage]] = {}
        self._tasks: dict[int, asyncio.Task] = {}
        self._photo_message: dict[tuple[int, str], int] = {}
        self.latency_ms: deque[int] = deque(maxlen=100)
        self.sent = 0
        self.errors = 0


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
                return "Подписка включена. Сюда будут приходить нарушения со стоп-кадром и клипом.\n/stop — отписаться, /status — состояние."
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
        for chat_id in self.chats():
            queue = self._queues.setdefault(chat_id, asyncio.Queue())
            queue.put_nowait(message)
            if chat_id not in self._tasks or self._tasks[chat_id].done():
                self._tasks[chat_id] = asyncio.create_task(self._chat_loop(chat_id, queue), name=f"tg-{chat_id}")

    async def _chat_loop(self, chat_id: int, queue: asyncio.Queue[BusMessage]) -> None:
        while not queue.empty():
            batch = [queue.get_nowait() for _ in range(queue.qsize())]
            opened = [m for m in batch if m.action == "opened"]
            clips = [m for m in batch if m.action == "clip_added"]
            try:
                if len(opened) == 1:
                    await self._send_opened(chat_id, opened[0])
                    await asyncio.sleep(self._interval)
                elif opened:
                    await self._sender.send_message(chat_id, summary([m.event for m in opened]))
                    self.sent += 1
                    await asyncio.sleep(self._interval)
                for m in clips:
                    reply_to = self._photo_message.pop((chat_id, m.event.id), None)
                    video = path_for_url(self._media, m.event.clip_url)
                    if reply_to is None or video is None:
                        continue
                    await self._sender.send_video(chat_id, video, f"Клип: {EVENT_TEXT[m.event.type]}, {m.event.camera_name}", reply_to)
                    self.sent += 1
                    await asyncio.sleep(self._interval)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                self.errors += 1
                log.error("телеграм, чат %s: отправка не удалась: %s", chat_id, e)
                await asyncio.sleep(self._interval)

    async def _send_opened(self, chat_id: int, message: BusMessage) -> None:
        event = message.event
        photo = path_for_url(self._media, event.snapshot_url)
        if photo is not None:
            message_id = await self._sender.send_photo(chat_id, photo, caption(event))
            self._photo_message[(chat_id, event.id)] = message_id
            if len(self._photo_message) > 2000:
                self._photo_message.pop(next(iter(self._photo_message)))
        else:
            await self._sender.send_message(chat_id, caption(event) + "\n(стоп-кадра нет)")
        self.sent += 1
        if message.captured_ts:
            self.latency_ms.append(int(time.time() * 1000) - message.captured_ts)

    async def wait(self) -> None:
        while any(not t.done() for t in self._tasks.values()):
            await asyncio.gather(*[t for t in self._tasks.values() if not t.done()], return_exceptions=True)

    async def close(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)


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
