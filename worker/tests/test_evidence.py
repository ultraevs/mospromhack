import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np
import pytest

from shared.config import AppConfig
from shared.schemas import WorkerCamera, WorkerConfig, Zone
from worker.app.capture import Frame
from worker.app.detector.fake import FakeBox, FakeDetector
from worker.app.evidence import CameraEvidence, ClipBuilder, annotate, build_clip, encode_jpeg, resize_to_width
from worker.app.main import Worker
from worker.tests.test_hazard import specs  # noqa: F401
from worker.tests.test_pipeline import FakeClient, FakeReader, FakeSender

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="нет ffmpeg в PATH")


def frame(value=90, size=(1280, 720)):
    return np.full((size[1], size[0], 3), value, np.uint8)


def wait_for(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def pool():
    p = ThreadPoolExecutor(2)
    yield p
    p.shutdown(wait=True)


def test_resize_keeps_aspect_and_even_height():
    assert resize_to_width(frame(size=(1920, 1080)), 1280).shape == (720, 1280, 3)
    assert resize_to_width(frame(size=(1000, 563)), 500).shape[0] % 2 == 0
    small = frame(size=(640, 360))
    assert resize_to_width(small, 1280) is small


def test_encode_jpeg_roundtrip():
    data = encode_jpeg(frame(120), 90)
    assert data[:2] == b"\xff\xd8"
    decoded = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    assert decoded.shape == (720, 1280, 3) and abs(int(decoded.mean()) - 120) <= 2


def test_annotate_draws_box_and_zone_without_touching_source():
    src = frame(90)
    out = annotate(src, (0.25, 0.25, 0.25, 0.5), "no_helmet", [[0.6, 0.1], [0.9, 0.1], [0.9, 0.9], [0.6, 0.9]])
    assert (src == 90).all() and out.shape == src.shape
    changed = np.any(out != 90, axis=2)
    assert changed[180:540, 320].any() and changed[180:540, 640].any()
    assert changed[72, 768:1152].any()
    assert not changed[600:700, 100:300].any()
    assert not changed[300:420, 420:560].any()
    assert np.any(annotate(src, None, "fire") != 90)


def test_buffer_keeps_only_pre_seconds(pool):
    ev = CameraEvidence(pre_seconds=2, post_seconds=1, clip_width=640, pool=pool)
    for i in range(40):
        ev.add_frame(frame(i), 1_000_000 + i * 200)
    assert wait_for(lambda: len(ev._frames) > 0 and ev._frames[-1][0] == 1_000_000 + 39 * 200)
    times = [t for t, _ in ev._frames]
    assert min(times) >= 1_000_000 + 39 * 200 - 2000 and len(times) == 11
    jpeg = ev._frames[-1][1]
    assert cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR).shape == (360, 640, 3)


def test_clip_gets_pre_and_post_frames_and_buffer_is_held_for_it(pool):
    ev = CameraEvidence(pre_seconds=2, post_seconds=1, clip_width=640, pool=pool)
    t0 = 1_000_000
    for i in range(20):
        ev.add_frame(frame(i), t0 + i * 200)
    wait_for(lambda: len(ev._frames) >= 11)
    event_ms = t0 + 19 * 200
    ev.start_clip(7, event_ms)
    assert ev.ready_clips(event_ms + 500) == [] and ev.pending() == 1
    for i in range(20, 40):
        ev.add_frame(frame(i), t0 + i * 200)
    assert wait_for(lambda: ev._frames and ev._frames[-1][0] == t0 + 39 * 200)
    (key, frames), = ev.ready_clips(event_ms + 1000)
    times = [t for t, _ in frames]
    assert key == 7 and times == sorted(times)
    assert times[0] == event_ms - 2000 and times[-1] == event_ms + 1000 and len(times) == 16
    assert ev.pending() == 0 and ev.ready_clips(event_ms + 5000) == []


@needs_ffmpeg
def test_build_clip_makes_h264_mp4_without_b_frames(tmp_path):
    frames = [(1_000_000 + i * 200, encode_jpeg(frame(40 + i * 5, (640, 360)), 80)) for i in range(15)]
    out = tmp_path / "clip.mp4"
    build_clip(frames, out)
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=codec_name,width,height,has_b_frames,nb_frames:format=duration",
                            "-of", "default=nw=1", str(out)], capture_output=True, text=True).stdout
    info = dict(line.split("=") for line in probe.strip().splitlines())
    assert info["codec_name"] == "h264" and (info["width"], info["height"]) == ("640", "360")
    assert info["has_b_frames"] == "0" and info["nb_frames"] == "15"
    assert 2.5 <= float(info["duration"]) <= 3.5


