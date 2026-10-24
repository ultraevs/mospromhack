import json
import re

from backend.tests.conftest import INTERNAL, event_body


def post_event(client, camera_id, **over) -> dict:
    r = client.post("/internal/events", headers=INTERNAL, json=event_body(camera_id, **over))
    assert r.status_code == 201, r.text
    return r.json()


def test_worker_event_is_stored_in_contract_shape(client, camera):
    assert client.post("/internal/events", json=event_body(camera["id"])).status_code == 401

    ev = post_event(client, camera["id"])
    assert re.fullmatch(r"evt_[0-9A-Z]{10}", ev["id"])
    assert ev == {
        "id": ev["id"], "type": "no_helmet", "severity": "medium", "status": "open",
        "camera_id": camera["id"], "camera_name": "Цех 2, ворота", "zone_id": None, "zone_name": None,
        "track_id": 17, "started_at": "2026-10-04T12:31:05.120000Z", "ended_at": None, "confidence": 0.62,
        "bbox": [0.41, 0.22, 0.08, 0.31], "snapshot_url": None, "snapshot_clean_url": None, "clip_url": None,
        "review": "none", "details": {"fraction": 0.58, "window_s": 4, "observations": 19},
        "clip_anon_url": None, "reviewed_at": None, "reviewed_by": None, "review_note": None,
    }
    assert client.get(f"/api/events/{ev['id']}").json() == ev
    assert client.get("/api/cameras").json()[0]["open_events"] == 1


def test_event_ids_are_unique(client, camera):
    ids = {post_event(client, camera["id"])["id"] for _ in range(50)}
    assert len(ids) == 50


def test_invalid_event_is_rejected(client, camera):
    for over in ({"type": "no_gloves"}, {"severity": "critical"}, {"bbox": [0.1, 0.2, 0.3]}):
        assert client.post("/internal/events", headers=INTERNAL,
                           json=event_body(camera["id"], **over)).status_code == 422


def test_worker_closes_event(client, camera):
    ev = post_event(client, camera["id"])
    r = client.patch(f"/internal/events/{ev['id']}", headers=INTERNAL,
                     json={"status": "closed", "ended_at": "2026-10-04T12:31:20Z"})
    assert r.status_code == 200
    assert r.json()["status"] == "closed" and r.json()["ended_at"] == "2026-10-04T12:31:20Z"
    assert client.get("/api/cameras").json()[0]["open_events"] == 0
    assert client.patch("/internal/events/evt_nope", headers=INTERNAL, json={"status": "closed"}).status_code == 404
    assert client.patch(f"/internal/events/{ev['id']}", json={"status": "closed"}).status_code == 401


def test_review(client, camera):
    ev = post_event(client, camera["id"])
    r = client.patch(f"/api/events/{ev['id']}", json={"review": "false_positive"})
    assert r.status_code == 200 and r.json()["review"] == "false_positive"
    assert client.patch(f"/api/events/{ev['id']}", json={"review": "maybe"}).status_code == 422
    assert client.patch("/api/events/evt_nope", json={"review": "confirmed"}).status_code == 404


def test_review_forwarded(client, camera):
    ev = post_event(client, camera["id"])
    r = client.patch(f"/api/events/{ev['id']}", json={"review": "forwarded"})
    assert r.status_code == 200 and r.json()["review"] == "forwarded"
    assert [e["id"] for e in client.get("/api/events", params={"review": "forwarded"}).json()["items"]] == [ev["id"]]
    s = client.get("/api/analytics/summary").json()
    assert s["forwarded"] == 1 and s["confirmed"] == 0 and s["unreviewed"] == 0


def test_journal_zone_filter(client, camera):
    zone = client.post(f"/api/cameras/{camera['id']}/zones", json={
        "name": "Z", "type": "restricted", "polygon": [[0, 0], [1, 0], [1, 1]], "required_ppe": []}).json()
    a = post_event(client, camera["id"], zone_id=zone["id"])
    post_event(client, camera["id"])
    items = client.get("/api/events", params={"zone_id": zone["id"]}).json()["items"]
    assert [e["id"] for e in items] == [a["id"]] and items[0]["zone_name"] == "Z"


