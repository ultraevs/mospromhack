import json
import shutil
import socket
import threading
import time

import cv2
import numpy as np
import pytest
import uvicorn
from websockets.sync.client import connect

from backend.app.main import create_app
from backend.app.services.webhooks import sign
from backend.tests.test_evidence_webhooks import Receiver
from shared.config import ROOT, AppConfig, Settings
from worker.app.capture import CameraReader
from worker.app.client import BackendClient, DetectionsSender
from worker.app.detector.fake import FakeBox, FakeDetector
from worker.app.main import Worker
from worker.app.model_spec import load_model_spec
from worker.tests.conftest import write_model_yaml
from worker.tests.test_pipeline import THRESHOLDS

TOKEN = "e2e-token"


class LoopingFile:

    def __init__(self, path, fps=20):
        self.cap = cv2.VideoCapture(str(path))
        self.delay = 1 / fps

    def read(self):
        time.sleep(self.delay)
        ok, frame = self.cap.read()
        if not ok:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = self.cap.read()
        return ok, frame

    def release(self):
        self.cap.release()


def wait_for(cond, timeout=20.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = cond()
        if value:
            return value
        time.sleep(0.05)
    return None


@pytest.fixture
def video(tmp_path):
    path = tmp_path / "videos" / "input.avi"
    path.parent.mkdir()
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 20, (640, 360))
    assert writer.isOpened()
    for i in range(40):
        img = np.full((360, 640, 3), 60, np.uint8)
        cv2.circle(img, (100 + i * 8, 180), 30, (200, 200, 200), -1)
        writer.write(img)
    writer.release()
    return path


@pytest.fixture
def backend(tmp_path, video):
    settings = Settings(_env_file=None, internal_token=TOKEN, telegram_bot_token="", data_dir=tmp_path,
                        config_path=ROOT / "config.yaml")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(settings, run_sources=False, webhook_retry_pauses=(0.05,)),
                                           host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    assert wait_for(lambda: server.started, timeout=10)
    yield f"http://127.0.0.1:{port}", settings
    server.should_exit = True
    thread.join(timeout=10)


def test_video_to_event_to_websocket_to_webhook(backend, video, tmp_path):
    import httpx

    base, settings = backend
    http = httpx.Client(base_url=base, timeout=5)
    receiver = Receiver()
    worker = None
    try:
        camera = http.post("/api/cameras", json={"name": "Цех 2, ворота", "source_type": "file", "source": video.name,
                                                 "required_ppe": ["helmet"]}).json()
        zone = http.post(f"/api/cameras/{camera['id']}/zones", json={
            "name": "Зона резки", "type": "ppe_required", "polygon": [[0, 0], [1, 0], [1, 1], [0, 1]],
            "required_ppe": ["helmet"]}).json()
        hook = http.post("/api/webhooks", json={"url": receiver.url, "secret": "e2e-secret"}).json()

        spec = load_model_spec(write_model_yaml(tmp_path / "models" / "ppe", "ppe", THRESHOLDS))
        scenario = {"ppe": [[FakeBox("person", (0.3, 0.3, 0.2, 0.4), 0.9), FakeBox("no_helmet", (0.36, 0.31, 0.07, 0.07), 0.7)]]}
        config = AppConfig()
        config.evidence.pre_seconds, config.evidence.post_seconds = 1, 1
        worker = Worker(config, {"ppe": spec}, FakeDetector({"ppe": spec}, scenario),
                        BackendClient(base, TOKEN), DetectionsSender(base, TOKEN).start(),
                        reader_factory=lambda cam_id, url: CameraReader(cam_id, url, open_fn=lambda _u: LoopingFile(video)))

        ws_base = base.replace("http://", "ws://")
        with connect(f"{ws_base}/ws/events") as events_ws, connect(f"{ws_base}/ws/detections?camera_id={camera['id']}") as det_ws:
            thread = threading.Thread(target=worker.run, daemon=True)
            thread.start()

            det = json.loads(det_ws.recv(timeout=15))
            assert det["type"] == "detections" and det["camera_id"] == camera["id"] and det["frame"] == {"w": 640, "h": 360}
            assert len(det["people"]) == 1 and det["objects"][0]["cls"] == "no_helmet"

            msg = json.loads(events_ws.recv(timeout=20))
            assert msg["type"] == "event_created"
            event = msg["event"]
            assert (event["type"], event["severity"], event["status"]) == ("no_helmet", "medium", "open")
            assert event["camera_name"] == "Цех 2, ворота" and event["track_id"] == det["people"][0]["track_id"]

            stored = http.get(f"/api/events/{event['id']}").json()
            assert stored["snapshot_url"] and stored["snapshot_clean_url"] and stored["details"]["observations"] >= 8
            for url in (stored["snapshot_url"], stored["snapshot_clean_url"]):
                img = cv2.imdecode(np.frombuffer(http.get(url).content, np.uint8), cv2.IMREAD_COLOR)
                assert img is not None and img.shape == (360, 640, 3)
            assert (settings.data_dir / "media" / "events" / event["id"] / "snapshot.jpg").is_file()
            assert http.get("/api/events").json()["total"] == 1
            assert stored["zone_id"] is None and zone["id"] > 0

            assert wait_for(lambda: receiver.requests)
            req = receiver.requests[0]
            assert req["headers"]["X-Signature"] == sign(req["body"], "e2e-secret")
            assert req["headers"]["X-Event-Type"] == "no_helmet" and req["headers"]["X-Event-Action"] == "opened"
            assert json.loads(req["body"])["id"] == event["id"]
            deliveries = wait_for(lambda: http.get(f"/api/webhooks/{hook['id']}/deliveries").json())
            assert deliveries[-1]["ok"] is True and deliveries[-1]["event_id"] == event["id"]

            if shutil.which("ffmpeg"):
                updated = wait_for(lambda: (e := http.get(f"/api/events/{event['id']}").json())["clip_url"] and e, timeout=30)
                assert updated, "клип не появился за 30 секунд"
                clip = http.get(updated["clip_url"])
                assert clip.status_code == 200 and clip.content[4:8] == b"ftyp"
                assert wait_for(lambda: any(r["headers"]["X-Event-Action"] == "clip_added" for r in receiver.requests))
                update = json.loads(events_ws.recv(timeout=10))
                assert update["type"] == "event_updated" and update["event"]["clip_url"] == updated["clip_url"]

            health = http.get("/api/health").json()
            assert health["worker"] == {"ok": True, "detector": "fake", "device": "fake"}
            assert http.get(f"/api/cameras/{camera['id']}").json()["status"] == "online"

        worker.stop()
        thread.join(timeout=10)
    finally:
        if worker:
            worker.stop()
            worker.shutdown()
        receiver.close()
        http.close()
