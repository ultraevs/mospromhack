import asyncio
import json
import time
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.db.session import make_engine, make_session_factory
from backend.app.main import create_app
from backend.app.services import media
from backend.app.services.bus import BusMessage
from backend.app.services.max_bot import MaxApiError, MaxNotifier, MaxSender, caption, command_of, run_bot, summary
from backend.tests.conftest import INTERNAL, event_body
from shared.schemas import Event, MaxSettings

INTERVAL = 0.08
SUMMARY = 0.3
REPEAT = 1.0


def cfg(**over) -> MaxSettings:
    values = {"instant_min_severity": "high", "min_interval_s": INTERVAL, "summary_interval_s": SUMMARY,
              "repeat_interval_s": REPEAT, **over}
    return MaxSettings.model_construct(**{**values, "instant_min_severity":
                                               MaxSettings(instant_min_severity=values["instant_min_severity"]).instant_min_severity})


class FakeSender:
    def __init__(self):
        self.calls: list[dict] = []
        self.fail_next = 0

    async def _record(self, kind, chat_id, **kw) -> int:
        if self.fail_next:
            self.fail_next -= 1
            raise RuntimeError("max is down")
        self.calls.append({"kind": kind, "chat": chat_id, "t": time.monotonic(), **kw})
        return f"mid.{1000 + len(self.calls)}"

    async def send_message(self, chat_id, text):
        return await self._record("message", chat_id, text=text)

    async def send_photo(self, chat_id, photo, caption):
        return await self._record("photo", chat_id, photo=photo, caption=caption)

    async def send_video(self, chat_id, video, caption, reply_to):
        return await self._record("video", chat_id, video=video, caption=caption, reply_to=reply_to)


TYPE_SEVERITY = {"fire": "high", "smoke": "high", "zone_intrusion": "high", "no_helmet": "medium", "no_mask": "medium",
                 "smoking": "medium", "no_vest": "low"}


def make_event(n, media_root=None, type="no_helmet", camera=(1, "Цех 2, ворота"), **over) -> Event:
    eid = f"evt_{n:010d}"
    ev = Event(id=eid, type=type, severity=over.pop("severity", TYPE_SEVERITY[type]), camera_id=camera[0],
               camera_name=camera[1], zone_name="Зона резки", track_id=n,
               started_at=datetime(2026, 10, 4, 12, 31, 5, tzinfo=timezone.utc), confidence=0.6, **over)
    if media_root is not None:
        ev.snapshot_url = media.save(media_root, eid, media.SNAPSHOT, b"jpeg")
    return ev


def make_notifier(tmp_path, **over):
    sessions = make_session_factory(make_engine(tmp_path / "db.sqlite"))
    sender = FakeSender()
    n = MaxNotifier(sessions, sender, tmp_path / "media", cfg(**over))
    n.sender = sender
    n.media = tmp_path / "media"
    return n


@pytest.fixture
async def notifier(tmp_path):
    n = make_notifier(tmp_path)
    yield n
    await n.close()


def kinds(notifier):
    return [c["kind"] for c in notifier.sender.calls]


def test_start_stop_status(tmp_path):
    notifier = make_notifier(tmp_path)
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
    assert ev.started_at.astimezone().strftime("%H:%M:%S") in text and "Пропущено" not in text
    assert "Зона" not in caption(make_event(2).model_copy(update={"zone_name": None}))
    assert caption(ev, skipped=3).splitlines()[-1] == "Пропущено таких же с прошлого сообщения: 3"
    s = summary({("Цех 2, ворота", "no_helmet"): 2, ("Склад", "fire"): 1})
    assert s.splitlines() == ["Сводка нарушений: 3", "• Без каски — Цех 2, ворота ×2", "• Огонь — Склад"]


def test_default_settings_match_the_decision():
    d = MaxSettings()
    assert (d.instant_min_severity.value, d.min_interval_s, d.summary_interval_s, d.repeat_interval_s) == ("high", 1, 60, 300)


