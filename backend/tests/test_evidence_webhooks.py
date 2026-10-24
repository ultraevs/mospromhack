import hashlib
import hmac
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.services.webhooks import sign
from backend.tests.conftest import INTERNAL, event_body

JPEG = b"\xff\xd8\xff\xe0" + b"snapshot-bytes" * 10
JPEG_CLEAN = b"\xff\xd8\xff\xe0" + b"clean-bytes" * 10
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"clip" * 50


class Receiver:

    def __init__(self):
        self.requests: list[dict] = []
        self.statuses: list[int] = []
        self.delay = 0.0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                outer.requests.append({"path": self.path, "headers": dict(self.headers), "body": body})
                time.sleep(outer.delay)
                self.send_response(outer.statuses.pop(0) if outer.statuses else 200)
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/hook"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def receiver():
    r = Receiver()
    yield r
    r.close()


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings, run_sources=False, webhook_retry_pauses=(0.02, 0.04, 0.06))) as c:
        yield c


def wait_for(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def post_event(client, camera_id, files=True, **over):
    body = event_body(camera_id, **over)
    if not files:
        r = client.post("/internal/events", headers=INTERNAL, json=body)
    else:
        r = client.post("/internal/events", headers=INTERNAL, data={"event": json.dumps(body)},
                        files={"snapshot": ("snapshot.jpg", JPEG, "image/jpeg"), "clean": ("clean.jpg", JPEG_CLEAN, "image/jpeg")})
    assert r.status_code == 201, r.text
    return r.json()


def add_hook(client, url, **over):
    r = client.post("/api/webhooks", json={"url": url, "secret": "s3cret", **over})
    assert r.status_code == 201, r.text
    return r.json()


def deliveries(client, hook_id):
    return client.get(f"/api/webhooks/{hook_id}/deliveries").json()


def test_event_with_snapshots_is_stored_and_served(client, camera, settings):
    ev = post_event(client, camera["id"])
    assert ev["snapshot_url"] == f"/media/events/{ev['id']}/snapshot.jpg"
    assert ev["snapshot_clean_url"] == f"/media/events/{ev['id']}/clean.jpg"
    assert ev["clip_url"] is None
    assert (settings.data_dir / "media" / "events" / ev["id"] / "snapshot.jpg").read_bytes() == JPEG
    r = client.get(ev["snapshot_url"])
    assert r.status_code == 200 and r.content == JPEG and r.headers["content-type"] == "image/jpeg"
    assert client.get(ev["snapshot_clean_url"]).content == JPEG_CLEAN
    assert client.get(f"/api/events/{ev['id']}").json()["snapshot_url"] == ev["snapshot_url"]


def test_event_without_files_and_invalid_multipart(client, camera):
    assert post_event(client, camera["id"], files=False)["snapshot_url"] is None
    r = client.post("/internal/events", headers=INTERNAL, data={"event": json.dumps(event_body(camera["id"], type="bogus"))},
                    files={"snapshot": ("s.jpg", JPEG, "image/jpeg")})
    assert r.status_code == 422
    r = client.post("/internal/events", headers=INTERNAL, data={"other": "x"}, files={"snapshot": ("s.jpg", JPEG, "image/jpeg")})
    assert r.status_code == 422
    assert client.post("/internal/events", data={"event": "{}"}, files={"snapshot": ("s.jpg", JPEG, "image/jpeg")}).status_code == 401


def test_clip_upload_updates_event_and_notifies(client, camera):
    ev = post_event(client, camera["id"])
    with client.websocket_connect("/ws/events") as ws:
        r = client.post(f"/internal/events/{ev['id']}/clip", headers=INTERNAL, files={"clip": ("clip.mp4", MP4, "video/mp4")})
        assert r.status_code == 200 and r.json()["clip_url"] == f"/media/events/{ev['id']}/clip.mp4"
        msg = ws.receive_json()
    assert msg["type"] == "event_updated" and msg["event"]["clip_url"] == r.json()["clip_url"]
    got = client.get(r.json()["clip_url"])
    assert got.content == MP4 and got.headers["content-type"] == "video/mp4"
    assert client.post("/internal/events/evt_0000000000/clip", headers=INTERNAL,
                       files={"clip": ("clip.mp4", MP4, "video/mp4")}).status_code == 404
    assert client.post(f"/internal/events/{ev['id']}/clip", files={"clip": ("clip.mp4", MP4, "video/mp4")}).status_code == 401
    assert client.post(f"/internal/events/{ev['id']}/clip", headers=INTERNAL,
                       files={"clip": ("clip.mp4", b"", "video/mp4")}).status_code == 422


def test_anon_clip_is_stored_separately(client, camera):
    ev = post_event(client, camera["id"])
    r = client.post(f"/internal/events/{ev['id']}/clip", headers=INTERNAL, params={"anon": "true"},
                    files={"clip": ("clip_anon.mp4", MP4, "video/mp4")})
    assert r.status_code == 200
    assert r.json()["clip_anon_url"] == f"/media/events/{ev['id']}/clip_anon.mp4" and r.json()["clip_url"] is None
    assert client.get(r.json()["clip_anon_url"]).content == MP4


def test_media_path_cannot_escape(client):
    assert client.get("/media/../db.sqlite").status_code == 404
    assert client.get("/media/events/nope/snapshot.jpg").status_code == 404


def test_webhook_crud(client):
    assert client.get("/api/webhooks").json() == []
    hook = add_hook(client, "http://127.0.0.1:9/hook", event_types=["fire", "smoke"], min_severity="high", send_closed=True)
    assert hook == {"id": hook["id"], "url": "http://127.0.0.1:9/hook", "secret": "s3cret", "event_types": ["fire", "smoke"],
                    "min_severity": "high", "send_closed": True, "enabled": True}
    r = client.patch(f"/api/webhooks/{hook['id']}", json={"enabled": False, "event_types": []})
    assert r.status_code == 200 and r.json()["enabled"] is False and r.json()["event_types"] == []
    assert r.json()["min_severity"] == "high"
    assert client.get("/api/webhooks").json() == [r.json()]
    assert client.delete(f"/api/webhooks/{hook['id']}").status_code == 204
    assert client.delete(f"/api/webhooks/{hook['id']}").status_code == 404
    assert client.get(f"/api/webhooks/{hook['id']}/deliveries").status_code == 404
    assert client.patch(f"/api/webhooks/{hook['id']}", json={"enabled": True}).status_code == 404
    assert client.post(f"/api/webhooks/{hook['id']}/test").status_code == 404


def test_webhook_validation(client):
    for body in ({"url": "not-a-url", "secret": "x"}, {"url": "http://a/b", "secret": ""}, {"url": "http://a/b"},
                 {"url": "http://a/b", "secret": "x", "event_types": ["explosion"]},
                 {"url": "http://a/b", "secret": "x", "min_severity": "critical"}):
        assert client.post("/api/webhooks", json=body).status_code == 422, body


def test_webhook_gets_signed_event_on_open_and_clip(client, camera, receiver):
    hook = add_hook(client, receiver.url)
    ev = post_event(client, camera["id"])
    assert wait_for(lambda: len(receiver.requests) == 1)
    req = receiver.requests[0]
    assert json.loads(req["body"]) == ev
    assert req["headers"]["X-Event-Type"] == "no_helmet" and req["headers"]["X-Event-Action"] == "opened"
    expected = "sha256=" + hmac.new(b"s3cret", req["body"], hashlib.sha256).hexdigest()
    assert req["headers"]["X-Signature"] == expected == sign(req["body"], "s3cret")
    assert req["headers"]["Content-Type"] == "application/json"

    client.post(f"/internal/events/{ev['id']}/clip", headers=INTERNAL, files={"clip": ("clip.mp4", MP4, "video/mp4")})
    assert wait_for(lambda: len(receiver.requests) == 2)
    second = receiver.requests[1]
    assert second["headers"]["X-Event-Action"] == "clip_added"
    assert json.loads(second["body"])["clip_url"].endswith("/clip.mp4")
    assert second["headers"]["X-Signature"] == sign(second["body"], "s3cret")

    assert wait_for(lambda: len(deliveries(client, hook["id"])) == 2)
    log = deliveries(client, hook["id"])
    assert [(d["action"], d["attempt"], d["status_code"], d["ok"], d["event_id"]) for d in reversed(log)] == [
        ("opened", 1, 200, True, ev["id"]), ("clip_added", 1, 200, True, ev["id"])]


def test_signature_depends_on_secret_and_body():
    assert sign(b"{}", "a") != sign(b"{}", "b") and sign(b"{}", "a") != sign(b"{ }", "a")
    assert sign(b"{}", "a") == "sha256=" + hmac.new(b"a", b"{}", hashlib.sha256).hexdigest()


def test_close_is_sent_only_when_asked(client, camera, receiver):
    add_hook(client, receiver.url)
    with_closed = add_hook(client, receiver.url + "?closed=1", send_closed=True)
    ev = post_event(client, camera["id"], files=False)
    assert wait_for(lambda: len(receiver.requests) == 2)
    client.patch(f"/internal/events/{ev['id']}", headers=INTERNAL, json={"status": "closed", "ended_at": "2026-10-04T12:40:00Z"})
    assert wait_for(lambda: len(receiver.requests) == 3)
    time.sleep(0.2)
    assert len(receiver.requests) == 3
    last = receiver.requests[-1]
    assert last["path"].endswith("?closed=1") and last["headers"]["X-Event-Action"] == "closed"
    assert json.loads(last["body"])["status"] == "closed"
    client.patch(f"/internal/events/{ev['id']}", headers=INTERNAL, json={"status": "closed"})
    time.sleep(0.2)
    assert len(receiver.requests) == 3 and len(deliveries(client, with_closed["id"])) == 2


def test_filters_by_type_severity_and_enabled(client, camera, receiver):
    add_hook(client, receiver.url + "?only=fire", event_types=["fire"])
    add_hook(client, receiver.url + "?min=high", min_severity="high")
    add_hook(client, receiver.url + "?min=medium", min_severity="medium")
    add_hook(client, receiver.url + "?off=1", enabled=False)

    post_event(client, camera["id"], files=False, type="no_vest", severity="low")
    time.sleep(0.3)
    assert receiver.requests == []

    post_event(client, camera["id"], files=False, type="no_helmet", severity="medium")
    assert wait_for(lambda: len(receiver.requests) == 1)
    assert receiver.requests[0]["path"].endswith("?min=medium")

    post_event(client, camera["id"], files=False, type="fire", severity="high", track_id=None)
    assert wait_for(lambda: len(receiver.requests) == 4)
    assert sorted(r["path"].split("?")[1] for r in receiver.requests[1:]) == ["min=high", "min=medium", "only=fire"]


def test_retries_up_to_three_times_and_logs_each_attempt(client, camera, receiver):
    hook = add_hook(client, receiver.url)
    receiver.statuses = [500, 503]
    ev = post_event(client, camera["id"], files=False)
    assert wait_for(lambda: len(receiver.requests) == 3)
    assert wait_for(lambda: len(deliveries(client, hook["id"])) == 3)
    log = list(reversed(deliveries(client, hook["id"])))
    assert [(d["attempt"], d["status_code"], d["ok"], d["error"]) for d in log] == [
        (1, 500, False, "HTTP 500"), (2, 503, False, "HTTP 503"), (3, 200, True, None)]
    assert all(d["event_id"] == ev["id"] for d in log)
    assert len({r["body"] for r in receiver.requests}) == 1
    assert len({r["headers"]["X-Signature"] for r in receiver.requests}) == 1

    receiver.statuses = [500] * 10
    post_event(client, camera["id"], files=False)
    assert wait_for(lambda: len(deliveries(client, hook["id"])) == 7)
    time.sleep(0.3)
    log = deliveries(client, hook["id"])
    assert len(log) == 7 and len(receiver.requests) == 7
    assert [d["attempt"] for d in log[:4]] == [4, 3, 2, 1] and not any(d["ok"] for d in log[:4])


def test_unreachable_webhook_is_logged_and_does_not_block_events(client, camera):
    hook = add_hook(client, "http://127.0.0.1:9/nobody-listens")
    started = time.monotonic()
    ids = [post_event(client, camera["id"], files=False)["id"] for _ in range(5)]
    assert time.monotonic() - started < 2.0
    assert client.get("/api/events").json()["total"] == 5 and len(set(ids)) == 5
    assert wait_for(lambda: len(deliveries(client, hook["id"])) >= 5, timeout=20)
    d = deliveries(client, hook["id"])[0]
    assert d["ok"] is False and d["status_code"] is None and d["error"]


def test_slow_webhook_does_not_block_event_intake(client, camera, receiver):
    add_hook(client, receiver.url)
    receiver.delay = 0.5
    started = time.monotonic()
    for _ in range(4):
        post_event(client, camera["id"], files=False)
    assert time.monotonic() - started < 1.0


def test_test_endpoint_sends_sample_once(client, receiver):
    hook = add_hook(client, receiver.url, enabled=False, event_types=["fire"])
    r = client.post(f"/api/webhooks/{hook['id']}/test")
    assert r.status_code == 200
    d = r.json()
    assert (d["ok"], d["status_code"], d["action"], d["attempt"], d["event_id"]) == (True, 200, "test", 1, "evt_TEST000000")
    req = receiver.requests[0]
    assert req["headers"]["X-Event-Action"] == "test" and req["headers"]["X-Signature"] == sign(req["body"], "s3cret")
    assert json.loads(req["body"])["details"] == {"test": True}

    receiver.statuses = [404]
    d = client.post(f"/api/webhooks/{hook['id']}/test").json()
    assert (d["ok"], d["status_code"], d["error"]) == (False, 404, "HTTP 404")
    assert len(receiver.requests) == 2
    assert len(deliveries(client, hook["id"])) == 2
