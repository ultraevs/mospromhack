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
