from backend.tests.conftest import INTERNAL, event_body

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
