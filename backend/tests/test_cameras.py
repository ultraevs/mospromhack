from backend.tests.conftest import INTERNAL


def version(client) -> int:
    return client.get("/internal/config", headers=INTERNAL).json()["version"]


def test_camera_crud(client):
    assert client.get("/api/cameras").json() == []

    r = client.post("/api/cameras", json={"name": "Склад", "source_type": "file", "source": "demo.mp4",
                                          "required_ppe": ["helmet", "vest"]})
    assert r.status_code == 201
    cam = r.json()
    assert cam == {"id": cam["id"], "name": "Склад", "source_type": "file", "source": "demo.mp4",
                   "required_ppe": ["helmet", "vest"], "enabled": True, "status": "offline", "open_events": 0}

    assert client.get(f"/api/cameras/{cam['id']}").json() == cam
    assert [c["id"] for c in client.get("/api/cameras").json()] == [cam["id"]]

    r = client.patch(f"/api/cameras/{cam['id']}", json={"name": "Склад 2", "required_ppe": []})
    assert r.status_code == 200
    assert r.json()["name"] == "Склад 2" and r.json()["required_ppe"] == [] and r.json()["source"] == "demo.mp4"

    assert client.delete(f"/api/cameras/{cam['id']}").status_code == 204
    assert client.get(f"/api/cameras/{cam['id']}").status_code == 404
    assert client.patch(f"/api/cameras/{cam['id']}", json={"name": "x"}).status_code == 404
    assert client.delete(f"/api/cameras/{cam['id']}").status_code == 404


def test_source_validation(client):
    bad = [
        {"name": "a", "source_type": "file", "source": "missing.mp4"},
        {"name": "a", "source_type": "file", "source": "../db.sqlite"},
        {"name": "a", "source_type": "rtsp", "source": "http://example.com/stream"},
        {"name": "a", "source_type": "usb", "source": "0"},
        {"name": "a", "source_type": "file", "source": "demo.mp4", "required_ppe": ["gloves"]},
    ]
    for body in bad:
        assert client.post("/api/cameras", json=body).status_code == 422, body
    assert client.post("/api/cameras", json={"name": "a", "source_type": "rtsp",
                                             "source": "rtsp://10.0.0.5:554/stream1"}).status_code == 201


def test_patch_validates_new_source(client, camera):
    assert client.patch(f"/api/cameras/{camera['id']}", json={"source": "missing.mp4"}).status_code == 422
    assert client.patch(f"/api/cameras/{camera['id']}",
                        json={"source_type": "rtsp", "source": "rtsp://cam/1"}).status_code == 200


def test_videos_lists_only_video_files(client, settings):
    (settings.data_dir / "videos" / "notes.txt").write_text("x")
    (settings.data_dir / "videos" / "b.MKV").write_bytes(b"x")
    assert [v["name"] for v in client.get("/api/videos").json()] == ["b.MKV", "demo.mp4"]


def test_internal_config_requires_token(client):
    assert client.get("/internal/config").status_code == 401
    assert client.get("/internal/config", headers={"X-Internal-Token": "wrong"}).status_code == 401
    assert client.get("/internal/config", headers=INTERNAL).status_code == 200


def test_internal_config_lists_enabled_cameras_and_bumps_version(client):
    v0 = version(client)
    a = client.post("/api/cameras", json={"name": "A", "source_type": "file", "source": "demo.mp4",
                                          "required_ppe": ["helmet"]}).json()
    b = client.post("/api/cameras", json={"name": "B", "source_type": "file", "source": "demo.mp4",
                                          "enabled": False}).json()
    cfg = client.get("/internal/config", headers=INTERNAL).json()
    assert cfg["version"] == v0 + 2
    assert [(c["id"], c["rtsp_url"], c["required_ppe"]) for c in cfg["cameras"]] == [
        (a["id"], f"rtsp://127.0.0.1:8554/cam-{a['id']}", ["helmet"])]
    assert cfg["rules"]["no_helmet"] == {"window_s": 4, "min_observations": 12, "fraction": 0.6}
    assert cfg["rules"]["no_vest"]["min_person_height_px"] == 120

    client.patch(f"/api/cameras/{b['id']}", json={"enabled": True})
    assert version(client) == v0 + 3
    client.delete(f"/api/cameras/{a['id']}")
    cfg = client.get("/internal/config", headers=INTERNAL).json()
    assert cfg["version"] == v0 + 4 and [c["id"] for c in cfg["cameras"]] == [b["id"]]


def test_camera_status_comes_from_worker_stats(client, camera):
    assert client.get(f"/api/cameras/{camera['id']}").json()["status"] == "offline"
    client.post("/internal/stats", headers=INTERNAL,
                json={"detector": "fake", "device": "fake", "cameras": {str(camera["id"]): {"online": True}}})
    assert client.get(f"/api/cameras/{camera['id']}").json()["status"] == "online"


def test_health_reports_problems(client, camera):
    h = client.get("/api/health").json()
    assert h["status"] == "degraded" and h["worker"]["ok"] is False
    assert "воркер не отвечает" in h["problems"]
    assert any("MediaMTX" in p or "проверялся" in p for p in h["problems"])

    client.post("/internal/stats", headers=INTERNAL, json={
        "detector": "onnx", "device": "cpu", "warnings": ["ИНФЕРЕНС ИДЁТ НА CPU"],
        "cameras": {str(camera["id"]): {"online": False, "error": "нет потока"}}})
    h = client.get("/api/health").json()
    assert h["worker"] == {"ok": True, "detector": "onnx", "device": "cpu"}
    assert "ИНФЕРЕНС ИДЁТ НА CPU" in h["problems"]
    assert any("нет потока" in p for p in h["problems"])


def test_client_config(client):
    assert client.get("/api/client-config").json() == {"video": "webrtc", "webrtc_port": 8889, "hls_port": 8888,
                                                         "overlay_delay_ms": 0}