async def test_high_event_is_sent_at_once_as_photo_and_clip_as_reply(notifier):
    notifier.command(42, "start")
    ev = make_event(1, notifier.media, type="fire")
    await notifier.handle(BusMessage("opened", ev, captured_ts=int(time.time() * 1000) - 150))
    await notifier.wait()
    photo = notifier.sender.calls[0]
    assert photo["kind"] == "photo" and photo["chat"] == 42 and photo["photo"].read_bytes() == b"jpeg"
    assert "Огонь" in photo["caption"] and "высокая" in photo["caption"]
    assert 150 <= notifier.latency_ms[0] < 2000

    ev.clip_url = media.save(notifier.media, ev.id, media.CLIP, b"mp4")
    await notifier.handle(BusMessage("clip_added", ev))
    await notifier.wait()
    video = notifier.sender.calls[1]
    assert video["kind"] == "video" and video["reply_to"] == "mid.1001" and video["video"].read_bytes() == b"mp4"
    assert notifier.sent == 2 and notifier.pending == 0


async def test_no_subscribers_no_messages_and_closed_is_ignored(notifier):
    await notifier.handle(BusMessage("opened", make_event(1, notifier.media, type="fire")))
    await notifier.handle(BusMessage("opened", make_event(2, notifier.media)))
    assert notifier.pending == 0
    notifier.command(42, "start")
    await notifier.handle(BusMessage("closed", make_event(3, notifier.media, type="fire")))
    await notifier.wait()
    assert notifier.sender.calls == []


async def test_event_without_snapshot_is_sent_as_text(notifier):
    notifier.command(42, "start")
    await notifier.handle(BusMessage("opened", make_event(1, type="smoke")))
    await notifier.wait()
    assert notifier.sender.calls[0]["kind"] == "message" and "стоп-кадра нет" in notifier.sender.calls[0]["text"]


async def test_messages_to_one_chat_are_never_closer_than_interval(notifier):
    notifier.command(42, "start")
    for n, typ in enumerate(("fire", "smoke", "zone_intrusion"), 1):
        await notifier.handle(BusMessage("opened", make_event(n, notifier.media, type=typ)))
    await notifier.wait()
    times = [c["t"] for c in notifier.sender.calls]
    assert kinds(notifier) == ["photo"] * 3 and all(b - a >= INTERVAL * 0.9 for a, b in zip(times, times[1:]))


async def test_each_chat_gets_the_message(notifier):
    notifier.command(1, "start")
    notifier.command(2, "start")
    await notifier.handle(BusMessage("opened", make_event(1, notifier.media, type="fire")))
    await notifier.wait()
    calls = notifier.sender.calls
    assert sorted(c["chat"] for c in calls) == [1, 2] and abs(calls[0]["t"] - calls[1]["t"]) < INTERVAL / 2


async def test_send_failure_is_counted_and_next_event_still_goes(notifier):
    notifier.command(42, "start")
    notifier.sender.fail_next = 1
    await notifier.handle(BusMessage("opened", make_event(1, notifier.media, type="fire")))
    await notifier.wait()
    assert notifier.errors == 1 and notifier.sender.calls == []
    await notifier.handle(BusMessage("opened", make_event(2, notifier.media, type="smoke")))
    await notifier.wait()
    assert kinds(notifier) == ["photo"]


async def test_repeat_of_same_type_on_same_camera_is_skipped_and_counted(notifier):
    notifier.command(42, "start")
    await notifier.handle(BusMessage("opened", make_event(1, notifier.media, type="zone_intrusion")))
    for n in (2, 3, 4):
        await notifier.handle(BusMessage("opened", make_event(n, notifier.media, type="zone_intrusion")))
    await notifier.wait()
    assert kinds(notifier) == ["photo"] and notifier.pending == 3
    await asyncio.sleep(REPEAT)
    await notifier.handle(BusMessage("opened", make_event(5, notifier.media, type="zone_intrusion")))
    await notifier.wait()
    photos = [c for c in notifier.sender.calls if c["kind"] == "photo"]
    assert len(photos) == 2 and photos[1]["caption"].splitlines()[-1] == "Пропущено таких же с прошлого сообщения: 3"
    assert notifier.pending == 0


