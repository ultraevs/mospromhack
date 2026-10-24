import time
from datetime import datetime

from backend.app.db.models import EventRow
from backend.tests.conftest import INTERNAL, event_body
from backend.tests.test_auth import admin, app, viewer  # noqa: F401

SQUARE = [[0, 0], [1, 0], [1, 1], [0, 1]]


def post(client, camera_id, **over) -> dict:
    r = client.post("/internal/events", headers=INTERNAL, json=event_body(camera_id, **over))
    assert r.status_code == 201, r.text
    return r.json()


def review(client, event, value):
    assert client.patch(f"/api/events/{event['id']}", json={"review": value}).status_code == 200


def by(items, key):
    return {i[key]: i for i in items}


def test_empty_summary(client):
    s = client.get("/api/analytics/summary").json()
    assert s["total"] == 0 and s["false_share"] is None and s["by_camera"] == [] and s["by_zone"] == [] and s["by_day"] == []
    assert [t["type"] for t in s["by_type"]] == ["no_helmet", "no_mask", "no_vest", "zone_intrusion", "smoking", "fire", "smoke",
                                                 "running", "vehicle_proximity", "fall", "lying_immobile",
                                                 "phone_usage", "barrier_crossing", "under_load", "no_harness",
                                                 "unsafe_carry"]
    assert all(t["total"] == 0 for t in s["by_type"]) and len(s["by_hour"]) == 24


def test_summary_counts(client, camera):
    other = client.post("/api/cameras", json={"name": "Склад", "source_type": "file", "source": "demo.mp4"}).json()
    zone = client.post(f"/api/cameras/{camera['id']}/zones", json={"name": "Резка", "type": "restricted", "polygon": SQUARE}).json()

    a = post(client, camera["id"], started_at="2026-10-04T08:10:00Z")
    b = post(client, camera["id"], started_at="2026-10-04T08:50:00Z")
    c = post(client, camera["id"], started_at="2026-10-04T23:30:00Z")
    post(client, camera["id"], type="zone_intrusion", severity="high", zone_id=zone["id"], started_at="2026-10-05T09:00:00Z")
    post(client, other["id"], type="fire", severity="high", track_id=None, started_at="2026-10-05T09:20:00Z")
    review(client, a, "confirmed")
    review(client, b, "false_positive")
    review(client, c, "false_positive")

    s = client.get("/api/analytics/summary").json()
    assert (s["total"], s["confirmed"], s["false_positive"], s["unreviewed"]) == (5, 1, 2, 2)
    assert s["false_share"] == 0.667
    types = by(s["by_type"], "type")
    assert types["no_helmet"] == {"type": "no_helmet", "total": 3, "confirmed": 1, "false_positive": 2, "unreviewed": 0, "forwarded": 0,
                                  "false_share": 0.667}
    assert types["fire"]["total"] == 1 and types["fire"]["false_share"] is None and types["smoke"]["total"] == 0
    assert {x["severity"]: x["total"] for x in s["by_severity"]} == {"high": 2, "medium": 3, "low": 0}
    assert s["by_camera"] == [{"camera_id": camera["id"], "camera_name": "Цех 2, ворота", "total": 4},
                              {"camera_id": other["id"], "camera_name": "Склад", "total": 1}]
    assert s["by_zone"] == [{"zone_id": zone["id"], "zone_name": "Резка", "camera_name": "Цех 2, ворота", "total": 1}]
    assert s["without_zone"] == 4
    hours = {h["hour"]: h["total"] for h in s["by_hour"]}
    assert (hours[8], hours[9], hours[23], sum(hours.values())) == (2, 2, 1, 5)
    assert s["by_day"] == [{"day": "2026-10-04", "total": 3}, {"day": "2026-10-05", "total": 2}]


def test_summary_period_camera_and_timezone(client, camera):
    other = client.post("/api/cameras", json={"name": "Склад", "source_type": "file", "source": "demo.mp4"}).json()
    post(client, camera["id"], started_at="2026-10-04T08:10:00Z")
    post(client, camera["id"], started_at="2026-10-04T22:30:00Z")
    post(client, other["id"], started_at="2026-10-05T09:00:00Z")

    s = client.get("/api/analytics/summary", params={"from": "2026-10-04T20:00:00Z", "to": "2026-10-05T08:00:00Z"}).json()
    assert s["total"] == 1
    assert client.get("/api/analytics/summary", params={"camera_id": other["id"]}).json()["total"] == 1
    assert client.get("/api/analytics/summary", params={"from": "2026-10-06T00:00:00Z"}).json()["total"] == 0
    s = client.get("/api/analytics/summary", params={"tz_offset_min": 180}).json()
    hours = {h["hour"]: h["total"] for h in s["by_hour"]}
    assert (hours[11], hours[1], hours[12]) == (1, 1, 1)
    assert s["by_day"] == [{"day": "2026-10-04", "total": 1}, {"day": "2026-10-05", "total": 2}]
    assert client.get("/api/analytics/summary", params={"tz_offset_min": 5000}).status_code == 422


