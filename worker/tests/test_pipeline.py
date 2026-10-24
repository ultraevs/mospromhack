import json

import numpy as np
import pytest

from shared.config import AppConfig
from shared.schemas import Event, RuleSettings, WorkerCamera, WorkerConfig, Zone
from worker.app.capture import Frame
from worker.app.detector.fake import FakeBox, FakeDetector
from worker.app.main import Worker
from worker.app.model_spec import load_model_spec
from worker.app.pipeline import CameraPipeline, check_classes
from worker.app.postprocess import Detections
from worker.tests.conftest import write_model_yaml

RULES = RuleSettings()
THRESHOLDS = {"person": 0.5, "helmet": 0.35, "no_helmet": 0.4, "vest": 0.3, "mask": 0.27, "no_mask": 0.43, "vehicle": 0.43}


@pytest.fixture
def spec(tmp_path):
    return load_model_spec(write_model_yaml(tmp_path / "ppe", "ppe", THRESHOLDS))


def dets(spec, *rows):
    if not rows:
        return Detections.empty()
    return Detections(np.array([r[1:5] for r in rows], np.float32), np.array([r[5] for r in rows], np.float32),
                      np.array([spec.class_id(r[0]) for r in rows], np.int64))


def cam(required=("helmet",), zones=()):
    return WorkerCamera(id=1, name="c", rtsp_url="rtsp://x", required_ppe=list(required), zones=list(zones))


PERSON_A = ("person", 100, 100, 200, 400, 0.9)
PERSON_B = ("person", 600, 100, 700, 400, 0.8)
A_NO_HELMET = ("no_helmet", 130, 105, 170, 145, 0.6)
B_HELMET = ("helmet", 630, 105, 670, 145, 0.7)


def run(pipe, spec, camera, frames, rows, start_ms=1_000_000, rules=RULES):
    out = []
    for i in range(frames):
        out.append(pipe.process_ppe(dets(spec, *rows), spec, 1000, 1000, start_ms + i * 167, camera, rules))
    return out


def test_check_classes_rejects_model_without_expected_names(tmp_path):
    check_classes(load_model_spec(write_model_yaml(tmp_path / "ok", "ppe", THRESHOLDS)))
    bad = load_model_spec(write_model_yaml(tmp_path / "bad", "ppe", {"person": 0.5, "hardhat": 0.3}))
    with pytest.raises(KeyError, match="нет класса"):
        check_classes(bad)


def test_message_has_tracks_ppe_status_and_object_owner(spec):
    pipe = CameraPipeline()
    msg, actions = run(pipe, spec, cam(), 1, [PERSON_A, PERSON_B, A_NO_HELMET, B_HELMET])[0]
    assert actions == []
    a, b = msg.people
    assert a.track_id != b.track_id and a.bbox == [0.1, 0.1, 0.1, 0.3]
    assert a.ppe == {"helmet": "unknown", "mask": "not_required", "vest": "not_required"}
    assert b.ppe["helmet"] == "ok" and a.violations == [] and b.violations == []
    owners = {o.cls: o.track_id for o in msg.objects}
    assert owners == {"no_helmet": a.track_id, "helmet": b.track_id}


def test_violation_is_attached_to_the_right_person_only(spec):
    pipe = CameraPipeline()
    results = run(pipe, spec, cam(), 10, [PERSON_A, PERSON_B, A_NO_HELMET, B_HELMET])
    opens = [a for _, acts in results for a in acts]
    assert len(opens) == 1
    msg = results[-1][0]
    a, b = msg.people
    assert opens[0].track_id == a.track_id and opens[0].type.value == "no_helmet"
    assert a.violations == ["no_helmet"] and a.ppe["helmet"] == "violation"
    assert b.violations == [] and b.ppe["helmet"] == "ok"


def test_unattached_item_does_not_count(spec):
    pipe = CameraPipeline()
    stray = ("no_helmet", 400, 105, 440, 145, 0.6)
    results = run(pipe, spec, cam(), 12, [PERSON_A, stray])
    assert all(acts == [] for _, acts in results)
    assert results[-1][0].objects[0].track_id is None


def test_zone_requirements_and_intrusion(spec):
    left_half = [[0, 0], [0.5, 0], [0.5, 1], [0, 1]]
    zones = [Zone(id=3, camera_id=1, name="Резка", type="ppe_required", polygon=left_half, required_ppe=["helmet"]),
             Zone(id=4, camera_id=1, name="Запретная", type="restricted", polygon=[[0.55, 0], [1, 0], [1, 1], [0.55, 1]])]
    pipe = CameraPipeline()
    b_no_helmet = ("no_helmet", 630, 105, 670, 145, 0.6)
    results = run(pipe, spec, cam(required=(), zones=zones), 12, [PERSON_A, PERSON_B, A_NO_HELMET, b_no_helmet])
    msg = results[-1][0]
    a, b = msg.people
    assert a.zones == [3] and b.zones == [4]
    assert a.ppe["helmet"] == "violation" and b.ppe["helmet"] == "not_required"
    opened = {(e.type.value, e.track_id, e.zone_id) for _, acts in results for e in acts}
    assert opened == {("no_helmet", a.track_id, 3), ("zone_intrusion", b.track_id, 4)}


def test_zone_change_applies_on_next_frame(spec):
    pipe = CameraPipeline()
    assert all(a == [] for _, a in run(pipe, spec, cam(required=()), 12, [PERSON_A]))
    zone = Zone(id=4, camera_id=1, name="Запретная", type="restricted", polygon=[[0, 0], [0.5, 0], [0.5, 1], [0, 1]])
    msg, actions = run(pipe, spec, cam(required=(), zones=[zone]), 1, [PERSON_A], start_ms=1_000_000 + 12 * 167)[0]
    assert msg.people[0].zones == [4] and actions == []
    later = run(pipe, spec, cam(required=(), zones=[zone]), 12, [PERSON_A], start_ms=1_000_000 + 13 * 167)
    assert [a.type.value for _, acts in later for a in acts] == ["zone_intrusion"]


