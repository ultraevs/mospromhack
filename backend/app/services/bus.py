from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Literal

from shared.schemas import Event

log = logging.getLogger("backend.bus")

Action = Literal["opened", "clip_added", "closed"]


@dataclass(frozen=True)
class BusMessage:
    action: Action
    event: Event
    captured_ts: int | None = None


Handler = Callable[[BusMessage], Awaitable[None]]


class EventBus:
    def __init__(self, queue_size: int = 1000) -> None:
        self._queue_size = queue_size
        self._subs: list[tuple[str, asyncio.Queue[BusMessage], Handler]] = []
        self._tasks: list[asyncio.Task] = []

    def subscribe(self, name: str, handler: Handler) -> None:
        self._subs.append((name, asyncio.Queue(maxsize=self._queue_size), handler))

    def start(self) -> None:
        for name, queue, handler in self._subs:
            self._tasks.append(asyncio.create_task(self._run(name, queue, handler), name=f"bus-{name}"))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    def publish(self, message: BusMessage) -> None:
        for name, queue, _ in self._subs:
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                log.error("очередь подписчика %s переполнена, событие %s потеряно для него", name, message.event.id)

    async def _run(self, name: str, queue: asyncio.Queue[BusMessage], handler: Handler) -> None:
        while True:
            message = await queue.get()
            try:
                await handler(message)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("подписчик %s упал на событии %s", name, message.event.id)

    async def drain(self) -> None:
        while any(not q.empty() for _, q, _ in self._subs):
            await asyncio.sleep(0.01)
