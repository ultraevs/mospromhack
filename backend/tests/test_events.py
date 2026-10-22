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