def test_shifts_settings(client):
    assert client.get("/api/settings/shifts").json()["shifts"][1] == {"name": "Ночная", "start": "20:00", "end": "08:00"}
    body = {"shifts": [{"name": "A", "start": "07:00", "end": "15:00"}, {"name": "B", "start": "15:00", "end": "23:00"},
                       {"name": "C", "start": "23:00", "end": "07:00"}]}
    assert client.put("/api/settings/shifts", json=body).status_code == 200
    assert client.get("/api/settings/shifts").json() == body
    assert client.put("/api/settings/shifts", json={"shifts": []}).status_code == 422
    assert client.put("/api/settings/shifts", json={"shifts": [{"name": "x", "start": "25:00", "end": "08:00"}]}).status_code == 422
    assert client.put("/api/settings/shifts", json={"shifts": body["shifts"] * 2}).status_code == 422


def test_shifts_admin_only(admin, viewer):
    assert viewer.get("/api/settings/shifts").status_code == 200
    body = {"shifts": [{"name": "Одна", "start": "00:00", "end": "00:00"}]}
    assert viewer.put("/api/settings/shifts", json=body).status_code == 403
    assert admin.put("/api/settings/shifts", json=body).status_code == 200


def test_summary_shifts_and_heatmap(client, camera):
    post(client, camera["id"], started_at="2026-10-05T09:00:00Z")  # пн 12:00 мск
    post(client, camera["id"], started_at="2026-10-05T19:30:00Z")  # пн 22:30
    post(client, camera["id"], type="fire", severity="high", track_id=None, started_at="2026-10-05T23:30:00Z")  # вт 02:30
    s = client.get("/api/analytics/summary", params={"tz_offset_min": 180}).json()
    assert s["by_shift"] == [{"name": "Дневная", "total": 1}, {"name": "Ночная", "total": 2}]
    assert {(x["name"], x["type"]): x["total"] for x in s["by_shift_type"]} == {
        ("Дневная", "no_helmet"): 1, ("Ночная", "no_helmet"): 1, ("Ночная", "fire"): 1}
    heat = s["heatmap"]
    assert len(heat) == 7 and all(len(r) == 24 for r in heat)
    assert (heat[0][12], heat[0][22], heat[1][2], sum(map(sum, heat))) == (1, 1, 1, 3)


def test_summary_reaction(client, camera):
    assert client.get("/api/analytics/summary").json()["reaction"] == {"n": 0, "median_s": None, "p90_s": None, "high_median_s": None}
    a = post(client, camera["id"], started_at="2026-10-05T09:00:00Z")
    b = post(client, camera["id"], started_at="2026-10-05T10:00:00Z")
    c = post(client, camera["id"], type="fire", severity="high", track_id=None, started_at="2026-10-05T11:00:00Z")
    post(client, camera["id"], started_at="2026-10-05T12:00:00Z")
    for e, v in ((a, "confirmed"), (b, "false_positive"), (c, "confirmed")):
        review(client, e, v)
    with client.app.state.app.session_factory() as db:
        for e, at in ((a, "09:01:00"), (b, "10:05:00"), (c, "11:00:30")):
            db.get(EventRow, e["id"]).reviewed_at = datetime.fromisoformat(f"2026-10-05T{at}")
        db.commit()
    r = client.get("/api/analytics/summary").json()["reaction"]
    assert r == {"n": 3, "median_s": 60.0, "p90_s": 300.0, "high_median_s": 30.0}


def test_capacity(client):
    r = client.get("/api/capacity", params={"cameras": 20, "fps": 6, "tiles": True}).json()
    assert r["source"] == "reference" and r["note"] and r["infer_per_s"] == 25
    assert (r["per_accelerator"], r["accelerators"]) == (13, 2)
    assert client.get("/api/capacity", params={"tiles": False}).json()["infer_per_s"] == 19
    state = client.app.state.app
    state.worker_stats = {"detector": "onnx", "inference": {m: {"avg_ms": 4.0, "batch": 2} for m in ("ppe", "people", "heads", "hazard")}}
    state.worker_seen = time.monotonic()
    r = client.get("/api/capacity", params={"cameras": 50}).json()
    assert r["source"] == "measured" and r["detector"] == "onnx" and r["note"] is None
    assert (r["per_accelerator"], r["accelerators"]) == (20, 3)