def test_lost_tracks_are_dropped_from_state(spec):
    pipe = CameraPipeline()
    run(pipe, spec, cam(), 3, [PERSON_A])
    assert len(pipe.tracks) == 1
    run(pipe, spec, cam(), 1, [], start_ms=1_010_000)
    assert pipe.tracks == {}


class FakeReader:
    def __init__(self, camera_id, url):
        self.camera_id, self.url, self.online, self.error, self.frame = camera_id, url, True, None, None

    def start(self):
        return self

    def stop(self, wait=True):
        self.stopped_with_wait = wait

    def latest(self):
        return self.frame

    def read_fps(self):
        return 25.0


class FakeClient:
    def __init__(self, config):
        self.config, self.events, self.closed, self.fail = config, [], [], False
        self.snapshots, self.clips = [], []

    def get_config(self):
        return self.config

    def post_event(self, body, snapshot=None, clean=None):
        if self.fail:
            raise ConnectionError("backend down")
        self.events.append(body)
        self.snapshots.append((snapshot, clean))
        return Event(id=f"evt_{len(self.events):010d}", camera_name="c", **body.model_dump(exclude={"captured_ts"}))

    def close_event(self, event_id, ended_at):
        self.closed.append(event_id)

    def upload_clip(self, event_id, path):
        self.clips.append((event_id, path.read_bytes()))

    def close(self):
        pass


class FakeSender:
    connected = True

    def __init__(self):
        self.sent = []

    def send(self, text):
        self.sent.append(json.loads(text))

    def stop(self):
        pass


@pytest.fixture
def worker(spec):
    scenario = {"ppe": [[FakeBox("person", (0.25, 0.25, 0.5, 0.5), 0.9), FakeBox("no_helmet", (0.45, 0.27, 0.1, 0.1), 0.7)]]}
    remote = WorkerConfig(version=1, cameras=[WorkerCamera(id=3, name="c", rtsp_url="rtsp://x/cam-3", required_ppe=["helmet"])])
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
    person = msg["people"][0]
    assert person["bbox"] == pytest.approx([0.25, 0.25, 0.5, 0.5], abs=1e-3)
    assert set(person) == {"track_id", "bbox", "conf", "ppe", "zones", "violations"}
    assert msg["objects"] == [{"cls": "no_helmet", "bbox": pytest.approx([0.45, 0.27, 0.1, 0.1], abs=1e-3),
                               "conf": 0.7, "track_id": person["track_id"]}]


def test_tick_skips_frame_already_processed(worker):
    assert push(worker, 1, 1000) == 1
    assert worker.tick_ppe() == 0
    assert len(worker.sender.sent) == 1


def test_boxes_are_normalized_for_wide_frame(worker):
    push(worker, 1, 1000, size=(1280, 640))
    assert worker.sender.sent[0]["people"][0]["bbox"] == pytest.approx([0.25, 0.0, 0.5, 1.0], abs=2e-3)


def test_event_is_posted_once_with_track_and_capture_time(worker):
    for i in range(12):
        push(worker, i + 1, 1_000_000 + i * 166)
    assert len(worker.client.events) == 1
    ev = worker.client.events[0]
    track_id = worker.sender.sent[-1]["people"][0]["track_id"]
    assert ev.type.value == "no_helmet" and ev.camera_id == 3 and ev.track_id == track_id
    assert ev.captured_ts == 1_000_000 + 7 * 166
    assert worker.sender.sent[-1]["people"][0]["violations"] == ["no_helmet"]
    assert worker.sender.sent[0]["people"][0]["violations"] == []


def test_event_is_closed_in_backend_when_track_disappears(worker):
    for i in range(12):
        push(worker, i + 1, 1_000_000 + i * 166)
    worker.detector = FakeDetector(worker.specs, {})
    for i in range(20):
        push(worker, 100 + i, 1_002_000 + i * 166)
    assert worker.client.closed == ["evt_0000000001"]


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
    assert first.stopped_with_wait is False


def test_stats_shape(worker):
    push(worker, 1, 1000)
    s = worker.stats()
    assert s["detector"] == "fake" and s["streams"] == 1 and s["warnings"] == []
    assert s["cameras"]["3"]["online"] is True and s["inference"]["ppe"]["batch"] == 1


def test_pixel_thresholds_use_source_frame_pixels(spec):
    small = ("person", 100, 100, 150, 190, 0.9)
    for scale, expected in ((1.0, 0), (1.5, 1)):
        pipe = CameraPipeline()
        opened = []
        for i in range(40):
            _, actions = pipe.process_ppe(dets(spec, small), spec, 1000, 1000, 1_000_000 + i * 167, cam(required=("vest",)),
                                          RULES, px_scale=scale)
            opened += actions
        assert len(opened) == expected, scale
        assert pipe.tracks[next(iter(pipe.tracks))].last.height_px == pytest.approx(90 * scale)


def test_linq_thresholds_replace_float_ones(spec):
    det = FakeDetector({"ppe": spec}, {})
    det.thresholds = {"ppe": np.full(spec.nc, 0.05, np.float32)}
    w = Worker(AppConfig(), {"ppe": spec}, det, FakeClient(WorkerConfig(version=1, cameras=[])), FakeSender(),
               reader_factory=FakeReader)
    assert np.allclose(w.specs["ppe"].thresholds, 0.05) and not np.allclose(spec.thresholds, 0.05)
