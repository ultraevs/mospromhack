#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from worker.app.detector.linq import LinqDetector  # noqa: E402
from worker.app.model_spec import load_model_spec  # noqa: E402
from worker.app.postprocess import decode, iou_matrix, preprocess  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def ms(values) -> dict[str, float]:
    a = np.asarray(values, dtype=np.float64)
    return {"mean": round(float(a.mean()), 1), "p50": round(float(np.percentile(a, 50)), 1),
            "p95": round(float(np.percentile(a, 95)), 1)}


def match_rate(a, b) -> float:
    if len(a) == 0:
        return 1.0 if len(b) == 0 else 0.0
    if len(b) == 0:
        return 0.0
    m = iou_matrix(a.xyxy, b.xyxy) * (a.cls[:, None] == b.cls[None, :])
    return float((m.max(axis=1) >= 0.5).mean())


def check(det: LinqDetector, model: str, spec, ref) -> dict:
    out, infos = [], []
    for frame in ref["frames"]:
        x, info = preprocess(frame, spec.input_size)
        out.append(det.infer(model, x[None])[0])
        infos.append(info)
    out = np.stack(out)
    vs_float = [match_rate(decode(f, spec, i), decode(o, spec, i)) for f, o, i in zip(ref["float32"], out, infos)]
    vs_sim = [match_rate(decode(s, spec, i), decode(o, spec, i)) for s, o, i in zip(ref["int8"], out, infos)]
    cls = slice(4, None)
    return {"frames": len(out),
            "max_abs_box_vs_sim": round(float(np.abs(out[:, :4] - ref["int8"][:, :4]).max()), 3),
            "max_abs_score_vs_sim": round(float(np.abs(out[:, cls] - ref["int8"][:, cls]).max()), 4),
            "det_match_vs_sim": round(float(np.mean(vs_sim)), 3),
            "det_match_vs_float": round(float(np.mean(vs_float)), 3)}


def timing(det: LinqDetector, model: str, spec, frames: np.ndarray, batch: int, n: int, pause: float) -> dict:
    x = np.stack([preprocess(f, spec.input_size)[0] for f in frames[:batch]])
    tpu, trip, per_frame = [], [], []
    for _ in range(max(1, n // batch)):
        before = dict(det.timings[model])
        t0 = time.perf_counter()
        det.infer(model, x)
        dt = (time.perf_counter() - t0) * 1000
        tpu.append((det.timings[model]["tpu_ms"] - before["tpu_ms"]) / batch)
        trip.append(dt)
        per_frame.append(dt / batch)
        time.sleep(pause)
    return {"batch": batch, "calls": len(trip), "tpu_ms_per_frame": ms(tpu), "call_ms": ms(trip),
            "wall_ms_per_frame": ms(per_frame), "fps": round(1000 / float(np.mean(per_frame)), 1)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", default=["ppe", "hazard"])
    ap.add_argument("--frames", type=int, default=32, help="кадров на каждый замер")
    ap.add_argument("--batches", type=int, nargs="+", default=[1, 4])
    ap.add_argument("--pause", type=float, default=0.3, help="пауза между вызовами, с — стенд общий")
    ap.add_argument("--login", default=os.environ.get("LINQ_LOGIN", ""))
    ap.add_argument("--broker", default="10.0.114.30")
    ap.add_argument("--tag", default="local", help="где гоняли: local (через vpn) или stand")
    args = ap.parse_args()
    if not args.login:
        raise SystemExit("нужен логин стенда: --login или LINQ_LOGIN")

    specs = {m: load_model_spec(ROOT / "models" / m) for m in args.models}
    det = LinqDetector(specs, transport="cloud", broker=args.broker, program_dir=f"/auto/cache/{args.login}",
                       batch=max(args.batches))
    report = {"tag": args.tag, "device": det.device.replace(args.login, "<login>"), "models": {}}
    try:
        for model, spec in specs.items():
            ref = np.load(ROOT / "data" / "linq" / f"ref_{model}.npz")
            det.infer(model, preprocess(ref["frames"][0], spec.input_size)[0][None])
            row = {"program": det.meta[model]["program"], "check": check(det, model, spec, ref),
                   "timing": [timing(det, model, spec, ref["frames"], b, args.frames, args.pause) for b in args.batches]}
            report["models"][model] = row
            print(f"\n## {model} ({row['program']})")
            print("сверка:", row["check"])
            print("| пачка | tpu, мс/кадр | вызов, мс | на кадр, мс | кадров/с |")
            print("|---|---|---|---|---|")
            for t in row["timing"]:
                print(f"| {t['batch']} | {t['tpu_ms_per_frame']['mean']} | {t['call_ms']['mean']} (p95 {t['call_ms']['p95']}) "
                      f"| {t['wall_ms_per_frame']['mean']} | {t['fps']} |")
    finally:
        det.close()

    out = ROOT / "data" / "linq" / f"bench_{args.tag}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nотчет: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
