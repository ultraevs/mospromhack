#!/usr/bin/env python3
# сколько стоит вызов, когда ppe и people летят в облако одновременно (как в воркере)
from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from worker.app.detector.linq import LinqDetector  # noqa: E402
from worker.app.model_spec import load_model_spec  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", default=["ppe", "people"])
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--n", type=int, default=8)
    args = ap.parse_args()
    specs = {m: load_model_spec(ROOT / "models" / m) for m in args.models}
    det = LinqDetector(specs, transport="cloud", program_dir=f"/auto/cache/{os.environ['LINQ_LOGIN']}",
                       batch=args.batch, raw=True)
    frames = np.load(ROOT / "data" / "linq" / "ref_ppe.npz")["frames"][:args.batch]
    x = np.stack([det.prepare("ppe", f)[0] for f in frames])
    for m in args.models:
        det.infer(m, x)

    def run(m, out):
        for _ in range(args.n):
            t0 = time.perf_counter()
            det.infer(m, x)
            out.append((time.perf_counter() - t0) * 1000)

    for together in ([args.models[0]], args.models):
        res = {m: [] for m in together}
        threads = [threading.Thread(target=run, args=(m, res[m])) for m in together]
        t0 = time.perf_counter()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        wall = time.perf_counter() - t0
        frames_done = args.n * args.batch * len(together)
        print(f"{'+'.join(together)} пачка {args.batch}: вызов " +
              ", ".join(f"{m} {np.mean(v):.0f} мс" for m, v in res.items()) +
              f" | всего {frames_done / wall:.1f} кадров/с")
    det.close()


if __name__ == "__main__":
    main()
