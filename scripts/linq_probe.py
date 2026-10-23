#!/usr/bin/env python3
# где теряется время в облачном вызове: упаковка, сериализация, сеть/очередь, tpu, распаковка
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
    ap.add_argument("--model", default="ppe")
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--batch", type=int, default=1)
    ap.add_argument("--login", default=os.environ.get("LINQ_LOGIN", ""))
    args = ap.parse_args()
    spec = load_model_spec(ROOT / "models" / args.model)
    frames = np.load(ROOT / "data" / "linq" / f"ref_{args.model}.npz")["frames"]
    x = np.stack([preprocess(f, spec.input_size)[0] for f in frames[:args.batch]])
    for raw in (False, True):
        det = LinqDetector({args.model: spec}, transport="cloud", program_dir=f"/auto/cache/{args.login}",
                           batch=args.batch, raw=raw)
        def prep():
            if not raw:
                return x
            return np.stack([det.prepare(args.model, f)[0] for f in frames[:args.batch]])

        det.infer(args.model, prep())
        det.timings[args.model] = dict.fromkeys(det.timings[args.model], 0.0)
        t0 = time.perf_counter()
        prep_ms = 0.0
        for _ in range(args.n):
            tp = time.perf_counter()
            batch = prep()
            prep_ms += time.perf_counter() - tp
            det.infer(args.model, batch)
        wall = (time.perf_counter() - t0) * 1000 / args.n / args.batch
        t = det.timings[args.model]
        k = 1 / t["frames"]
        print(f"raw={raw}: на кадр {wall:.0f} мс | prepare {prep_ms * 1000 * k:.1f} | упаковка {t['pack_ms'] * k:.1f} | облако {t['roundtrip_ms'] * k:.0f} "
              f"(из них tpu {t['tpu_ms'] * k:.1f}) | хвост+распаковка {t['tail_ms'] * k:.1f}")
        det.close()


if __name__ == "__main__":
    main()
