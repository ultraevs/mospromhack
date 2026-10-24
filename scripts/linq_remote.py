#!/usr/bin/env python3
from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from linq_stand import Stand  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PACKETS = ROOT / "hack_materials" / "sdk" / "tpu_framework" / "packets"
BASE = "linq"
REPO = f"{BASE}/repo"
ENV = "~/work/linq/sdkenv"
PY = f"{ENV}/bin/python"
CODE = ["scripts/linq_calib.py", "scripts/linq_compile.py", "scripts/linq_eval.py", "scripts/eval_map.py",
        "scripts/linq_bench.py", "scripts/linq_cams.py", "scripts/linq_bridge.py", "scripts/linq_probe.py", "scripts/linq_pipeline.py", "scripts/linq_probe_par.py", "scripts/linq_night.py", "scripts/linq_warmup.py",
        "worker/__init__.py", "worker/app", "shared"]
MODEL_FILES = ["model.yaml", "best.onnx"]
RESULTS = ["meta.yaml", "strides.npy", "thresholds.json", "scales.json"]


def stream(stand: Stand, cmd: str) -> bool:
    code = ("import subprocess, sys\n"
            f"p = subprocess.Popen({cmd!r}, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,"
            " executable='/bin/bash', bufsize=1)\n"
            "for line in p.stdout: print(line, end='', flush=True)\n"
            "print(f'[exit {p.wait()}]')\n"
            "sys.exit(p.returncode) if p.returncode else None")
    return stand.run(code)


def remote_size(stand: Stand, path: str) -> int | None:
    r = stand.http.get(f"{stand.api}/contents/{path}", params={"content": 0}, timeout=30)
    return r.json().get("size") if r.ok else None


def put(stand: Stand, local: Path, remote: str) -> None:
    if remote_size(stand, remote) == local.stat().st_size:
        return
    stand.upload(local, remote)


def setup(stand: Stand) -> None:
    for whl in sorted(PACKETS.iterdir()):
        put(stand, whl, f"{BASE}/sdk/packets/{whl.name}")
    stream(stand, f"set -e; cd ~/work/linq/sdk/packets; [ -x {PY} ] || mamba create -y -q -p {ENV} python=3.12.11 >/dev/null; "
                  f"{PY} -m pip install -q -U pip; "
                  f"{PY} -m pip install -q torch==2.9.1 torchvision==0.24.1 --index-url https://download.pytorch.org/whl/cpu; "
                  f"{PY} -m pip install -q -r requirements.txt 'opencv-python-headless==4.10.0.84' pyyaml 'numpy==1.26.4' "
                  "pydantic pydantic-settings 2>&1 | grep -v -i warn || true; "
                  f"{PY} -c 'import tpu_framework, torch, tensorflow as tf; print(\"sdk ok, torch\", torch.__version__, \"tf\", tf.__version__)'")


def sync(stand: Stand, models: list[str]) -> None:
    for item in CODE:
        path = ROOT / item
        files = [f for f in path.rglob("*.py") if "tests" not in f.parts] if path.is_dir() else [path]
        for f in files:
            put(stand, f, f"{REPO}/{f.relative_to(ROOT).as_posix()}")
    for m in models:
        for name in MODEL_FILES:
            put(stand, ROOT / "models" / m / name, f"{REPO}/models/{m}/{name}")
    stream(stand, f"cd ~/work/{REPO} && mkdir -p data && ln -sfn ~/work/{BASE}/app/data/videos data/videos && ls data/videos")


def pick_val(images_dir: Path, limit: int) -> list[Path]:
    images = sorted(images_dir.iterdir())
    return images[:: max(1, len(images) // limit)][:limit]


def data(stand: Stand, merged: Path, models: list[str], n_calib: int, n_val: int) -> None:
    from linq_calib import dataset_images

    for m in models:
        picked = {"train": dataset_images(merged / m / "images" / "train", n_calib, random.Random(0)),
                  "val": pick_val(merged / m / "images" / "val", n_val)}
        for split, images in picked.items():
            print(f"{m} {split}: {len(images)} картинок")
            for img in images:
                put(stand, img, f"{BASE}/merged/{m}/images/{split}/{img.name}")
                label = merged / m / "labels" / split / f"{img.stem}.txt"
                if label.exists():
                    put(stand, label, f"{BASE}/merged/{m}/labels/{split}/{label.name}")


def shell(stand: Stand, cmd: str) -> bool:
    return stream(stand, f"cd ~/work/{REPO} && export PYTHONPATH=$PWD PYTHONUNBUFFERED=1 CUDA_VISIBLE_DEVICES=-1 "
                         f"TF_CPP_MIN_LOG_LEVEL=2 PATH={ENV}/bin:$PATH LINQ_LOGIN=$(whoami) && {cmd}")


def fetch(stand: Stand, models: list[str]) -> None:
    for m in models:
        for name in RESULTS:
            remote = f"{REPO}/models/{m}/linq/{name}"
            if remote_size(stand, remote) is not None:
                stand.download(remote, ROOT / "models" / m / "linq" / name)


def main() -> None:
    from shared.config import merged_dir

    ap = argparse.ArgumentParser(description="калибровка/компиляция/eval sdk linq на стенде, у себя ничего не считаем")
    ap.add_argument("--models", nargs="+", default=["ppe", "hazard"])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup", help="колеса sdk + окружение py3.12 в ~/work/linq/sdkenv")
    sub.add_parser("sync", help="код и onnx моделей в ~/work/linq/repo")
    p = sub.add_parser("data", help="выборка calib/val из MERGED_DIR в ~/work/linq/merged")
    p.add_argument("--n-calib", type=int, default=128)
    p.add_argument("--n-val", type=int, default=300)
    p = sub.add_parser("job", help='например: job "scripts/linq_eval.py --model ppe --merged ../merged"')
    p.add_argument("args")
    p = sub.add_parser("sh", help="shell в ~/work/linq/repo с окружением sdk")
    p.add_argument("command")
    sub.add_parser("fetch", help="meta.yaml и мелочь из models/<m>/linq обратно к себе")
    args = ap.parse_args()

    stand = Stand()
    if args.cmd == "setup":
        setup(stand)
    elif args.cmd == "sync":
        sync(stand, args.models)
    elif args.cmd == "data":
        data(stand, merged_dir(), args.models, args.n_calib, args.n_val)
    elif args.cmd == "job":
        sys.exit(0 if shell(stand, f"python {args.args}") else 1)
    elif args.cmd == "sh":
        sys.exit(0 if shell(stand, args.command) else 1)
    elif args.cmd == "fetch":
        fetch(stand, args.models)


if __name__ == "__main__":
    main()
