#!/usr/bin/env python3
# отложенный набор: прогон прод-конфига без изменений (linq_pipeline --once, onnx локально) по eval/holdout + event_eval
# сиз у роликов разные, поэтому гоняем группами по required_ppe и склеиваем события
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gt", default="eval/holdout")
    ap.add_argument("--detector", default="onnx")
    ap.add_argument("--tag", default="local")
    ap.add_argument("--skip-run", action="store_true", help="только пересчитать по готовым событиям")
    args = ap.parse_args()
    gt_dir = ROOT / args.gt
    out = gt_dir / "runs" / f"events_{args.tag}.json"

    if not args.skip_run:
        groups = defaultdict(list)
        for f in sorted(gt_dir.glob("*.yaml")):
            g = yaml.safe_load(f.read_text(encoding="utf-8"))
            groups[tuple(g.get("required_ppe", []))].append(g["video"])
        runs = []
        for i, (ppe, videos) in enumerate(groups.items()):
            tag = f"holdout_{args.tag}_{i}"  # свой тег, чтобы не затереть прогоны абляции
            cmd = [sys.executable, "scripts/linq_pipeline.py", "--once", "--tiles", "--detector", args.detector,
                   "--tag", tag, "--videos", *videos, "--ppe", *ppe]
            print(" ".join(cmd), flush=True)
            subprocess.run(cmd, cwd=ROOT, check=True)
            runs += json.loads((ROOT / "data" / "linq" / f"events_{tag}.json").read_text(encoding="utf-8"))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(runs, ensure_ascii=False, indent=2), encoding="utf-8")

    subprocess.run([sys.executable, "scripts/event_eval.py", "--gt", args.gt, "--events", str(out.relative_to(ROOT))],
                   cwd=ROOT, check=True)
    # event_eval пишет report.json, держим рядом с тегом
    (gt_dir / "report.json").replace(gt_dir / f"report_{args.tag}.json")
    print(f"отчет: {args.gt}/report_{args.tag}.json")


if __name__ == "__main__":
    main()
