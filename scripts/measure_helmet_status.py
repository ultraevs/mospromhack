#!/usr/bin/env python3
from __future__ import annotations

import argparse
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.config import get_settings, load_config  # noqa: E402
from shared.schemas import Event, WorkerCamera, WorkerConfig  # noqa: E402
from worker.app.capture import Frame, open_capture  # noqa: E402
from worker.app.detector import create_detector  # noqa: E402
from worker.app.main import Worker  # noqa: E402
from worker.app.model_spec import load_all_specs  # noqa: E402

MIN_OBS = 8


class Client:

    def __init__(self, rules):
        self.remote = WorkerConfig(version=1, rules=rules, cameras=[
            WorkerCamera(id=1, name="замер", rtsp_url="file", required_ppe=["helmet"])])
        self.events = 0

    def get_config(self):
        return self.remote

    def post_event(self, body, snapshot=None, clean=None):
        self.events += 1
        return Event(id=f"evt_{self.events:010d}", camera_name="замер", **body.model_dump(exclude={"captured_ts"}))

    def upload_clip(self, event_id, path):
        pass

    def close_event(self, event_id, ended_at):
        pass

    def post_stats(self, stats):
        pass

    def close(self):
        pass


class Sender:
    connected = True

    def __init__(self):
        self.messages: list[str] = []

    def send(self, text):
        self.messages.append(text)

    def stop(self):
        pass


class Reader:
    online, error, decoder = True, None, ""

    def __init__(self, camera_id, url):
        self.url, self.frame = url, None

    def start(self):
        return self

    def stop(self, wait=True):
        pass

    def latest(self):
        return self.frame

    def read_fps(self):
        return 0.0


def run(path: Path, width: int, head: bool, specs, detector, config) -> dict:
    import json

    cfg = config.model_copy(deep=True)
    cfg.worker.crop_pass.head.enabled = head
    client, sender = Client(cfg.rules), Sender()
    worker = Worker(cfg, specs, detector, client, sender, reader_factory=Reader)
    worker.sync_config()
    cap = open_capture(str(path), width, "auto", cfg.worker.ppe_fps)
    if cap is None:
        raise SystemExit(f"не удалось открыть {path}")
    n, spent = 0, 0.0
    while True:
        ok, image = cap.read()
        if not ok:
            break
        n += 1
        worker.cameras[1].reader.frame = Frame(image, int(1_000_000_000 + n * 1000 / cfg.worker.ppe_fps), n, cap.px_scale)
        t0 = time.perf_counter()
        worker.tick_ppe()
        spent += time.perf_counter() - t0
    size = cap.size
    cap.release()
    crops = len(worker._crop_times)
    worker.shutdown()

    statuses: dict[int, list[str]] = defaultdict(list)
    heights: dict[int, list[float]] = defaultdict(list)
    for text in sender.messages:
        msg = json.loads(text)
        for p in msg["people"]:
            statuses[p["track_id"]].append(p["ppe"]["helmet"])
            heights[p["track_id"]].append(p["bbox"][3] * msg["frame"]["h"] * cap.px_scale)
    tracks = {tid: s for tid, s in statuses.items() if len(s) >= MIN_OBS}
    ever = Counter()
    frames = Counter()
    by_height = {"до 100 px": [0, 0], "100–200 px": [0, 0], "от 200 px": [0, 0]}
    for tid, s in tracks.items():
        tail = s[MIN_OBS - 1:]
        frames.update(tail)
        defined = "violation" in tail or "ok" in tail
        ever["violation" if "violation" in tail else "ok" if "ok" in tail else "unknown"] += 1
        h = statistics.median(heights[tid])
        bucket = "до 100 px" if h < 100 else "100–200 px" if h < 200 else "от 200 px"
        by_height[bucket][0] += 1
        by_height[bucket][1] += defined
    total_frames = sum(frames.values()) or 1
    return {
        "width": size[0], "height": size[1], "head": head, "frames": n, "tracks_all": len(statuses), "tracks": len(tracks),
        "ever": dict(ever), "frames_share": {k: v / total_frames for k, v in frames.items()},
        "by_height": by_height, "crops": crops, "ms_per_frame": spent / max(1, n) * 1000,
        "people_per_frame": sum(len(s) for s in statuses.values()) / max(1, n), "events": client.events,
    }


def main() -> None:
    settings = get_settings()
    config = load_config(settings.config_path)
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", default="warehouse.mp4")
    ap.add_argument("--widths", type=int, nargs="+", default=[0, 1280], help="ширина кадра после декодирования; 0 — исходная")
    args = ap.parse_args()
    path = settings.data_dir / "videos" / args.video
    specs = load_all_specs(settings.models_dir)
    detector = create_detector("onnx", specs)

    rows = [run(path, w, head, specs, detector, config) for w in args.widths for head in (False, True)]
    r0 = rows[0]
    print(f"\nвидео {args.video}, {r0['frames']} кадров при {config.worker.ppe_fps:g} кадрах/с, в среднем {r0['people_per_frame']:.1f} человека на кадре")
    print(f"треки от {MIN_OBS} наблюдений; статус считается с {MIN_OBS}-го кадра трека\n")
    print("| кадр | второй проход | треков | violation | ok | unknown | доля треков с определённым статусом | "
          "доля кадров трека с определённым статусом | вырезок на кадр | обработка кадра, мс | событий no_helmet |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        e, t = r["ever"], max(1, r["tracks"])
        defined = e.get("violation", 0) + e.get("ok", 0)
        fs = r["frames_share"]
        print(f"| {r['width']}x{r['height']} | {'вкл' if r['head'] else 'выкл'} | {r['tracks']} | {e.get('violation', 0)} | "
              f"{e.get('ok', 0)} | {e.get('unknown', 0)} | {defined / t:.0%} | "
              f"{fs.get('violation', 0) + fs.get('ok', 0):.0%} | {r['crops'] / max(1, r['frames']):.1f} | "
              f"{r['ms_per_frame']:.1f} | {r['events']} |")
    print("\nпо высоте человека в пикселях исходного кадра (треков всего / с определённым статусом):")
    for r in rows:
        parts = ", ".join(f"{k}: {v[0]} / {v[1]}" for k, v in r["by_height"].items())
        print(f"  {r['width']}x{r['height']}, второй проход {'вкл' if r['head'] else 'выкл'}: {parts}")


if __name__ == "__main__":
    main()
