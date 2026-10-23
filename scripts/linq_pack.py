#!/usr/bin/env python3
from __future__ import annotations

import argparse
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CODE = [
    "scripts/linq_bench.py",
    "scripts/linq_cams.py",
    "worker/__init__.py",
    "worker/app/__init__.py",
    "worker/app/model_spec.py",
    "worker/app/postprocess.py",
    "worker/app/detector/__init__.py",
    "worker/app/detector/base.py",
    "worker/app/detector/linq.py",
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", default=["ppe", "hazard"])
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "linq" / "linq_stand.zip")
    args = ap.parse_args()

    files = [ROOT / p for p in CODE if (ROOT / p).exists()]
    for model in args.models:
        linq = ROOT / "models" / model / "linq"
        meta = linq / "meta.yaml"
        if not meta.exists():
            raise SystemExit(f"нет {meta}: сначала scripts/linq_compile.py --model {model}")
        files += [ROOT / "models" / model / "model.yaml", meta, *linq.glob("strides.npy")]
        ref = ROOT / "data" / "linq" / f"ref_{model}.npz"
        if ref.exists():
            files.append(ref)
        else:
            print(f"!!! нет {ref.name}: сверки с эталоном на стенде не будет (linq_eval.py --ref 16)")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(f, f.relative_to(ROOT).as_posix())
    size = args.out.stat().st_size / 2**20
    print(f"{args.out}: {len(files)} файлов, {size:.1f} МБ")
    for f in files:
        print("  " + f.relative_to(ROOT).as_posix())


if __name__ == "__main__":
    main()
