#!/usr/bin/env python3
from __future__ import annotations

import argparse
import random
import sys
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from worker.app.crops import tile_spans  # noqa: E402
from worker.app.model_spec import load_model_spec  # noqa: E402
from worker.app.postprocess import letterbox  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
VIDEO_EXT = {".mp4", ".avi", ".mkv", ".mov"}


def dataset_images(images_dir: Path, count: int, rng: random.Random) -> list[Path]:
    by_source: dict[str, list[Path]] = defaultdict(list)
    for path in sorted(images_dir.iterdir()):
        if path.suffix.lower() in {".jpg", ".jpeg", ".png"}:
            by_source[path.name.split("_", 1)[0]].append(path)
    if not by_source:
        raise SystemExit(f"в {images_dir} нет картинок")
    per_source = count // len(by_source)
    picked = []
    for paths in by_source.values():
        picked += rng.sample(paths, min(per_source, len(paths)))
    return picked


def video_frames(videos_dir: Path, count: int) -> list[np.ndarray]:
    videos = sorted(p for p in videos_dir.iterdir() if p.suffix.lower() in VIDEO_EXT)
    if not videos:
        raise SystemExit(f"в {videos_dir} нет роликов")
    frames = []
    per_video = max(1, count // len(videos))
    for video in videos:
        cap = cv2.VideoCapture(str(video))
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        for k in range(per_video):
            cap.set(cv2.CAP_PROP_POS_FRAMES, round((k + 0.5) * total / per_video))
            ok, image = cap.read()
            if ok:
                frames.append(image)
        cap.release()
    return frames


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, help="ppe | hazard")
    ap.add_argument("--images", required=True, type=Path, help="папка с картинками, например merged/ppe/images/train")
    ap.add_argument("--videos", type=Path, help="папка с роликами для кадров из реальных сцен")
    ap.add_argument("--n-images", type=int, default=128)
    ap.add_argument("--n-video", type=int, default=64)
    ap.add_argument("--tiles", action="store_true", help="кадры роликов резать на плитки, как их видит модель голов")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "linq")
    args = ap.parse_args()

    spec = load_model_spec(ROOT / "models" / args.model)
    rng = random.Random(args.seed)
    paths = dataset_images(args.images, args.n_images, rng)
    frames = [cv2.imread(str(p)) for p in paths]
    if args.videos:
        video = video_frames(args.videos, args.n_video)
        if args.tiles:
            video = [f[:, x1:x2] for f in video for x1, x2 in tile_spans(f.shape[1], f.shape[0])]
        frames += video

    batch = np.stack([letterbox(f, spec.input_size)[0] for f in frames if f is not None])
    args.out.mkdir(parents=True, exist_ok=True)
    out = args.out / f"calib_{args.model}.npy"
    np.save(out, batch)
    print(f"{out}: {batch.shape} uint8 BGR, картинок {len(paths)}, кадров из видео {len(batch) - len(paths)}")


if __name__ == "__main__":
    main()