async def test_skipped_repeats_reach_the_summary_when_no_new_event_comes(notifier):
    notifier.command(42, "start")
    for n in (1, 2, 3):
        await notifier.handle(BusMessage("opened", make_event(n, notifier.media, type="fire")))
    await asyncio.sleep(SUMMARY * 1.5)
    assert kinds(notifier) == ["photo"]
    await asyncio.sleep(REPEAT)
    await notifier.wait()
    assert kinds(notifier) == ["photo", "message"]
    assert notifier.sender.calls[1]["text"].splitlines() == ["Сводка нарушений: 2", "• Огонь — Цех 2, ворота ×2"]


async def test_other_camera_and_other_type_are_not_repeats(notifier):
    notifier.command(42, "start")
    await notifier.handle(BusMessage("opened", make_event(1, notifier.media, type="fire")))
    await notifier.handle(BusMessage("opened", make_event(2, notifier.media, type="fire", camera=(2, "Склад"))))
    await notifier.handle(BusMessage("opened", make_event(3, notifier.media, type="smoke")))
    await notifier.wait()
    assert kinds(notifier) == ["photo"] * 3 and notifier.pending == 0


async def test_clip_of_skipped_event_is_not_sent(notifier):
    notifier.command(42, "start")
    events = [make_event(n, notifier.media, type="fire") for n in (1, 2)]
    for ev in events:
        await notifier.handle(BusMessage("opened", ev))
    await notifier.wait()
    for ev in events:
        ev.clip_url = media.save(notifier.media, ev.id, media.CLIP, b"mp4")
        await notifier.handle(BusMessage("clip_added", ev))
    await notifier.wait()
    assert kinds(notifier) == ["photo", "video"]


async def test_medium_and_low_events_go_to_one_summary(notifier):
    notifier.command(42, "start")
    started = time.monotonic()
    for n in (1, 2, 3):
        await notifier.handle(BusMessage("opened", make_event(n, notifier.media)))
    await notifier.handle(BusMessage("opened", make_event(4, notifier.media, type="no_vest", camera=(2, "Склад"))))
    await notifier.wait()
    assert notifier.sender.calls == [] and notifier.pending == 4
    await asyncio.sleep(SUMMARY * 1.5)
    await notifier.wait()
    assert kinds(notifier) == ["message"] and notifier.pending == 0
    call = notifier.sender.calls[0]
    assert call["text"].splitlines() == ["Сводка нарушений: 4", "• Без каски — Цех 2, ворота ×3", "• Без жилета — Склад"]
    assert call["t"] - started >= SUMMARY * 0.9


async def test_summary_mentions_a_type_once_per_repeat_interval(notifier):
    notifier.command(42, "start")
    await notifier.handle(BusMessage("opened", make_event(1, notifier.media)))
    await asyncio.sleep(SUMMARY * 1.3)
    await notifier.handle(BusMessage("opened", make_event(2, notifier.media)))
    await notifier.handle(BusMessage("opened", make_event(3, notifier.media, type="no_mask")))
    await asyncio.sleep(SUMMARY * 1.3)
    await notifier.wait()
    texts = [c["text"].splitlines() for c in notifier.sender.calls]
    assert texts == [["Сводка нарушений: 1", "• Без каски — Цех 2, ворота"], ["Сводка нарушений: 1", "• Без маски — Цех 2, ворота"]]
    assert notifier.pending == 1
    await asyncio.sleep(REPEAT)
    await notifier.wait()
    assert notifier.sender.calls[-1]["text"].splitlines() == ["Сводка нарушений: 1", "• Без каски — Цех 2, ворота"]


async def test_quiet_period_sends_nothing(notifier):
    notifier.command(42, "start")
    await asyncio.sleep(SUMMARY * 1.5)
    assert notifier.sender.calls == []


