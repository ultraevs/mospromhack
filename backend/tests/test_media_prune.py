import json

from backend.app.services.media import KEEP_EVENTS
from backend.tests.conftest import INTERNAL, event_body

JPEG = b"\xff\xd8\xff\xe0" + b"snapshot-bytes" * 10
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"clip" * 50


def post(client, camera_id, minute):
    body = event_body(camera_id, started_at=f"2026-10-04T12:{minute:02d}:05.120Z")
    r = client.post("/internal/events", headers=INTERNAL, data={"event": json.dumps(body)},
                    files={"snapshot": ("s.jpg", JPEG, "image/jpeg"), "clean": ("c.jpg", JPEG, "image/jpeg")})
    assert r.status_code == 201, r.text
    return r.json()


def folder(settings, ev):
    return settings.data_dir / "media" / "events" / ev["id"]


def test_only_newest_events_keep_media(client, camera, settings):
    extra = 3
    evs = [post(client, camera["id"], m) for m in range(KEEP_EVENTS + extra)]
    old, new = evs[:extra], evs[extra:]
    for ev in old:
        assert not folder(settings, ev).exists()
        got = client.get(f"/api/events/{ev['id']}").json()
        assert got["snapshot_url"] is None and got["snapshot_clean_url"] is None and got["clip_url"] is None
    for ev in new:
        assert (folder(settings, ev) / "snapshot.jpg").is_file()
        got = client.get(f"/api/events/{ev['id']}").json()
        assert got["snapshot_url"] == f"/media/events/{ev['id']}/snapshot.jpg"
        assert got["snapshot_clean_url"] == f"/media/events/{ev['id']}/clean.jpg"


def test_exactly_keep_events_prunes_nothing(client, camera, settings):
    evs = [post(client, camera["id"], m) for m in range(KEEP_EVENTS)]
    assert all(folder(settings, e).exists() for e in evs)


def test_clip_upload_also_prunes(client, camera, settings):
    evs = [post(client, camera["id"], m) for m in range(KEEP_EVENTS)]
    # клип новому событию - старые остаются без медиа
    newest = post(client, camera["id"], KEEP_EVENTS)
    assert not folder(settings, evs[0]).exists()
    r = client.post(f"/internal/events/{newest['id']}/clip", headers=INTERNAL, files={"clip": ("c.mp4", MP4, "video/mp4")})
    assert r.status_code == 200 and r.json()["clip_url"].endswith("clip.mp4")
    assert (folder(settings, newest) / "clip.mp4").is_file()