def test_build_clip_needs_two_frames(tmp_path):
    with pytest.raises(ValueError, match="два кадра"):
        build_clip([(1, b"x")], tmp_path / "clip.mp4")


def test_clip_builder_reports_failure(pool):
    done = []

    def broken(frames, path):
        raise RuntimeError("ffmpeg crashed")

    builder = ClipBuilder(pool, lambda key, path: done.append((key, path)), build=broken)
    builder.submit(5, [(1, b"a"), (2, b"b")])
    assert wait_for(lambda: done == [(5, None)])
    builder.cleanup()


def make_worker(specs, zones=()):
    scenario = {"ppe": [[FakeBox("person", (0.25, 0.25, 0.25, 0.5), 0.9), FakeBox("no_helmet", (0.33, 0.27, 0.08, 0.08), 0.7)]]}
    remote = WorkerConfig(version=1, cameras=[WorkerCamera(id=3, name="c", rtsp_url="rtsp://x/cam-3", required_ppe=["helmet"],
                                                           zones=list(zones))])
    config = AppConfig()
    config.evidence.pre_seconds, config.evidence.post_seconds = 1, 1
    config.worker.crop_pass.cigarette.enabled = False
    w = Worker(config, specs, FakeDetector(specs, scenario), FakeClient(remote), FakeSender(), reader_factory=FakeReader)
    w.sync_config()
    return w


def test_event_carries_two_snapshots(specs, monkeypatch):  # noqa: F811
    monkeypatch.setattr("worker.app.main.time.time", lambda: 1000.0)
    worker = make_worker(specs)
    for i in range(10):
        worker.cameras[3].reader.frame = Frame(frame(90, (640, 640)), 1_000_000 + i * 166, i + 1)
        worker.tick_ppe()
    assert len(worker.client.events) == 1
    snapshot, clean = worker.client.snapshots[0]
    marked = cv2.imdecode(np.frombuffer(snapshot, np.uint8), cv2.IMREAD_COLOR)
    plain = cv2.imdecode(np.frombuffer(clean, np.uint8), cv2.IMREAD_COLOR)
    assert marked.shape == plain.shape == (640, 640, 3)
    assert np.abs(plain.astype(int) - 90).max() <= 3
    diff = np.abs(marked.astype(int) - plain.astype(int)).max(axis=2) > 40
    assert diff[160:480, 158:163].any() and diff[160:480, 318:323].any()
    worker.shutdown()


@needs_ffmpeg
def test_clip_is_built_and_uploaded_after_post_seconds(specs, monkeypatch):  # noqa: F811
    clock = {"ms": 1_000_000}
    monkeypatch.setattr("worker.app.main.time.time", lambda: clock["ms"] / 1000)
    worker = make_worker(specs)
    for i in range(30):
        clock["ms"] = 1_000_000 + i * 166
        worker.cameras[3].reader.frame = Frame(frame(60 + i, (640, 360)), clock["ms"], i + 1)
        worker.tick_ppe()
        time.sleep(0.01)
    assert wait_for(lambda: (worker.tick_ppe(), worker.client.clips)[1], timeout=15)
    event_id, data = worker.client.clips[0]
    assert event_id == "evt_0000000001" and data[4:8] == b"ftyp" and len(data) > 1000
    stats = worker.stats()["clips"]
    assert stats == {"built": 1, "failed": 0, "pending": 0}
    worker.shutdown()


def test_clip_waits_until_event_reaches_backend(specs, monkeypatch):  # noqa: F811
    clock = {"ms": 1_000_000}
    monkeypatch.setattr("worker.app.main.time.time", lambda: clock["ms"] / 1000)
    worker = make_worker(specs)
    worker._clips = ClipBuilder(worker._pool, lambda key, path: worker._clips_done.put((key, path)),
                                build=lambda frames, path: path.write_bytes(b"fake-mp4"))
    worker.client.fail = True
    for i in range(30):
        clock["ms"] = 1_000_000 + i * 166
        worker.cameras[3].reader.frame = Frame(frame(60, (640, 360)), clock["ms"], i + 1)
        worker.tick_ppe()
    assert wait_for(lambda: not worker._clips_done.empty())
    worker.tick_ppe()
    assert worker.client.clips == [] and worker.client.events == []
    worker.client.fail = False
    worker.flush_events()
    worker.tick_ppe()
    assert worker.client.clips == [("evt_0000000001", b"fake-mp4")]
    worker.shutdown()