async def test_instant_threshold_is_a_setting(tmp_path):
    notifier = make_notifier(tmp_path, instant_min_severity="medium")
    notifier.command(42, "start")
    await notifier.handle(BusMessage("opened", make_event(1, notifier.media)))
    await notifier.handle(BusMessage("opened", make_event(2, notifier.media, type="no_vest")))
    await notifier.wait()
    assert kinds(notifier) == ["photo"] and notifier.pending == 1
    await notifier.close()


async def test_settings_from_db_override_defaults(tmp_path):
    from backend.app.services.max_bot import save_settings

    notifier = make_notifier(tmp_path)
    notifier.command(42, "start")
    with notifier._sessions() as db:
        save_settings(db, MaxSettings(instant_min_severity="low", repeat_interval_s=0, min_interval_s=0.05))
        db.commit()
    for n in (1, 2):
        await notifier.handle(BusMessage("opened", make_event(n, notifier.media, type="no_vest")))
    await notifier.wait()
    assert kinds(notifier) == ["photo", "photo"] and notifier.pending == 0
    await notifier.close()


def test_backend_without_token_has_bot_disabled(client):
    mx = client.get("/api/max").json()
    assert mx == {"enabled": False, "reason": "no_token", "running": False, "username": None, "error": None,
                  "subscribers": 0, "sent": 0, "errors": 0, "pending": 0}
    assert client.get("/api/stats").json()["latency"]["capture_to_max"] is None


def test_backend_sends_event_to_subscribed_chat(settings):
    sender = FakeSender()
    with TestClient(create_app(settings, run_sources=False, max_sender=sender)) as c:
        app_state = c.app.state.app
        app_state.notifier.command(42, "start")
        assert c.get("/api/max").json()["subscribers"] == 1 and c.get("/api/max").json()["enabled"] is True
        cam = c.post("/api/cameras", json={"name": "Цех", "source_type": "file", "source": "demo.mp4"}).json()
        body = event_body(cam["id"], type="fire", severity="high", track_id=None, captured_ts=int(time.time() * 1000) - 120)
        r = c.post("/internal/events", headers=INTERNAL, data={"event": json.dumps(body)},
                   files={"snapshot": ("snapshot.jpg", b"jpeg-bytes", "image/jpeg")})
        assert r.status_code == 201
        end = time.monotonic() + 3
        while not sender.calls and time.monotonic() < end:
            time.sleep(0.02)
        assert sender.calls and sender.calls[0]["kind"] == "photo" and sender.calls[0]["chat"] == 42
        assert sender.calls[0]["photo"].read_bytes() == b"jpeg-bytes" and "Камера: Цех" in sender.calls[0]["caption"]
        lat = c.get("/api/stats").json()["latency"]["capture_to_max"]
        assert lat["events"] == 1 and 120 <= lat["avg_ms"] < 3000
        assert c.get("/api/max").json()["sent"] == 1
        r = c.post("/internal/events", headers=INTERNAL, json=event_body(cam["id"]))
        assert r.status_code == 201
        end = time.monotonic() + 3
        while c.get("/api/max").json()["pending"] == 0 and time.monotonic() < end:
            time.sleep(0.02)
        assert c.get("/api/max").json()["pending"] == 1 and len(sender.calls) == 1


def test_switch_turns_bot_off_even_with_token_and_sender(settings, monkeypatch):
    sender = FakeSender()
    off = settings.model_copy(update={"max_enabled": False, "max_bot_token": "fake-max-token"})
    with TestClient(create_app(off, run_sources=False, max_sender=sender)) as c:
        assert c.app.state.app.notifier is None
        mx = c.get("/api/max").json()
        assert (mx["enabled"], mx["reason"], mx["error"]) == (False, "switched_off", None)
        cam = c.post("/api/cameras", json={"name": "Цех", "source_type": "file", "source": "demo.mp4"}).json()
        body = event_body(cam["id"], type="fire", severity="high", track_id=None)
        assert c.post("/internal/events", headers=INTERNAL, json=body).status_code == 201
        time.sleep(0.3)
        assert sender.calls == [] and c.get("/api/events").json()["total"] == 1
        assert not any("MAX" in p for p in c.get("/api/health").json()["problems"])
    from shared.config import Settings
    for raw, expected in (("false", False), ("0", False), ("off", False), ("true", True), ("1", True)):
        monkeypatch.setenv("MAX_ENABLED", raw)
        assert Settings(_env_file=None).max_enabled is expected


