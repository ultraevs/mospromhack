import time

import numpy as np

from worker.app.capture import CameraReader


class FakeCapture:
    def __init__(self, frames: int, delay: float = 0.005):
        self.left, self.delay, self.released = frames, delay, False

    def read(self):
        time.sleep(self.delay)
        if self.left <= 0:
            return False, None
        self.left -= 1
        return True, np.full((4, 4, 3), self.left % 255, np.uint8)

    def release(self):
        self.released = True


def wait_for(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_reader_keeps_only_latest_frame_with_capture_time():
    cap = FakeCapture(frames=10_000)
    reader = CameraReader(1, "rtsp://x", open_fn=lambda url: cap).start()
    try:
        assert wait_for(lambda: reader.latest() is not None and reader.latest().seq >= 5)
        a = reader.latest()
        assert reader.online and reader.error is None
        assert abs(a.ts_ms - time.time() * 1000) < 2000
        assert wait_for(lambda: reader.latest().seq > a.seq)
        assert reader.latest().ts_ms >= a.ts_ms
    finally:
        reader.stop()
    assert cap.released


def test_reader_reconnects_after_open_failure_and_stream_end():
    opened = []

    def open_fn(url):
        opened.append(url)
        if len(opened) == 1:
            return None  # сначала не открывается
        if len(opened) == 2:
            return FakeCapture(frames=3)  # потом 3 кадра и обрыв
        return FakeCapture(frames=10_000)

    reader = CameraReader(7, "rtsp://x", open_fn=open_fn, pauses=(0.02, 0.02)).start()
    try:
        assert wait_for(lambda: len(opened) >= 3 and reader.latest() is not None and reader.latest().seq > 3)
        assert reader.online and reader.error is None
    finally:
        reader.stop()


def test_reader_survives_exception_in_read():
    class Broken(FakeCapture):
        def read(self):
            raise RuntimeError("decoder crashed")

    caps = [Broken(1), FakeCapture(frames=10_000)]
    reader = CameraReader(1, "rtsp://x", open_fn=lambda url: caps.pop(0) if caps else None, pauses=(0.02,)).start()
    try:
        assert wait_for(lambda: reader.latest() is not None)
        assert reader.online
    finally:
        reader.stop()


def test_reader_reports_offline_with_reason():
    reader = CameraReader(1, "rtsp://x", open_fn=lambda url: None, pauses=(0.02,)).start()
    try:
        time.sleep(0.1)
        assert not reader.online and reader.latest() is None
        assert reader.error and reader.read_fps() == 0.0
    finally:
        reader.stop()


import shutil  # noqa: E402
import subprocess  # noqa: E402

import pytest  # noqa: E402

from worker.app import capture  # noqa: E402
from worker.app.capture import ffmpeg_command, open_capture, probe_stream, target_size  # noqa: E402

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="нет ffmpeg в PATH")


def test_target_size_keeps_aspect_and_even_sides():
    assert target_size(1920, 1080, 1280) == (1280, 720)
    assert target_size(1920, 1080, 0) == (1920, 1080)
    assert target_size(640, 360, 1280) == (640, 360)
    assert target_size(2592, 1944, 1280) == (1280, 960)
    assert target_size(1921, 1081, 961) == (960, 540)


def test_ffmpeg_command_gpu_and_cpu():
    gpu = ffmpeg_command("rtsp://h/cam-1", "h264", (1280, 720), (1920, 1080), 6, cuvid=True)
    assert gpu[gpu.index("-c:v") + 1] == "h264_cuvid" and gpu[gpu.index("-resize") + 1] == "1280x720"
    assert gpu.index("-c:v") < gpu.index("-i") and gpu[gpu.index("-vf") + 1] == "fps=6"
    assert gpu[gpu.index("-rtsp_transport") + 1] == "tcp" and gpu[-5:] == ["-pix_fmt", "bgr24", "-f", "rawvideo", "pipe:1"]
    cpu = ffmpeg_command("rtsp://h/cam-1", "h264", (1280, 720), (1920, 1080), 6, cuvid=False)
    assert "-c:v" not in cpu and "-resize" not in cpu and cpu[cpu.index("-vf") + 1] == "fps=6,scale=1280:720"
    same = ffmpeg_command("a.mp4", "hevc", (640, 360), (640, 360), 0, cuvid=False)
    assert "-vf" not in same and "-rtsp_transport" not in same
    assert "-resize" not in ffmpeg_command("a.mp4", "hevc", (640, 360), (640, 360), 6, cuvid=True)


@pytest.fixture
def clip(tmp_path):
    path = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "color=c=red:s=160x240:r=20:d=2,format=yuv420p[l];color=c=blue:s=160x240:r=20:d=2,format=yuv420p[r];[l][r]hstack",
                    "-c:v", "libx264", "-bf", "0", str(path)], check=True)
    return path


