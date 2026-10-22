import numpy as np
import pytest

from shared.config import AppConfig
from shared.schemas import RuleSettings, SmokingRule, WorkerCamera, WorkerConfig, Zone
from worker.app.capture import Frame
from worker.app.crops import crop_box, merge, only_classes, shift
from worker.app.detector.fake import FakeBox, FakeDetector
from worker.app.main import Worker
from worker.app.model_spec import load_model_spec
from worker.app.pipeline import CameraPipeline, check_classes
from worker.app.postprocess import Detections
from worker.tests.conftest import write_model_yaml
from worker.tests.test_pipeline import THRESHOLDS, FakeClient, FakeReader, FakeSender

RULES = RuleSettings()
EVERYWHERE = RuleSettings(smoking=SmokingRule(everywhere=True))
FLAMMABLE = Zone(id=9, camera_id=3, name="Склад ГСМ", type="flammable", polygon=[[0, 0], [1, 0], [1, 1], [0, 1]])
HAZARD = {"fire": 0.34, "smoke": 0.31, "cigarette": 0.36}
PERSON = FakeBox("person", (0.25, 0.25, 0.25, 0.5), 0.9)


@pytest.fixture
def specs(tmp_path):
    return {"ppe": load_model_spec(write_model_yaml(tmp_path / "ppe", "ppe", THRESHOLDS)),
            "hazard": load_model_spec(write_model_yaml(tmp_path / "hazard", "hazard", HAZARD))}


def make_worker(specs, scenario, cameras=(3,), config=None, zones=(), rules=RULES):
    remote = WorkerConfig(version=1, rules=rules, cameras=[
        WorkerCamera(id=i, name=f"c{i}", rtsp_url=f"rtsp://x/cam-{i}", zones=list(zones)) for i in cameras])
    w = Worker(config or AppConfig(), specs, FakeDetector(specs, scenario), FakeClient(remote), FakeSender(),
               reader_factory=FakeReader)
    w.sync_config()
    return w


def push(worker, seq, ts_ms, cameras=None, size=(640, 640)):
    for cam_id in cameras or worker.cameras:
        worker.cameras[cam_id].reader.frame = Frame(np.zeros((size[1], size[0], 3), np.uint8), ts_ms, seq)


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch):
    now = {"ms": 1_000_000}
    monkeypatch.setattr("worker.app.main.time.time", lambda: now["ms"] / 1000)
    return now


def test_crop_box_takes_top_part_with_margin_and_clips():
    assert crop_box((200, 100, 300, 400), 0.6, 0.2, 1920, 1080) == (180, 64, 320, 316)
    assert crop_box((0, 0, 100, 300), 0.6, 0.2, 1920, 1080) == (0, 0, 120, 216)
    assert crop_box((1850, 900, 1920, 1080), 0.6, 0.2, 1920, 1080) == (1836, 878, 1920, 1029)
    assert crop_box((10, 10, 14, 20), 0.5, 0.0, 1920, 1080) is None


def test_shift_only_classes_merge():
    d = Detections(np.array([[10, 10, 20, 20], [30, 30, 40, 40]], np.float32), np.array([0.9, 0.8], np.float32),
                   np.array([0, 2]))
    s = shift(only_classes(d, {2}), 100, 200)
    assert s.xyxy.tolist() == [[130, 230, 140, 240]] and s.cls.tolist() == [2]
    frame = Detections(np.array([[130, 230, 140, 240]], np.float32), np.array([0.5], np.float32), np.array([2]))
    extra = Detections(np.array([[130, 230, 140, 241], [300, 300, 310, 310]], np.float32),
                       np.array([0.7, 0.6], np.float32), np.array([2, 2]))
    m = merge(frame, extra, 0.5)
    assert sorted(m.conf.tolist()) == pytest.approx([0.6, 0.7])
    assert merge(frame, Detections.empty(), 0.5) is frame


def test_check_classes_covers_hazard(specs, tmp_path):
    check_classes(specs["ppe"], specs["hazard"])
    bad = load_model_spec(write_model_yaml(tmp_path / "bad", "hazard", {"fire": 0.3, "smoke": 0.3}))
    with pytest.raises(KeyError, match="cigarette"):
        check_classes(specs["ppe"], bad)