def test_enabled_bot_reports_no_reason(settings):
    with TestClient(create_app(settings, run_sources=False, max_sender=FakeSender())) as c:
        mx = c.get("/api/max").json()
        assert (mx["enabled"], mx["reason"]) == (True, None)


def test_max_settings_api(client):
    default = {"instant_min_severity": "high", "min_interval_s": 1, "summary_interval_s": 60, "repeat_interval_s": 300}
    assert client.get("/api/settings/max").json() == default
    new = {"instant_min_severity": "medium", "min_interval_s": 2, "summary_interval_s": 120, "repeat_interval_s": 600}
    r = client.put("/api/settings/max", json=new)
    assert r.status_code == 200 and r.json() == new and client.get("/api/settings/max").json() == new
    for field, value in (("instant_min_severity", "urgent"), ("min_interval_s", 0), ("summary_interval_s", 0),
                         ("repeat_interval_s", -1)):
        assert client.put("/api/settings/max", json={**new, field: value}).status_code == 422, field
    assert client.get("/api/settings/max").json() == new


# вместо platform-api: пишет запросы, отвечает как MAX
class FakeMax:

    def __init__(self, not_ready=0, updates=None, me_status=200):
        self.requests: list[tuple[str, str, dict, bytes]] = []
        self.not_ready = not_ready
        self.updates = list(updates or [])
        self.me_status = me_status

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        self.requests.append((request.method, request.url.path, params, request.content))
        host, path = request.url.host, request.url.path
        if host == "upload.test":
            return httpx.Response(200, json={"photos": {"p1": {"token": "img-token"}}} if path == "/img" else None,
                                  text=None if path == "/img" else "<retval>1</retval>")
        if path == "/me":
            if self.me_status != 200:
                return httpx.Response(self.me_status, json={"code": "verify.token", "message": "Invalid access_token"})
            return httpx.Response(200, json={"user_id": 1, "name": "Контроль ТБ", "username": "safety_bot", "is_bot": True})
        if path == "/me/commands":
            return httpx.Response(200, json={})
        if path == "/uploads":
            if params["type"] == "image":
                return httpx.Response(200, json={"url": "https://upload.test/img"})
            return httpx.Response(200, json={"url": "https://upload.test/vid", "token": "vid-token"})
        if path == "/messages":
            if self.not_ready:
                self.not_ready -= 1
                return httpx.Response(400, json={"code": "attachment.not.ready", "message": "Key: errors.process.attachment.file.not.processed"})
            return httpx.Response(200, json={"message": {"body": {"mid": f"mid.{len(self.requests)}", "seq": 1}}})
        if path == "/updates":
            if not self.updates:
                await asyncio.sleep(0.05)  # как long polling, иначе цикл бота не отпускает loop
            batch = self.updates.pop(0) if self.updates else []
            return httpx.Response(200, json={"updates": batch, "marker": len(self.requests)})
        return httpx.Response(404, json={"code": "not.found", "message": path})

    def sent(self) -> list[dict]:
        return [{"chat": int(q["chat_id"]), **json.loads(body)} for m, path, q, body in self.requests if path == "/messages"]


def make_sender(fake: FakeMax) -> MaxSender:
    return MaxSender("max-token", "https://api.test", ready_pauses=(0.01, 0.01), transport=httpx.MockTransport(fake))


def test_sender_needs_token_and_trusts_mincifry_root():
    with pytest.raises(ValueError):
        MaxSender("  ")
    sender = MaxSender("max-token")
    assert sender.client.headers["Authorization"] == "max-token" and str(sender.client.base_url).startswith("https://platform-api2.max.ru")