@needs_ffmpeg
def test_ffmpeg_capture_gives_scaled_bgr_frames_at_target_rate(clip, tmp_path):
    assert probe_stream(str(clip)) == ("h264", 320, 240)
    cap = open_capture(str(clip), width=160, hwaccel="none", fps=5, log_path=tmp_path / "decode.log")
    assert (cap.size, cap.src, cap.px_scale, cap.cuvid) == ((160, 120), (320, 240), 2.0, False)
    frames = []
    while True:
        ok, image = cap.read()
        if not ok:
            break
        frames.append(image)
    cap.release()
    assert len(frames) in (10, 11)
    assert frames[0].shape == (120, 160, 3) and frames[0].dtype == np.uint8
    b, g, r = frames[3][60, 20]
    assert r > 200 and b < 60 and g < 60
    b, g, r = frames[3][60, 140]
    assert b > 200 and r < 60


@needs_ffmpeg
def test_ffmpeg_capture_missing_source_and_reader_scale(clip, tmp_path):
    assert probe_stream(str(tmp_path / "nope.mp4")) is None
    assert open_capture(str(tmp_path / "nope.mp4"), 160, "none", 5) is None
    reader = CameraReader(1, str(clip), open_fn=lambda u: open_capture(u, 160, "none", 20), pauses=(0.05,)).start()
    deadline = time.monotonic() + 10
    while reader.latest() is None and time.monotonic() < deadline:
        time.sleep(0.02)
    frame = reader.latest()
    reader.stop()
    assert frame is not None and frame.px_scale == 2.0 and frame.image.shape == (120, 160, 3) and reader.decoder == "cpu"


@needs_ffmpeg
def test_gpu_decoder_failure_falls_back_to_cpu(clip, monkeypatch):
    monkeypatch.setattr(capture, "_cuvid_failed", False)
    monkeypatch.setitem(capture.CUVID, "h264", "no_such_decoder")
    cap = open_capture(str(clip), 160, "auto", 5)
    assert cap is not None and cap.cuvid is False and capture._cuvid_failed is True and capture.decoder_state() == "cpu"
    assert cap.read()[0]
    cap.release()


def test_opencv_backend_is_still_available(monkeypatch):
    monkeypatch.setattr(capture, "open_rtsp", lambda url: ("opencv", url))
    assert open_capture("rtsp://x", backend="opencv") == ("opencv", "rtsp://x")


def test_reader_keeps_short_queue_of_new_frames():
    frames = [np.full((2, 2, 3), i, np.uint8) for i in range(1, 8)]
    gate = {"n": 2}

    class Stepped:
        def __init__(self):
            self.i = 0

        def read(self):
            while self.i >= gate["n"]:
                time.sleep(0.005)
            self.i += 1
            return (True, frames[self.i - 1]) if self.i <= len(frames) else (False, None)

        def release(self):
            pass

    reader = CameraReader(1, "rtsp://x", open_fn=lambda _u: Stepped(), pauses=(5,)).start()
    try:
        assert wait_for(lambda: reader.latest() is not None and reader.latest().seq == 2)
        assert [f.seq for f in reader.take_new()] == [1, 2]
        assert reader.take_new() == []
        gate["n"] = 7
        assert wait_for(lambda: reader.latest().seq == 7)
        assert [f.seq for f in reader.take_new()] == [6, 7]
        assert reader.latest().seq == 7
    finally:
        reader.stop()


from shared.sync import SYNC_EPOCH_S, loop_offset  # noqa: E402


def test_loop_offset_wraps():
    assert loop_offset(10, SYNC_EPOCH_S + 3.5) == pytest.approx(3.5)
    assert loop_offset(10, SYNC_EPOCH_S + 23.5) == pytest.approx(3.5)
    assert loop_offset(10, SYNC_EPOCH_S - 1) == pytest.approx(9.0)  # до эпохи тоже положительный
    assert loop_offset(0, SYNC_EPOCH_S + 5) == 0.0 and loop_offset(-1) == 0.0


def test_ffmpeg_command_syncfile(monkeypatch, tmp_path):
    monkeypatch.setenv("SYNC_VIDEOS_DIR", str(tmp_path))
    monkeypatch.setattr(capture, "_durations", {tmp_path / "a.mp4": 10.0})
    monkeypatch.setattr(capture, "loop_offset", lambda d, now=None: loop_offset(d, SYNC_EPOCH_S + 27.25))
    cmd = ffmpeg_command("syncfile://a.mp4", "h264", (640, 360), (1280, 720), 6, cuvid=False)
    i = cmd.index("-i")
    assert cmd[i + 1] == str(tmp_path / "a.mp4") and cmd[i + 2:i + 4] == ["-ss", "7.250"]
    assert cmd[cmd.index("-readrate") + 1] == "1" and cmd[cmd.index("-stream_loop") + 1] == "-1"
    assert cmd[cmd.index("-vf") + 1] == "fps=6,scale=640:360" and cmd[-1] == "pipe:1"
    monkeypatch.setattr(capture, "_durations", {tmp_path / "a.mp4": None})
    assert "-ss" not in ffmpeg_command("syncfile://a.mp4", "h264", (640, 360), (640, 360), 0, cuvid=False)
