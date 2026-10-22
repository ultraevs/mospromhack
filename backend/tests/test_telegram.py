import asyncio
import json
import time
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.app.db.session import make_engine, make_session_factory
from backend.app.main import create_app
from backend.app.services import media
from backend.app.services.bus import BusMessage
from backend.app.services.telegram import TelegramNotifier, caption, summary
from backend.tests.conftest import INTERNAL, event_body
from shared.schemas import Event

INTERVAL = 0.08


class FakeSender:
    def __init__(self):
        self.calls: list[dict] = []
        self.fail_next = 0

    async def _record(self, kind, chat_id, **kw) -> int:
        if self.fail_next:
            self.fail_next -= 1
            raise RuntimeError("telegram is down")
        self.calls.append({"kind": kind, "chat": chat_id, "t": time.monotonic(), **kw})
        return 1000 + len(self.calls)

    async def send_message(self, chat_id, text):
        return await self._record("message", chat_id, text=text)

    async def send_photo(self, chat_id, photo, caption):
        return await self._record("photo", chat_id, photo=photo, caption=caption)

    async def send_video(self, chat_id, video, caption, reply_to):
        return await self._record("video", chat_id, video=video, caption=caption, reply_to=reply_to)


def make_event(n, media_root=None, **over) -> Event:
    eid = f"evt_{n:010d}"
    ev = Event(id=eid, type="no_helmet", severity="medium", camera_id=1, camera_name="Цех 2, ворота", zone_name="Зона резки",
               track_id=n, started_at=datetime(2026, 10, 4, 12, 31, 5, tzinfo=timezone.utc), confidence=0.6, **over)
    if media_root is not None:
        ev.snapshot_url = media.save(media_root, eid, media.SNAPSHOT, b"jpeg")
    return ev


@pytest.fixture
def notifier(tmp_path):
    sessions = make_session_factory(make_engine(tmp_path / "db.sqlite"))
    sender = FakeSender()
    n = TelegramNotifier(sessions, sender, tmp_path / "media", min_interval_s=INTERVAL)
    n.sender = sender
    n.media = tmp_path / "media"
    return n


def test_start_stop_status(notifier):
    assert notifier.chats() == []
    assert "Подписка включена" in notifier.command(42, "start")
    notifier.command(42, "start")
    notifier.command(7, "start")
    assert sorted(notifier.chats()) == [7, 42]
    status = notifier.command(42, "status")
    assert "Камер в работе: 0" in status and "Открытых нарушений: 0" in status and "включена" in status
    assert "Подписка выключена" in notifier.command(42, "stop")
    assert notifier.chats() == [7]
    assert "выключена" in notifier.command(42, "status")
    notifier.command(42, "stop")


def test_caption_and_summary_text():
    ev = make_event(1)
    text = caption(ev)
    assert "Без каски" in text and "средняя" in text and "Камера: Цех 2, ворота" in text and "Зона: Зона резки" in text
    assert ev.started_at.astimezone().strftime("%H:%M:%S") in text
    assert "Зона" not in caption(make_event(2).model_copy(update={"zone_name": None}))
    s = summary([make_event(1), make_event(2), make_event(3).model_copy(update={"type": "fire", "camera_name": "Склад"})])
    assert s.splitlines()[0] == "Нарушений за последние секунды: 3"
    assert "• Без каски — Цех 2, ворота ×2" in s and "• Огонь — Склад" in s


async def test_event_is_sent_as_photo_and_clip_as_reply(notifier):
    notifier.command(42, "start")
    ev = make_event(1, notifier.media)
    await notifier.handle(BusMessage("opened", ev, captured_ts=int(time.time() * 1000) - 150))
    await notifier.wait()
    photo = notifier.sender.calls[0]
    assert photo["kind"] == "photo" and photo["chat"] == 42 and photo["photo"].read_bytes() == b"jpeg"
    assert "Без каски" in photo["caption"]
    assert 150 <= notifier.latency_ms[0] < 2000

    ev.clip_url = media.save(notifier.media, ev.id, media.CLIP, b"mp4")
    await notifier.handle(BusMessage("clip_added", ev))
    await notifier.wait()
    video = notifier.sender.calls[1]
    assert video["kind"] == "video" and video["reply_to"] == 1001 and video["video"].read_bytes() == b"mp4"
    assert notifier.sent == 2


async def test_no_subscribers_no_messages_and_closed_is_ignored(notifier):
    await notifier.handle(BusMessage("opened", make_event(1, notifier.media)))
    notifier.command(42, "start")
    await notifier.handle(BusMessage("closed", make_event(2, notifier.media)))
    await notifier.wait()
    assert notifier.sender.calls == []