async def test_photo_is_uploaded_then_sent_with_token(tmp_path):
    fake = FakeMax()
    sender = make_sender(fake)
    photo = tmp_path / "s.jpg"
    photo.write_bytes(b"jpeg")
    mid = await sender.send_photo(42, photo, "Огонь")
    assert [(m, p) for m, p, _, _ in fake.requests] == [("POST", "/uploads"), ("POST", "/img"), ("POST", "/messages")]
    assert b"jpeg" in fake.requests[1][3] and b'name="data"' in fake.requests[1][3]
    assert fake.sent() == [{"chat": 42, "text": "Огонь",
                            "attachments": [{"type": "image", "payload": {"photos": {"p1": {"token": "img-token"}}}}]}]
    assert mid == "mid.3"
    await sender.close()


async def test_clip_waits_until_max_processed_it_and_goes_as_reply(tmp_path):
    fake = FakeMax(not_ready=2)
    sender = make_sender(fake)
    clip = tmp_path / "c.mp4"
    clip.write_bytes(b"mp4")
    await sender.send_video(-77, clip, "Клип", "mid.photo")
    sent = fake.sent()
    assert len(sent) == 3 and sent[-1] == {"chat": -77, "text": "Клип", "link": {"type": "reply", "mid": "mid.photo"},
                                           "attachments": [{"type": "video", "payload": {"token": "vid-token"}}]}
    fake.not_ready = 5
    with pytest.raises(MaxApiError) as e:
        await sender.send_video(-77, clip, "Клип", None)
    assert e.value.code == "attachment.not.ready"
    await sender.close()


def test_commands_come_from_start_button_and_slash_messages():
    def msg(text, chat_id=5, is_bot=False):
        return {"update_type": "message_created", "timestamp": 1,
                "message": {"sender": {"user_id": 9, "is_bot": is_bot}, "recipient": {"chat_id": chat_id, "chat_type": "dialog"},
                            "body": {"mid": "m", "seq": 1, "text": text}}}

    assert command_of({"update_type": "bot_started", "timestamp": 1, "chat_id": 5, "user": {"user_id": 9}}) == (5, "start")
    assert command_of(msg("/status")) == (5, "status")
    assert command_of(msg(" /Stop@safety_bot please", chat_id=-100)) == (-100, "stop")
    assert command_of(msg("/")) == (5, "")
    assert command_of(msg("привет")) is None
    assert command_of(msg("/start", is_bot=True)) is None
    assert command_of(msg("/start", chat_id=None)) is None
    assert command_of({"update_type": "message_callback", "timestamp": 1}) is None


async def test_bot_answers_commands_and_keeps_marker(tmp_path):
    fake = FakeMax(updates=[[{"update_type": "bot_started", "timestamp": 1, "chat_id": 5, "user": {"user_id": 9}}],
                            [{"update_type": "message_created", "timestamp": 2,
                              "message": {"sender": {"user_id": 9}, "recipient": {"chat_id": 5, "chat_type": "dialog"},
                                          "body": {"mid": "m", "seq": 2, "text": "/status"}}}]])
    notifier = make_notifier(tmp_path)
    state = {"enabled": True, "running": False, "username": None, "error": None, "reason": None}
    task = asyncio.create_task(run_bot(make_sender(fake), notifier, state))
    end = time.monotonic() + 3
    while len(fake.sent()) < 2 and time.monotonic() < end:
        await asyncio.sleep(0.01)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert state["username"] == "safety_bot" and state["running"] is False
    sent = fake.sent()
    assert "Подписка включена" in sent[0]["text"] and "Камер в работе" in sent[1]["text"] and notifier.chats() == [5]
    polls = [q for m, p, q, _ in fake.requests if p == "/updates"]
    assert "marker" not in polls[0] and int(polls[1]["marker"]) > 0
    assert polls[0]["types"] == "message_created,bot_started"
    await notifier.close()


async def test_bad_token_stops_bot_with_reason(tmp_path):
    notifier = make_notifier(tmp_path)
    state = {"enabled": True, "running": False, "username": None, "error": None, "reason": None}
    await run_bot(make_sender(FakeMax(me_status=401)), notifier, state)
    assert state["reason"] == "invalid_token" and "verify.token" in state["error"] and state["running"] is False
    await notifier.close()
