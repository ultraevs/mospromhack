#!/usr/bin/env python3
# ролик для показа: настоящий воркер (onnx) проходит видео один раз, рамки и события рисуются поверх исходника
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import worker.app.capture as capture  # noqa: E402
import worker.app.main as wmain  # noqa: E402
from shared.config import ROOT, get_settings, load_config  # noqa: E402
from shared.schemas import WorkerCamera, WorkerConfig, Zone, ZoneType  # noqa: E402
from shared.sync import probe_duration  # noqa: E402
from worker.app.detector import create_detector  # noqa: E402
from worker.app.model_spec import load_all_specs  # noqa: E402

log = logging.getLogger("render")
OUT_DIR = ROOT / "data" / "demo"

LABEL = {
    "no_helmet": "Без каски", "no_mask": "Без маски", "no_vest": "Без жилета", "zone_intrusion": "Запретная зона",
    "smoking": "Курение", "fire": "Огонь", "smoke": "Дым", "running": "Бег", "vehicle_proximity": "Рядом с техникой",
    "fall": "Падение", "lying_immobile": "Лежит неподвижно", "phone_usage": "Телефон",
    "barrier_crossing": "Через ограждение", "under_load": "Под грузом", "no_harness": "Без привязи",
    "unsafe_carry": "Опасная переноска",
}
ITEM = {"helmet": "каска", "vest": "жилет", "mask": "маска"}
OBJ = {"helmet": ("каска", (40, 200, 70)), "no_helmet": ("без каски", (235, 50, 50)),
       "vest": ("жилет", (40, 200, 70)), "mask": ("маска", (40, 200, 70)), "no_mask": ("без маски", (255, 140, 0)),
       "vehicle": ("техника", (60, 140, 255)), "cell phone": ("телефон", (255, 140, 0))}
RED, GREEN, YELLOW, WHITE = (235, 50, 50), (40, 200, 70), (250, 200, 40), (255, 255, 255)


class Backend:
    def __init__(self, cfg: WorkerConfig):
        self.cfg = cfg
        self.events: list[dict] = []

    def get_config(self) -> WorkerConfig:
        return self.cfg

    def post_event(self, body, snapshot=None, clean=None):
        self.events.append({"type": body.type.value, "track_id": body.track_id, "bbox": body.bbox,
                            "zone_id": body.zone_id, "captured_ts": body.captured_ts, "confidence": body.confidence})
        return SimpleNamespace(id=str(len(self.events)))

    def close_event(self, event_id, ended_at) -> None:
        pass

    def upload_clip(self, event_id, path) -> None:
        pass

    def post_stats(self, stats: dict) -> None:
        pass

    def close(self) -> None:
        pass


class Sender:
    connected = True

    def __init__(self):
        self.msgs: list[dict] = []

    def send(self, text: str) -> None:
        self.msgs.append(json.loads(text))

    def stop(self) -> None:
        pass


def parse_zone(text: str | None) -> list[list[float]] | None:
    if not text:
        return None
    return [[float(v) for v in p.split(",")] for p in text.split()]