async def test_event_without_snapshot_is_sent_as_text(notifier):
    notifier.command(42, "start")
    await notifier.handle(BusMessage("opened", make_event(1)))
    await notifier.wait()
    assert notifier.sender.calls[0]["kind"] == "message" and "стоп-кадра нет" in notifier.sender.calls[0]["text"]


async def test_rate_limit_one_message_per_interval_and_burst_becomes_summary(notifier):
    notifier.command(42, "start")
    await notifier.handle(BusMessage("opened", make_event(1, notifier.media)))
    await asyncio.sleep(0.01)
    for n in range(2, 8):
        await notifier.handle(BusMessage("opened", make_event(n, notifier.media)))
    await notifier.wait()
    calls = notifier.sender.calls
    assert [c["kind"] for c in calls] == ["photo", "message"]
    assert calls[1]["text"].startswith("Нарушений за последние секунды: 6")
    assert calls[1]["t"] - calls[0]["t"] >= INTERVAL * 0.9


async def test_messages_are_never_closer_than_interval(notifier):
    notifier.command(42, "start")
    for n in range(1, 4):
        await notifier.handle(BusMessage("opened", make_event(n, notifier.media)))
        await asyncio.sleep(INTERVAL * 1.5)
    await notifier.wait()
    times = [c["t"] for c in notifier.sender.calls]
    assert len(times) == 3 and all(b - a >= INTERVAL * 0.9 for a, b in zip(times, times[1:]))


async def test_clip_of_summarized_event_is_not_sent(notifier):
    notifier.command(42, "start")
    events = [make_event(n, notifier.media) for n in (1, 2, 3)]
    for ev in events:
        await notifier.handle(BusMessage("opened", ev))
    await notifier.wait()
    assert [c["kind"] for c in notifier.sender.calls] == ["message"]
    events[0].clip_url = media.save(notifier.media, events[0].id, media.CLIP, b"mp4")
    await notifier.handle(BusMessage("clip_added", events[0]))
    await notifier.wait()
    assert len(notifier.sender.calls) == 1


async def test_each_chat_has_its_own_limit(notifier):
    notifier.command(1, "start")
    notifier.command(2, "start")
    await notifier.handle(BusMessage("opened", make_event(1, notifier.media)))
    await notifier.wait()
    calls = notifier.sender.calls
    assert sorted(c["chat"] for c in calls) == [1, 2] and abs(calls[0]["t"] - calls[1]["t"]) < INTERVAL / 2


async def test_send_failure_is_counted_and_next_event_still_goes(notifier):
    notifier.command(42, "start")
    notifier.sender.fail_next = 1
    await notifier.handle(BusMessage("opened", make_event(1, notifier.media)))
    await notifier.wait()
    assert notifier.errors == 1 and notifier.sender.calls == []
    await notifier.handle(BusMessage("opened", make_event(2, notifier.media)))
    await notifier.wait()
    assert [c["kind"] for c in notifier.sender.calls] == ["photo"]


def test_backend_without_token_has_bot_disabled(client):
    tg = client.get("/api/telegram").json()
    assert tg == {"enabled": False, "running": False, "username": None, "error": None, "subscribers": 0, "sent": 0, "errors": 0}
    assert client.get("/api/stats").json()["latency"]["capture_to_telegram"] is None


def test_backend_sends_event_to_subscribed_chat(settings):
    sender = FakeSender()
    with TestClient(create_app(settings, run_sources=False, telegram_sender=sender)) as c:
        app_state = c.app.state.app
        app_state.notifier.command(42, "start")
        assert c.get("/api/telegram").json()["subscribers"] == 1 and c.get("/api/telegram").json()["enabled"] is True
        cam = c.post("/api/cameras", json={"name": "Цех", "source_type": "file", "source": "demo.mp4"}).json()
        body = event_body(cam["id"], captured_ts=int(time.time() * 1000) - 120)
        r = c.post("/internal/events", headers=INTERNAL, data={"event": json.dumps(body)},
                   files={"snapshot": ("snapshot.jpg", b"jpeg-bytes", "image/jpeg")})
        assert r.status_code == 201
        end = time.monotonic() + 3
        while not sender.calls and time.monotonic() < end:
            time.sleep(0.02)
        assert sender.calls and sender.calls[0]["kind"] == "photo" and sender.calls[0]["chat"] == 42
        assert sender.calls[0]["photo"].read_bytes() == b"jpeg-bytes" and "Камера: Цех" in sender.calls[0]["caption"]
        lat = c.get("/api/stats").json()["latency"]["capture_to_telegram"]
        assert lat["events"] == 1 and 120 <= lat["avg_ms"] < 3000
        assert c.get("/api/telegram").json()["sent"] == 1


def test_aiogram_sender_accepts_token_format():
    from backend.app.services.telegram import AiogramSender

    sender = AiogramSender("123456:AAH-fake-token-for-construction-only")
    assert sender.bot.id == 123456