def test_journal_filters_and_pagination(client, camera):
    other = client.post("/api/cameras", json={"name": "B", "source_type": "file", "source": "demo.mp4"}).json()
    a = post_event(client, camera["id"], started_at="2026-10-04T10:00:00Z")
    b = post_event(client, camera["id"], type="fire", severity="high", track_id=None, started_at="2026-10-04T11:00:00Z")
    c = post_event(client, other["id"], type="no_vest", severity="low", started_at="2026-10-04T12:00:00Z")
    client.patch(f"/internal/events/{a['id']}", headers=INTERNAL, json={"status": "closed"})
    client.patch(f"/api/events/{b['id']}", json={"review": "confirmed"})

    def ids(**params):
        r = client.get("/api/events", params=params)
        assert r.status_code == 200, r.text
        return [e["id"] for e in r.json()["items"]]

    assert ids() == [c["id"], b["id"], a["id"]]
    assert ids(camera_id=camera["id"]) == [b["id"], a["id"]]
    assert ids(type="fire") == [b["id"]]
    assert ids(severity="low") == [c["id"]]
    assert ids(status="closed") == [a["id"]]
    assert ids(status="open") == [c["id"], b["id"]]
    assert ids(review="confirmed") == [b["id"]]
    assert ids(review="none") == [c["id"], a["id"]]
    assert ids(**{"from": "2026-10-04T10:30:00Z"}) == [c["id"], b["id"]]
    assert ids(to="2026-10-04T11:00:00Z") == [b["id"], a["id"]]
    assert ids(**{"from": "2026-10-04T10:30:00Z", "to": "2026-10-04T11:30:00Z"}) == [b["id"]]
    assert ids(camera_id=camera["id"], type="no_vest") == []

    page = client.get("/api/events", params={"limit": 2, "offset": 1}).json()
    assert [e["id"] for e in page["items"]] == [b["id"], a["id"]]
    assert (page["total"], page["limit"], page["offset"]) == (3, 2, 1)
    assert client.get("/api/events", params={"type": "bogus"}).status_code == 422


def test_ws_events_gets_created_and_updated(client, camera):
    with client.websocket_connect("/ws/events") as ws:
        ev = post_event(client, camera["id"])
        msg = ws.receive_json()
        assert msg["type"] == "event_created" and msg["event"] == ev

        client.patch(f"/internal/events/{ev['id']}", headers=INTERNAL, json={"status": "closed"})
        msg = ws.receive_json()
        assert msg["type"] == "event_updated" and msg["event"]["status"] == "closed"

        client.patch(f"/api/events/{ev['id']}", json={"review": "confirmed"})
        assert ws.receive_json()["event"]["review"] == "confirmed"


def test_detections_are_relayed_to_the_right_camera(client):
    frame = {"type": "detections", "camera_id": 1, "ts": 1759581065120, "frame": {"w": 1920, "h": 1080},
             "people": [{"track_id": 17, "bbox": [0.41, 0.22, 0.08, 0.31], "conf": 0.83, "ppe": {"helmet": "violation"},
                         "zones": [], "violations": ["no_helmet"]}], "objects": []}
    with client.websocket_connect("/ws/detections?camera_id=1") as cam1, \
            client.websocket_connect("/ws/detections?camera_id=2") as cam2, \
            client.websocket_connect("/internal/detections", headers=INTERNAL) as worker:
        worker.send_text(json.dumps({**frame, "camera_id": 2, "ts": 1}))
        worker.send_text("{not json")
        worker.send_text(json.dumps(frame))
        assert cam1.receive_json() == frame
        assert cam2.receive_json()["ts"] == 1