def run(video: Path, ppe: list[str], zone: list[list[float]] | None, thr_scale: float, thr_models: list[str],
        slow: float, cigarette: bool = True) -> dict:
    settings = get_settings()
    config = load_config(settings.config_path)
    config.worker.detector = "onnx"
    config.worker.crop_pass.cigarette.enabled = cigarette
    specs = load_all_specs(settings.models_dir)
    for name in thr_models:
        if name in specs:
            specs[name].thresholds[:] = specs[name].thresholds * thr_scale
    detector = create_detector("onnx", specs, None, config.worker.precision, config.worker.linq.model_dump())
    pose = wmain.make_pose(settings, detector)

    src = video
    if slow != 1.0:
        # медленнее чем в жизни, чтобы воркер успел посчитать каждый кадр
        src = OUT_DIR / f"_slow_{video.stem[:40]}.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-an", "-vf", f"setpts={slow}*PTS",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", str(src)], check=True)
    dur = probe_duration(src) or 60
    opened: dict = {}

    def open_once(url, *a, **kw):
        if opened:
            opened.setdefault("t_end", time.time())
            return None
        cap = capture.open_capture(url, *a, **kw)
        if cap is not None:
            opened["t0"] = time.time()
        return cap

    os.environ["SYNC_VIDEOS_DIR"] = str(src.parent)
    capture.sync_input = lambda url, now=None: ["-re", "-i", str(src)]
    orig, wmain.open_capture = wmain.open_capture, open_once
    zones = [Zone(id=1, camera_id=1, name="Проход запрещён", type=ZoneType.restricted, polygon=zone)] if zone else []
    cam = WorkerCamera(id=1, name="Цех", rtsp_url=f"{capture.SYNC_SCHEME}{src.name}", required_ppe=ppe, zones=zones)
    backend, sender = Backend(WorkerConfig(version=1, cameras=[cam], rules=config.rules)), Sender()
    worker = wmain.Worker(config, specs, detector, backend, sender, pose=pose)
    thread = threading.Thread(target=worker.run, daemon=True)
    thread.start()
    t0 = time.monotonic()
    try:
        while time.monotonic() - t0 < dur + 25 and not ("t0" in opened and time.time() - opened["t0"] > dur + 8):
            time.sleep(1)
    finally:
        worker.stop()
        thread.join(timeout=10)
        for state in worker.cameras.values():
            state.reader.stop()
        for pool in (worker._pool, worker._cpu, worker._side, worker._detect_pool):
            pool.shutdown(wait=False, cancel_futures=True)
        worker._clips.cleanup()
        wmain.open_capture = orig
        close = getattr(detector, "close", None)
        if close:
            close()

    first = min((m["ts"] for m in sender.msgs), default=0)
    for m in sender.msgs:
        m["t_video"] = round((m["ts"] - first) / 1000 / slow, 3)
    for e in backend.events:
        e["t_video"] = round((e["captured_ts"] - first) / 1000 / slow, 3) if e["captured_ts"] else None
    log.info("кадров с рамками %s за %.1f с ролика, событий %s: %s", len(sender.msgs), dur / slow, len(backend.events),
             [(e["type"], e["track_id"], e["t_video"]) for e in backend.events])
    return {"video": str(video), "ppe": ppe, "zone": zone, "thr_scale": thr_scale, "thr_models": thr_models, "slow": slow,
            "msgs": sender.msgs, "events": backend.events}


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    for name in (("arialbd.ttf", "DejaVuSans-Bold.ttf") if bold else ("arial.ttf", "DejaVuSans.ttf")):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def tag(d: ImageDraw.ImageDraw, xy: tuple[float, float], text: str, color, f, above: bool = True) -> None:
    x, y = xy
    l, t, r, b = d.textbbox((0, 0), text, font=f)
    w, h = r - l + 8, b - t + 6
    y = y - h if above else y
    d.rectangle([x, y, x + w, y + h], fill=color)
    d.text((x + 4, y + 3 - t), text, font=f, fill=WHITE if color != YELLOW else (0, 0, 0))


def pick(msgs: list[dict], tv: float, hold: float = 0.6) -> dict | None:
    best = None
    for m in msgs:
        if m["t_video"] <= tv + 0.05:
            best = m
        else:
            break
    return best if best and tv - best["t_video"] <= hold else None


