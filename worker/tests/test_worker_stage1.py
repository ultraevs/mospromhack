import json

import numpy as np
import pytest

from shared.config import AppConfig
from shared.schemas import Event, RuleSettings, WorkerCamera, WorkerConfig
from worker.app.capture import Frame
from worker.app.detector.fake import FakeBox, FakeDetector
from worker.app.main import Worker
from worker.app.model_spec import load_model_spec
from worker.app.rules.simple import SimpleNoHelmet
from worker.app.rules.types import EventClose, EventOpen
from worker.tests.conftest import write_model_yaml

RULES = RuleSettings()
BOX = (0.4, 0.2, 0.05, 0.08)


def feed(rule, start, seconds, fps, hit_every):
    out = []
    for i in range(int(seconds * fps)):
        boxes = [(0.6, BOX)] if hit_every and i % hit_every == 0 else []
        out += rule.update(start + i / fps, boxes, RULES)
    return out


def test_simple_rule_opens_when_fraction_reached():
    rule = SimpleNoHelmet()
    actions = feed(rule, 1000.0, 4, 6, hit_every=2)  # 0.5 при пороге 0.35
    assert len(actions) == 1 and isinstance(actions[0], EventOpen)
    ev = actions[0]
    assert ev.type.value == "no_helmet" and ev.severity.value == "medium" and ev.bbox == BOX
    assert ev.details["observations"] == 8
    assert ev.details["fraction"] == 0.5


def test_simple_rule_does_not_open_below_fraction_or_without_observations():
    assert feed(SimpleNoHelmet(), 1000.0, 10, 6, hit_every=4) == []  # 0.25
    assert feed(SimpleNoHelmet(), 1000.0, 1, 6, hit_every=1) == []  # мало наблюдений


def test_simple_rule_closes_after_quiet_period_and_respects_cooldown():
    rule = SimpleNoHelmet()
    opened = feed(rule, 1000.0, 4, 6, hit_every=1)
    assert len(opened) == 1
    quiet = feed(rule, 1004.0, 10, 6, hit_every=0)
    assert len(quiet) == 1 and isinstance(quiet[0], EventClose) and quiet[0].key == opened[0].key
    assert 1011.0 <= quiet[0].t <= 1012.5
    assert feed(rule, 1014.0, 30, 6, hit_every=1) == []
    again = feed(rule, 1080.0, 4, 6, hit_every=1)
    assert len(again) == 1 and isinstance(again[0], EventOpen) and again[0].key != opened[0].key


class FakeReader:
    def __init__(self, camera_id, url):
        self.camera_id, self.url, self.online, self.error = camera_id, url, True, None
        self.frame = None

    def start(self):
        return self

    def stop(self):
        pass

    def latest(self):
        return self.frame

    def read_fps(self):
        return 25.0


class FakeClient:
    def __init__(self, config):
        self.config, self.events, self.closed, self.fail = config, [], [], False

    def get_config(self):
        return self.config

    def post_event(self, body):
        if self.fail:
            raise ConnectionError("backend down")
        self.events.append(body)
        return Event(id=f"evt_{len(self.events):010d}", camera_name="c", **body.model_dump(exclude={"captured_ts"}))

    def close_event(self, event_id, ended_at):
        self.closed.append(event_id)


class FakeSender:
    connected = True

    def __init__(self):
        self.sent = []

    def send(self, text):
        self.sent.append(json.loads(text))


@pytest.fixture
def worker(tmp_path):
    spec = load_model_spec(write_model_yaml(tmp_path / "ppe", "ppe", {"person": 0.5, "helmet": 0.35, "no_helmet": 0.4}))
    scenario = {"ppe": [[FakeBox("person", (0.25, 0.25, 0.5, 0.5), 0.9), FakeBox("no_helmet", (0.45, 0.25, 0.1, 0.1), 0.7)]]}
    remote = WorkerConfig(version=1, cameras=[WorkerCamera(id=3, name="c", rtsp_url="rtsp://x/cam-3")])
    w = Worker(AppConfig(), {"ppe": spec}, FakeDetector({"ppe": spec}, scenario), FakeClient(remote), FakeSender(),
               reader_factory=FakeReader)
    w.sync_config()
    return w


def push(worker, seq, ts_ms, size=(640, 640)):
    worker.cameras[3].reader.frame = Frame(np.zeros((size[1], size[0], 3), np.uint8), ts_ms, seq)
    return worker.tick_ppe()


def test_tick_sends_detections_in_contract_shape(worker):
    assert push(worker, 1, 1_759_581_065_120) == 1
    msg = worker.sender.sent[0]
    assert msg["type"] == "detections" and msg["camera_id"] == 3 and msg["ts"] == 1_759_581_065_120
    assert msg["frame"] == {"w": 640, "h": 640}
    assert msg["people"][0]["bbox"] == pytest.approx([0.25, 0.25, 0.5, 0.5], abs=1e-3)
    assert msg["objects"] == [{"cls": "no_helmet", "bbox": pytest.approx([0.45, 0.25, 0.1, 0.1], abs=1e-3),
                               "conf": 0.7, "track_id": None}]


def test_tick_skips_frame_already_processed(worker):
    assert push(worker, 1, 1000) == 1
    assert worker.tick_ppe() == 0  # тот же кадр дважды не гоняем
    assert len(worker.sender.sent) == 1


def test_boxes_are_normalized_for_wide_frame(worker):
    push(worker, 1, 1000, size=(1280, 640))
    person = worker.sender.sent[0]["people"][0]["bbox"]
    assert person == pytest.approx([0.25, 0.0, 0.5, 1.0], abs=2e-3)


def test_event_is_posted_once_and_people_are_marked(worker):
    for i in range(12):
        push(worker, i + 1, 1_000_000 + i * 166)
    assert len(worker.client.events) == 1
    ev = worker.client.events[0]
    assert ev.type.value == "no_helmet" and ev.camera_id == 3 and ev.captured_ts == 1_000_000 + 7 * 166
    assert worker.sender.sent[-1]["people"][0]["violations"] == ["no_helmet"]
    assert worker.sender.sent[0]["people"][0]["violations"] == []


def test_event_is_kept_while_backend_is_down(worker):
    worker.client.fail = True
    for i in range(12):
        push(worker, i + 1, 1_000_000 + i * 166)
    assert worker.client.events == [] and len(worker._pending) == 1
    worker.client.fail = False
    worker.flush_events()
    assert len(worker.client.events) == 1 and not worker._pending


def test_config_change_replaces_readers_without_restart(worker):
    first = worker.cameras[3].reader
    worker.client.config = WorkerConfig(version=2, cameras=[
        WorkerCamera(id=3, name="c2", rtsp_url="rtsp://x/cam-3"), WorkerCamera(id=4, name="d", rtsp_url="rtsp://x/cam-4")])
    worker.sync_config()
    assert set(worker.cameras) == {3, 4} and worker.cameras[3].reader is first
    assert worker.cameras[3].camera.name == "c2"
    worker.client.config = WorkerConfig(version=3, cameras=[])
    worker.sync_config()
    assert worker.cameras == {}


def test_stats_shape(worker):
    push(worker, 1, 1000)
    s = worker.stats()
    assert s["detector"] == "fake" and s["streams"] == 1 and s["warnings"] == []
    assert s["cameras"]["3"]["online"] is True and s["inference"]["ppe"]["batch"] == 1