def test_internal_detections_requires_token(client):
    import pytest
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect("/internal/detections"):
            pass
    assert e.value.code == 4401


def test_events_left_open_are_closed_when_worker_restarts(client, camera):
    stats = {"detector": "fake", "device": "fake", "cameras": {}}
    client.post("/internal/stats", headers=INTERNAL, json={**stats, "session": "run-1"})
    ev = post_event(client, camera["id"])
    client.post("/internal/stats", headers=INTERNAL, json={**stats, "session": "run-1"})
    assert client.get(f"/api/events/{ev['id']}").json()["status"] == "open"

    with client.websocket_connect("/ws/events") as ws:
        client.post("/internal/stats", headers=INTERNAL, json={**stats, "session": "run-2"})
        msg = ws.receive_json()
    assert msg["type"] == "event_updated" and msg["event"]["id"] == ev["id"]
    closed = client.get(f"/api/events/{ev['id']}").json()
    assert closed["status"] == "closed" and closed["ended_at"] is not None
    assert closed["details"]["closed_by"] == "worker_restart"

    fresh = post_event(client, camera["id"])
    client.post("/internal/stats", headers=INTERNAL, json={**stats, "session": "run-2"})
    assert client.get(f"/api/events/{fresh['id']}").json()["status"] == "open"


def test_backend_restart_does_not_close_events_of_running_worker(settings):
    from fastapi.testclient import TestClient

    from backend.app.main import create_app

    stats = {"detector": "fake", "device": "fake", "cameras": {}, "session": "run-1"}
    with TestClient(create_app(settings, run_sources=False)) as c:
        cam = c.post("/api/cameras", json={"name": "A", "source_type": "file", "source": "demo.mp4"}).json()
        c.post("/internal/stats", headers=INTERNAL, json=stats)
        ev = post_event(c, cam["id"])
    with TestClient(create_app(settings, run_sources=False)) as c:
        c.post("/internal/stats", headers=INTERNAL, json=stats)
        assert c.get(f"/api/events/{ev['id']}").json()["status"] == "open"


def test_stats_report_capture_to_event_latency(client, camera):
    import time

    assert client.get("/api/stats").json()["latency"] == {"capture_to_event": None, "capture_to_telegram": None}
    now = int(time.time() * 1000)
    for age in (100, 200, 300):
        post_event(client, camera["id"], captured_ts=now - age)
    post_event(client, camera["id"])
    lat = client.get("/api/stats").json()["latency"]["capture_to_event"]
    assert lat["events"] == 3 and 190 <= lat["avg_ms"] <= 400 and lat["p95_ms"] >= 290


def test_stats_merge_worker_report(client):
    assert client.get("/api/stats").json()["worker_alive"] is False
    client.post("/internal/stats", headers=INTERNAL, json={
        "detector": "onnx", "device": "cuda", "streams": 4, "crops_per_s": 2.4,
        "inference": {"ppe": {"avg_ms": 27.1, "p95_ms": 31.0, "batch": 4}},
        "cameras": {"1": {"online": True, "read_fps": 25.0, "proc_fps": 6.0}}})
    s = client.get("/api/stats").json()
    assert s["worker_alive"] is True and s["device"] == "cuda" and s["streams"] == 4
    assert s["inference"]["ppe"]["batch"] == 4 and s["cameras"]["1"]["proc_fps"] == 6.0
    assert s["subscribers"] == {"detections": 0, "events": 0}