def test_fire_opens_after_3_of_5_checks_and_is_camera_level(specs):
    fire = [FakeBox("fire", (0.6, 0.4, 0.2, 0.2), 0.55)]
    worker = make_worker(specs, {"hazard": [fire, [], fire, [], fire, [], [], [], [], [], [], [], []]}, )
    worker.config.worker.crop_pass.cigarette.enabled = False
    opened_at = None
    for i in range(13):
        push(worker, i + 1, 1_000_000 + i * 1000)
        worker.tick_hazard()
        if worker.client.events and opened_at is None:
            opened_at = i
    assert opened_at == 4
    ev = worker.client.events[0]
    assert (ev.type.value, ev.severity.value, ev.track_id, ev.camera_id) == ("fire", "high", None, 3)
    assert ev.bbox == pytest.approx([0.6, 0.4, 0.2, 0.2], abs=1e-3) and ev.captured_ts == 1_004_000
    assert len(worker.client.events) == 1
    assert worker.client.closed == ["evt_0000000001"]


def test_single_fire_frame_does_not_open_event(specs):
    worker = make_worker(specs, {"hazard": [[FakeBox("smoke", (0.1, 0.1, 0.3, 0.3), 0.9)], [], [], [], []]})
    worker.config.worker.crop_pass.cigarette.enabled = False
    for i in range(10):
        push(worker, i + 1, 1_000_000 + i * 1000)
        worker.tick_hazard()
    assert worker.client.events == []


def test_hazard_boxes_are_added_to_detections_for_the_player(specs):
    worker = make_worker(specs, {"ppe": [[PERSON]], "hazard": [[FakeBox("fire", (0.6, 0.4, 0.2, 0.2), 0.55)]]})
    worker.config.worker.crop_pass.cigarette.enabled = False
    push(worker, 1, 1_000_000)
    worker.tick_hazard()
    worker.tick_ppe()
    objs = worker.sender.sent[-1]["objects"]
    assert [(o["cls"], o["track_id"]) for o in objs] == [("fire", None)]
    push(worker, 2, 1_003_000)
    worker.tick_ppe()
    assert worker.sender.sent[-1]["objects"] == []


