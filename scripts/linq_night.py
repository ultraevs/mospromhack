#!/usr/bin/env python3
# ночной замер на стенде: ждёт указанного часа (стенд пустой) и гоняет probe/pipeline, всё складывает в одну папку.
# запуск через nohup setsid, переживает закрытие ядра jupyter. см. docs/LINQ.md
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MSK = dt.timezone(dt.timedelta(hours=3))


def sh(cmd: str, log) -> str:
    log.write(f"\n$ {cmd}\n")
    log.flush()
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=ROOT)
    out = r.stdout + r.stderr
    log.write(out)
    log.flush()
    return r.stdout


def snapshot(out: Path, name: str, log) -> None:
    info = {"time_msk": dt.datetime.now(MSK).isoformat(timespec="seconds"),
            "uptime": sh("uptime", log).strip(),
            "cpu_stat": sh("cat /sys/fs/cgroup/cpu.stat", log),
            "top": sh("ps aux --sort=-%cpu | head -6 | cut -c1-110 | sed -E 's/hackathon[0-9]+/<login>/g'", log)}
    (out / f"{name}.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--at", default="05:00", help="час:мин по мск, когда начинать; now — сразу")
    ap.add_argument("--inflight", type=int, default=2)
    ap.add_argument("--cams", default="1 2 4 6 8")
    ap.add_argument("--seconds", type=int, default=60)
    args = ap.parse_args()

    if args.at != "now":
        h, m = map(int, args.at.split(":"))
        now = dt.datetime.now(MSK)
        start = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if start <= now:
            start += dt.timedelta(days=1)
        print(f"жду до {start.isoformat(timespec='minutes')} мск", flush=True)
        time.sleep((start - now).total_seconds())

    tag = dt.datetime.now(MSK).strftime("%m%d_%H%M")
    out = ROOT / "data" / "linq" / f"night_{tag}"
    out.mkdir(parents=True, exist_ok=True)
    py = sys.executable
    with open(out / "log.txt", "w", encoding="utf-8") as log:
        snapshot(out, "before", log)
        for model in ("ppe", "people", "hazard"):
            for b in (1, 4, 8):
                (out / f"probe_{model}_b{b}.txt").write_text(
                    sh(f"{py} scripts/linq_probe.py --model {model} --n 10 --batch {b}", log), encoding="utf-8")
        for b in (4, 8):
            (out / f"probe_par_b{b}.txt").write_text(sh(f"{py} scripts/linq_probe_par.py --batch {b}", log),
                                                     encoding="utf-8")
        videos = "--videos factory_floor_sub.mp4 warehouse_sub.mp4"
        sh(f"{py} scripts/linq_pipeline.py --cams {args.cams} --seconds {args.seconds} --inflight {args.inflight} "
           f"{videos} --tag night_{tag}_sub", log)
        sh(f"{py} scripts/linq_pipeline.py --cams 1 2 4 --seconds {args.seconds} --inflight {args.inflight} "
           f"--tag night_{tag}_full", log)
        snapshot(out, "after", log)
    print(f"готово: {out}", flush=True)


if __name__ == "__main__":
    main()
