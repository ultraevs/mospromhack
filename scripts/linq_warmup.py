#!/usr/bin/env python3
# прогрев tpu перед защитой: после простоя программу выгружают, первый вызов медленный
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from worker.app.detector.linq import LinqDetector  # noqa: E402
from worker.app.model_spec import load_model_spec  # noqa: E402
from worker.app.postprocess import preprocess  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=5, help="сколько пачек на модель")
    ap.add_argument("--batch", type=int, default=1)
    ap.add_argument("--login", default=os.environ.get("LINQ_LOGIN", ""))
    args = ap.parse_args()
    names = sorted(p.parent.parent.name for p in (ROOT / "models").glob("*/linq/meta.yaml"))
    for name in names:
        spec = load_model_spec(ROOT / "models" / name)
        ref = ROOT / "data" / "linq" / f"ref_{name}.npz"
        frames = np.load(ref if ref.exists() else ROOT / "data" / "linq" / "ref_ppe.npz")["frames"]
        x = np.stack([preprocess(f, spec.input_size)[0] for f in frames[:args.batch]])
        det = LinqDetector({name: spec}, transport="cloud", program_dir=f"/auto/cache/{args.login}", batch=args.batch)
        times = []
        for _ in range(args.n):
            t0 = time.perf_counter()
            det.infer(name, x)
            times.append((time.perf_counter() - t0) * 1000)
        print(f"{name}: первый {times[0]:.0f} мс, последний {times[-1]:.0f} мс ({args.n} пачек по {args.batch})", flush=True)
        det.close()


if __name__ == "__main__":
    main()