def cigarette_scenario(found_on_crop, box=(0.45, 0.45, 0.1, 0.1)):
    def fn(model, call, item):
        if model == "ppe":
            return [PERSON]
        if call % 2 == 1 and found_on_crop(call // 2, item):
            return [FakeBox("cigarette", box, 0.6)]
        return []
    return fn


def run_ticks(worker, n, start_ms=1_000_000, clock=None):
    for i in range(n):
        ts = start_ms + i * 1000
        if clock:
            clock["ms"] = ts
        push(worker, 2 * i + 1, ts)
        worker.tick_ppe()
        worker.tick_hazard()
        push(worker, 2 * i + 2, ts + 500)
        worker.tick_ppe()


def test_smoking_opens_after_3_of_5_crop_checks(specs, frozen_clock):
    worker = make_worker(specs, cigarette_scenario(lambda tick, item: tick in (1, 3, 4)), rules=EVERYWHERE)
    run_ticks(worker, 4, clock=frozen_clock)
    assert worker.client.events == []
    run_ticks(worker, 1, start_ms=1_004_000, clock=frozen_clock)
    assert [e.type.value for e in worker.client.events] == ["smoking"]
    ev = worker.client.events[0]
    track_id = worker.sender.sent[-1]["people"][0]["track_id"]
    assert ev.track_id == track_id and ev.severity.value == "medium" and ev.zone_id is None
    assert ev.started_at.timestamp() == pytest.approx(1004.5)


def test_smoking_two_of_five_is_not_enough(specs, frozen_clock):
    worker = make_worker(specs, cigarette_scenario(lambda tick, item: tick in (1, 3)), rules=EVERYWHERE)
    run_ticks(worker, 8, clock=frozen_clock)
    assert worker.client.events == []


def test_smoking_by_default_works_only_in_flammable_zones(specs, frozen_clock):
    always = cigarette_scenario(lambda tick, item: True)
    worker = make_worker(specs, always)
    run_ticks(worker, 6, clock=frozen_clock)
    assert worker.client.events == [] and worker.stats()["crops_per_s"] == 0
    far = Zone(id=9, camera_id=3, name="Склад ГСМ", type="flammable", polygon=[[0.6, 0], [1, 0], [1, 1], [0.6, 1]])
    worker = make_worker(specs, always, zones=[far])
    run_ticks(worker, 6, clock=frozen_clock)
    assert worker.client.events == [] and worker.stats()["crops_per_s"] == 0
    restricted = FLAMMABLE.model_copy(update={"type": "restricted"})
    worker = make_worker(specs, always, zones=[restricted])
    run_ticks(worker, 6, clock=frozen_clock)
    assert [e.type.value for e in worker.client.events] == ["zone_intrusion"]


def test_cigarette_below_upper_third_of_person_does_not_count(specs, frozen_clock):
    low = cigarette_scenario(lambda tick, item: True, box=(0.45, 0.85, 0.1, 0.1))
    worker = make_worker(specs, low, rules=EVERYWHERE)
    run_ticks(worker, 6, clock=frozen_clock)
    assert worker.client.events == []
    assert [o for o in worker.sender.sent[-1]["objects"] if o["cls"] == "cigarette"] == []
    loose = RuleSettings(smoking=SmokingRule(everywhere=True, upper_fraction=0.7))
    worker = make_worker(specs, low, rules=loose)
    run_ticks(worker, 6, clock=frozen_clock)
    assert [e.type.value for e in worker.client.events] == ["smoking"]


def test_cigarette_box_is_mapped_from_crop_to_frame(specs, frozen_clock):
    worker = make_worker(specs, cigarette_scenario(lambda tick, item: True), rules=EVERYWHERE)
    run_ticks(worker, 1, clock=frozen_clock)
    assert crop_box((160, 160, 320, 480), 0.6, 0.2, 640, 640) == (128, 121, 352, 390)
    cig = [o for o in worker.sender.sent[-1]["objects"] if o["cls"] == "cigarette"][0]
    side = 64 * 269 / 640
    assert cig["bbox"] == pytest.approx([(240 - side / 2) / 640, (255.5 - side / 2) / 640, side / 640, side / 640], abs=3e-3)
    assert cig["track_id"] == worker.sender.sent[-1]["people"][0]["track_id"] and cig["conf"] == 0.6


def test_cigarette_on_full_frame_is_ignored(specs, frozen_clock):
    def fn(model, call, item):
        if model == "ppe":
            return [PERSON]
        return [FakeBox("cigarette", (0.3, 0.3, 0.05, 0.05), 0.9)] if call % 2 == 0 else []
    worker = make_worker(specs, fn, rules=EVERYWHERE)
    run_ticks(worker, 6, clock=frozen_clock)
    assert worker.client.events == []


def test_smoking_in_flammable_zone_is_high(specs, frozen_clock):
    worker = make_worker(specs, cigarette_scenario(lambda tick, item: True), zones=[FLAMMABLE])
    run_ticks(worker, 3, clock=frozen_clock)
    ev = worker.client.events[0]
    assert (ev.type.value, ev.severity.value, ev.zone_id) == ("smoking", "high", 9)


def test_crops_are_limited_per_tick_and_rotate_between_ticks(specs):
    pipe = CameraPipeline()
    people = [("person", 50 + 110 * i, 100, 130 + 110 * i, 400, 0.9) for i in range(8)]
    from worker.tests.test_pipeline import cam, dets
    pipe.process_ppe(dets(specs["ppe"], *people), specs["ppe"], 1000, 1000, 1_000_000, cam(required=()), RULES)
    ids = sorted(pipe.tracks)
    picked = [[tid for tid, _ in pipe.select_crops(1000.0, 1000, 1000, 6, 0.6, 0.2)] for _ in range(4)]
    assert picked[0] == ids[:6]
    assert picked[1] == ids[6:] + ids[:4]
    assert picked[2] == ids[4:] + ids[:2]
    assert all(len(p) == 6 for p in picked)
    assert pipe.select_crops(1005.0, 1000, 1000, 6, 0.6, 0.2) == []
    assert pipe.select_crops(1000.0, 1000, 1000, 0, 0.6, 0.2) == []
    assert pipe.select_crops(1000.0, 1000, 1000, 6, 0.6, 0.2, frozenset({9})) == []


def test_cigarette_pass_can_be_disabled(specs, frozen_clock):
    worker = make_worker(specs, cigarette_scenario(lambda tick, item: True), rules=EVERYWHERE)
    worker.config.worker.crop_pass.cigarette.enabled = False
    run_ticks(worker, 4, clock=frozen_clock)
    assert worker.client.events == [] and worker.stats()["crops_per_s"] == 0


def test_head_pass_finds_small_head_only_when_enabled(specs):
    small = FakeBox("person", (0.4, 0.4, 0.1, 0.25), 0.9)

    def fn(model, call, item):
        return [small] if call % 2 == 0 else [FakeBox("no_helmet", (0.3, 0.1, 0.4, 0.4), 0.7)]

    off = make_worker(specs, {"ppe": [[small]]})
    push(off, 1, 1_000_000)
    off.tick_ppe()
    assert off.sender.sent[-1]["objects"] == []

    config = AppConfig()
    config.worker.crop_pass.head.enabled = True
    on = make_worker(specs, fn, config=config)
    push(on, 1, 1_000_000)
    on.tick_ppe()
    msg = on.sender.sent[-1]
    assert [o["cls"] for o in msg["objects"]] == ["no_helmet"]
    assert msg["objects"][0]["track_id"] == msg["people"][0]["track_id"]
    x, y, w, h = msg["objects"][0]["bbox"]
    px, py, pw, ph = msg["people"][0]["bbox"]
    assert px - 0.03 <= x and x + w <= px + pw + 0.03 and py - 0.03 <= y and y + h <= py + 0.45 * ph + 0.03


def test_head_pass_skips_large_people(specs):
    config = AppConfig()
    config.worker.crop_pass.head.enabled = True
    worker = make_worker(specs, {"ppe": [[PERSON]]}, config=config)
    push(worker, 1, 1_000_000)
    worker.tick_ppe()
    assert worker.detector._calls["ppe"] == 1


def test_many_cameras_go_in_one_batch_and_keep_separate_state(specs):
    def fn(model, call, item):
        return [PERSON, FakeBox("no_helmet", (0.33, 0.27, 0.08, 0.08), 0.7)] if item == 1 else [PERSON]

    remote_cams = (1, 2, 3, 4)
    worker = make_worker(specs, fn, cameras=remote_cams)
    for cam_state in worker.cameras.values():
        cam_state.camera.required_ppe = ["helmet"]
    for i in range(12):
        push(worker, i + 1, 1_000_000 + i * 166)
        assert worker.tick_ppe() == 4
    assert worker.detector._calls["ppe"] == 12
    assert worker.stats()["inference"]["ppe"]["batch"] == 4
    assert [(e.camera_id, e.type.value) for e in worker.client.events] == [(2, "no_helmet")]
    by_cam = {m["camera_id"]: m for m in worker.sender.sent[-4:]}
    assert set(by_cam) == set(remote_cams)
    assert by_cam[2]["people"][0]["violations"] == ["no_helmet"] and by_cam[1]["people"][0]["violations"] == []


def test_only_cameras_with_new_frames_are_processed(specs):
    worker = make_worker(specs, {"ppe": [[PERSON]]}, cameras=(1, 2, 3))
    push(worker, 1, 1_000_000)
    assert worker.tick_ppe() == 3
    push(worker, 2, 1_000_166, cameras=[2])
    assert worker.tick_ppe() == 1 and worker.sender.sent[-1]["camera_id"] == 2


def test_stats_have_stage3_fields(specs, frozen_clock):
    worker = make_worker(specs, cigarette_scenario(lambda tick, item: False), cameras=(1, 2), rules=EVERYWHERE)
    run_ticks(worker, 2, clock=frozen_clock)
    s = worker.stats()
    assert s["streams"] == 2 and set(s["inference"]) == {"ppe", "hazard"}
    assert set(s["inference"]["hazard"]) == {"avg_ms", "p95_ms", "batch"}
    assert s["crops_per_s"] > 0 and s["crop_pass"] == {"cigarette": True, "head": False}
    assert s["target_fps"] == {"ppe": 6, "hazard": 1} and set(s["actual_fps"]) == {"ppe", "hazard"}
    assert len(s["session"]) == 32


def test_per_camera_rate_limit(specs, monkeypatch):
    clock = {"t": 100.0}
    monkeypatch.setattr("worker.app.main.time.monotonic", lambda: clock["t"])
    worker = make_worker(specs, {"ppe": [[PERSON]]}, cameras=(1, 2))
    worker.period_s = 0.15
    push(worker, 1, 1_000_000)
    assert worker.tick_ppe() == 2
    clock["t"] += 0.05
    push(worker, 2, 1_000_050)
    assert worker.tick_ppe() == 0
    clock["t"] += 0.10
    assert worker.tick_ppe() == 2
    clock["t"] += 0.20
    push(worker, 3, 1_000_300, cameras=[2])
    assert worker.tick_ppe() == 1