def test_export_csv_and_xlsx(client, camera):
    import io

    from openpyxl import load_workbook

    a = post_event(client, camera["id"])
    post_event(client, camera["id"], type="fire", severity="high")
    client.patch(f"/internal/events/{a['id']}", headers=INTERNAL, json={"status": "closed", "ended_at": "2026-10-04T12:32:10Z"})

    r = client.get("/api/events/export?format=csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert re.search(r'filename="events_\d{4}-\d{2}-\d{2}\.csv"', r.headers["content-disposition"])
    assert r.content.startswith(b"\xef\xbb\xbf")
    lines = r.content.decode("utf-8-sig").strip().splitlines()
    assert len(lines) == 3 and lines[0].startswith("ID;Начало")
    assert "Разобрал;Когда разобрано;Реакция, мин;Комментарий" in lines[0]
    row = next(l for l in lines if a["id"] in l).split(";")
    assert row[3] == "0:01:04" and row[6] == "Без каски" and row[8] == "Закрыто"

    r = client.get("/api/events/export?format=xlsx&type=fire")
    assert r.status_code == 200 and ".xlsx" in r.headers["content-disposition"]
    rows = list(load_workbook(io.BytesIO(r.content)).active.values)
    assert len(rows) == 2 and rows[1][6] == "Огонь" and rows[1][7] == "Высокая"

    assert client.get("/api/events/export?format=pdf").status_code == 422


def test_shift_report_html(client, camera):
    post_event(client, camera["id"])
    b = post_event(client, camera["id"], type="fire", severity="high")
    client.patch(f"/api/events/{b['id']}", json={"review": "false_positive"})
    r = client.get(f"/api/events/report?from=2026-10-04T00:00:00Z&camera_id={camera['id']}")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    t = r.text
    assert "Отчёт по нарушениям за смену" in t and "Ложных исключено" in t and "Подпись ответственного" in t
    assert "Без каски" in t and "Ложное" in t
    assert "<b>1</b>Нарушений" in t
    # без периода - последние 12 часов, старые события не попадают
    assert "Нарушений за период нет" in client.get("/api/events/report").text


def test_review_records_who_and_when(client, camera):
    ev = post_event(client, camera["id"])
    r = client.patch(f"/api/events/{ev['id']}", json={"review": "confirmed", "note": "  мастер предупредил  "})
    got = r.json()
    assert got["reviewed_by"] == "Тестовый администратор" and got["reviewed_at"] and got["review_note"] == "мастер предупредил"
    lines = client.get("/api/events/export?format=csv").content.decode("utf-8-sig").strip().splitlines()
    assert "Тестовый администратор" in lines[1] and "мастер предупредил" in lines[1]
    assert "мастер предупредил" in client.get("/api/events/report?from=2026-10-04T00:00:00Z").text

    got = client.patch(f"/api/events/{ev['id']}", json={"review": "none"}).json()
    assert got["reviewed_by"] is None and got["reviewed_at"] is None and got["review_note"] is None


def test_dataset_export(client, camera):
    import io
    import zipfile

    jpeg = b"\xff\xd8\xff\xe0" + b"x" * 50

    def post(**over):
        r = client.post("/internal/events", headers=INTERNAL, data={"event": json.dumps(event_body(camera["id"], **over))},
                        files={"snapshot": ("s.jpg", jpeg, "image/jpeg"), "clean": ("c.jpg", jpeg, "image/jpeg")})
        assert r.status_code == 201, r.text
        return r.json()

    good, bad, skip = post(), post(type="fire"), post()
    client.patch(f"/api/events/{good['id']}", json={"review": "confirmed"})
    client.patch(f"/api/events/{bad['id']}", json={"review": "false_positive"})
    r = client.get("/api/events/export/dataset")
    assert r.status_code == 200 and re.search(r'filename="dataset_\d{4}-\d{2}-\d{2}\.zip"', r.headers["content-disposition"])
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = set(z.namelist())
    assert {f"images/{good['id']}.jpg", f"labels/{good['id']}.txt", f"negatives/{bad['id']}.jpg",
            "data.yaml", "README.txt"} <= names
    assert not any(skip["id"] in n for n in names)
    assert z.read(f"labels/{good['id']}.txt").decode() == "0 0.450000 0.375000 0.080000 0.310000\n"
    assert z.read("data.yaml").decode().startswith("names:\n  - no_helmet\n")
