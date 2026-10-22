#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.config import get_settings, load_config  # noqa: E402
from shared.schemas import RuleSettings, WorkerCamera  # noqa: E402
from worker.app.capture import open_capture  # noqa: E402
from worker.app.detector import create_detector  # noqa: E402
from worker.app.evidence import JPEG_QUALITY_BUFFER, encode_jpeg, resize_to_width  # noqa: E402
from worker.app.model_spec import load_all_specs  # noqa: E402
from worker.app.pipeline import CameraPipeline  # noqa: E402
from worker.app.postprocess import decode, preprocess  # noqa: E402


class Clock:

    def __init__(self):
        self.rows: dict[str, list[float]] = {}

    def measure(self, name: str, fn, repeat: int):
        w0, c0 = time.perf_counter(), time.process_time()
        out = None
        for i in range(repeat):
            out = fn(i)
        wall, cpu = time.perf_counter() - w0, time.process_time() - c0
        self.rows[name] = [wall / repeat * 1000, cpu / repeat * 1000]
        return out


def main() -> None:
    settings = get_settings()
    config = load_config(settings.config_path)
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", default="warehouse.mp4")
    ap.add_argument("--frames", type=int, default=300)
    ap.add_argument("--cameras", type=int, default=8)
    ap.add_argument("--decode-width", type=int, default=None, help="ширина кадра после декодирования; по умолчанию из config.yaml")
    ap.add_argument("--hwaccel", choices=["none", "auto"], default=None, help="по умолчанию из config.yaml")
    args = ap.parse_args()

    path = settings.data_dir / "videos" / args.video
    width = config.worker.decode.width if args.decode_width is None else args.decode_width
    hwaccel = config.worker.decode.hwaccel if args.hwaccel is None else args.hwaccel
    specs = load_all_specs(settings.models_dir)
    detector = create_detector("onnx", specs)
    spec = specs["ppe"]
    clock = Clock()

    src = cv2.VideoCapture(str(path))
    src_fps = src.get(cv2.CAP_PROP_FPS) or 25.0
    src_frames = int(src.get(cv2.CAP_PROP_FRAME_COUNT))
    src_size = (int(src.get(cv2.CAP_PROP_FRAME_WIDTH)), int(src.get(cv2.CAP_PROP_FRAME_HEIGHT)))
    src.release()
    duration = src_frames / src_fps
    fps = config.worker.ppe_fps

    def decode_opencv() -> tuple[float, np.ndarray]:
        cap = cv2.VideoCapture(str(path), cv2.CAP_FFMPEG)
        c0, n, image = time.process_time(), 0, None
        while True:
            ok, img = cap.read()
            if not ok:
                break
            n += 1
            if n == src_frames // 2:
                image = img
        cpu = time.process_time() - c0
        cap.release()
        return cpu / (n / src_fps), image

    def decode_ffmpeg(w: int, hw: str) -> tuple[float, np.ndarray, bool] | None:
        import psutil

        cap = open_capture(str(path), w, hw, fps)
        if cap is None:
            return None
        proc = psutil.Process(cap._cap._proc.pid)
        c0, image, child, count = time.process_time(), None, 0.0, 0
        while True:
            try:
                times = [p.cpu_times() for p in [proc, *proc.children(recursive=True)]]
                child = max(child, sum(t.user + t.system for t in times))
            except psutil.Error:
                pass
            ok, img = cap.read()
            if not ok:
                break
            count += 1
            if count == int(duration * fps / 2):
                image = img
        own = time.process_time() - c0
        cuvid = cap.cuvid
        cap.release()
        return (child + own) / duration, image, cuvid

    decode_rows: list[tuple[str, float]] = []
    cores, _ = decode_opencv()
    decode_rows.append((f"opencv в воркере: {src_size[0]}x{src_size[1]}, все {src_fps:.0f} кадров/с (как было)", cores))
    frame = None
    for w, hw in ((0, "none"), (1280, "none"), (0, "auto"), (1280, "auto"), (960, "auto")):
        got = decode_ffmpeg(w, hw)
        if got is None:
            continue
        cores, image, cuvid = got
        if hw == "auto" and not cuvid:
            continue
        label = f"ffmpeg: {image.shape[1]}x{image.shape[0]}, {fps:g} кадров/с, {'видеокарта' if cuvid else 'процессор'}"
        decode_rows.append((label, cores))
        if (w, hw) == (width, hwaccel) or frame is None:
            frame = image
    if args.decode_width is not None or args.hwaccel is not None or config.worker.decode.backend == "ffmpeg":
        got = decode_ffmpeg(width, hwaccel)
        frame = got[1] if got else frame
    else:
        frame = decode_opencv()[1]

    n = args.frames
    frames = [frame] * 4
    h, w = frame.shape[:2]

    batch = np.empty((4, 3, spec.input_size, spec.input_size), dtype=np.float32)
    infos = [None] * 4

    def pre(i):
        infos[i % 4] = preprocess(frames[i % 4], spec.input_size, out=batch[i % 4])[1]
    clock.measure("предобработка (letterbox до 640, /255)", pre, n)

    detector.infer("ppe", batch)
    outputs = clock.measure("инференс ppe (пачка 4, на кадр)", lambda i: detector.infer("ppe", batch), max(20, n // 4))
    clock.rows["инференс ppe (пачка 4, на кадр)"] = [v / 4 for v in clock.rows["инференс ppe (пачка 4, на кадр)"]]

    dets = clock.measure("постобработка (декодирование выхода, NMS)", lambda i: decode(outputs[i % 4], spec, infos[i % 4]), n)

    pipe = CameraPipeline()
    camera = WorkerCamera(id=1, name="c", rtsp_url="", required_ppe=["helmet"])
    rules = RuleSettings()
    msg = clock.measure("трекер, привязка СИЗ, правила",
                        lambda i: pipe.process_ppe(dets, spec, w, h, 1_000_000 + i * 167, camera, rules)[0], n)
    clock.measure("сообщение с рамками (JSON)", lambda i: msg.model_dump_json(), n)
    clock.measure("JPEG-буфер: масштабирование", lambda i: resize_to_width(frame, config.evidence.clip_width), n)
    small = resize_to_width(frame, config.evidence.clip_width)
    clock.measure("JPEG-буфер: кодирование", lambda i: encode_jpeg(small, JPEG_QUALITY_BUFFER), n)

    people = int((dets.cls == spec.class_id("person")).sum())
    print(f"\nвидео {args.video}: источник {src_size[0]}x{src_size[1]} при {src_fps:.0f} кадрах/с; в обработку идёт {w}x{h}; "
          f"людей на кадре {people}; устройство {detector.device}")
    print(f"\nдекодирование, ядер процессора на {args.cameras} камер:")
    for name, cores in decode_rows:
        print(f"  {name:<70} {cores * args.cameras:>6.2f}")
    print(f"\nостальные стадии при кадре {w}x{h}, {args.cameras} камер по {fps:g} кадров/с:")
    print(f"  {'стадия':<50} {'мс/кадр':>8} {'проц., мс':>10} {'ядер':>6}")
    total = 0.0
    for name, (wall, cpu) in clock.rows.items():
        cores = cpu * fps * args.cameras / 1000
        total += cores
        print(f"  {name:<50} {wall:>8.2f} {cpu:>10.2f} {cores:>6.2f}")
    print(f"  {'всего без декодирования':<50} {'':>8} {'':>10} {total:>6.2f}")


if __name__ == "__main__":
    main()