def render(data: dict, video: Path, out: Path) -> None:
    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 6
    W, H = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    k = H / 1080
    f_small, f_mid, f_big = font(int(18 * k)), font(int(22 * k), True), font(int(30 * k), True)
    msgs = sorted(data["msgs"], key=lambda m: m["t_video"])
    events = sorted((e for e in data["events"] if e["t_video"] is not None), key=lambda e: e["t_video"])
    zone = data.get("zone")
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                            "-r", str(fps), "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)], stdin=subprocess.PIPE)
    flagged: dict[int, set[str]] = {}
    i = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        tv = i / fps
        i += 1
        img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)).convert("RGBA")
        over = Image.new("RGBA", img.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(over)
        m = pick(msgs, tv)
        for e in events:
            if e["t_video"] <= tv and e["track_id"] is not None:
                flagged.setdefault(e["track_id"], set()).add(e["type"])
        inside = bool(m and any(1 in p["zones"] for p in m["people"]))
        if zone:
            pts = [(x * W, y * H) for x, y in zone]
            d.polygon(pts, fill=(235, 50, 50, 110 if inside else 55), outline=(235, 50, 50, 255), width=max(2, int(4 * k)))
            cx = sum(p[0] for p in pts) / len(pts)
            bottom = max(p[1] for p in pts)
            tw = d.textlength("ПРОХОД ЗАПРЕЩЁН", font=f_mid)
            tag(d, (cx - tw / 2, bottom + 4), "ПРОХОД ЗАПРЕЩЁН", RED, f_mid, above=False)
        if m:
            for o in m["objects"]:
                if o["cls"] not in OBJ:
                    continue
                name, color = OBJ[o["cls"]]
                x, y, w, h = o["bbox"]
                d.rectangle([x * W, y * H, (x + w) * W, (y + h) * H], outline=color + (255,), width=max(2, int(2 * k)))
                if o["cls"] not in ("vest", "helmet"):
                    tag(d, (x * W, y * H), name, color, f_small)
            for p in m["people"]:
                x, y, w, h = p["bbox"]
                missing = [ITEM[it] for it, st in p["ppe"].items() if st == "violation" and it in ITEM]
                bad = set(p["violations"]) | flagged.get(p["track_id"], set())
                color = RED if bad or missing else GREEN if any(st == "ok" for st in p["ppe"].values()) else YELLOW
                d.rectangle([x * W, y * H, (x + w) * W, (y + h) * H], outline=color + (255,), width=max(2, int(4 * k)))
                head = f"#{p['track_id']}"
                if "zone_intrusion" in bad:
                    head += " НАРУШИТЕЛЬ"
                tag(d, (x * W, y * H), head, color, f_mid)
                if missing:
                    tag(d, (x * W, (y + h) * H), "нет: " + ", ".join(missing), RED, f_small, above=False)
        # лента событий справа сверху
        recent = [e for e in events if e["t_video"] <= tv][-6:]
        px, py = W - int(430 * k), int(20 * k)
        if recent:
            d.rectangle([px - 10, py - 10, W - 10, py + int(46 * k) * (len(recent) + 1)], fill=(20, 20, 24, 200))
            d.text((px, py), "События", font=f_big, fill=WHITE)
            for j, e in enumerate(reversed(recent)):
                fresh = tv - e["t_video"] < 2.5
                mm, ss = divmod(int(e["t_video"]), 60)
                tid = f" · #{e['track_id']}" if e["track_id"] is not None else ""
                tag(d, (px, py + int(46 * k) * (j + 1)), f"{mm:02d}:{ss:02d}  {LABEL.get(e['type'], e['type'])}{tid}",
                    RED if fresh else (90, 90, 100), f_mid, above=False)
        if any(e["type"] == "zone_intrusion" and 0 <= tv - e["t_video"] < 1.5 for e in events):
            d.rectangle([0, 0, W - 1, H - 1], outline=(235, 50, 50, 255), width=int(12 * k))
        tag(d, (int(20 * k), H - int(20 * k)), "MISIS VIBE · онлайн-анализ: СИЗ, головы, люди и техника, огонь", (20, 20, 24),
            f_small)
        img = Image.alpha_composite(img, over).convert("RGB")
        enc.stdin.write(np.asarray(img).tobytes())
    cap.release()
    enc.stdin.close()
    enc.wait()
    print(f"ролик: {out}")


def main() -> None:
    ap = argparse.ArgumentParser(description="прогон ролика настоящим воркером и отрисовка рамок для показа")
    ap.add_argument("video", type=Path)
    ap.add_argument("--tag", default="demo", help="имя прогона, данные в data/demo/<tag>.json")
    ap.add_argument("--ppe", nargs="*", default=["helmet", "vest", "mask"])
    ap.add_argument("--zone", help='запретная зона, нормированные точки "x,y x,y x,y ..."')
    ap.add_argument("--thr-scale", type=float, default=1.0, help="множитель порогов моделей, <1 даёт больше рамок")
    ap.add_argument("--thr-models", nargs="*", default=["ppe", "heads", "people"],
                    help="к каким моделям применять --thr-scale; hazard лучше не трогать, иначе ложный дым и курение")
    ap.add_argument("--slow", type=float, default=1.0, help="во сколько раз замедлить ролик для прогона")
    ap.add_argument("--no-cigarette", action="store_true", help="без прохода по сигаретам (ролик, где никто не курит)")
    ap.add_argument("--render-only", action="store_true", help="не гонять модели, рисовать по сохранённому json")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for name in ("httpx", "worker.capture"):
        logging.getLogger(name).setLevel(logging.WARNING)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dump = OUT_DIR / f"{args.tag}.json"
    if args.render_only:
        data = json.loads(dump.read_text(encoding="utf-8"))
    else:
        data = run(args.video.resolve(), args.ppe, parse_zone(args.zone), args.thr_scale, args.thr_models, args.slow,
                   not args.no_cigarette)
        dump.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    render(data, args.video, args.out or OUT_DIR / f"{args.tag}.mp4")


if __name__ == "__main__":
    main()
