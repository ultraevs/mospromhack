#!/usr/bin/env python3
# событийная метрика: события воркера (data/linq/events_*.json от linq_pipeline --once) против интервалов глазами (data/event_gt/*.yaml)
# событие верное, если попало в интервал того же типа [start - pre, end + tol]. на интервал засчитывается count событий (людей), лишние — дубли.
# в спорном интервале событие не верное и не ложное, просто не считается
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parent.parent


def match_video(gt: dict, events: list[dict], tol: float, pre: float) -> dict:
    ivs = [{**iv, "left": int(iv.get("count", 1)), "hits": []} for iv in gt["intervals"]]
    rows = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0, "dup": 0, "ignored": 0, "delays": []})
    detail = []
    for e in sorted(events, key=lambda e: e["t_video"] if e["t_video"] is not None else 1e9):
        t, typ = e["t_video"], e["type"]
        hit = [iv for iv in ivs if iv["type"] == typ and t is not None and iv["start_s"] - pre <= t <= iv["end_s"] + tol]
        real = [iv for iv in hit if not iv.get("disputed")]
        free = [iv for iv in real if iv["left"] > 0]
        r = rows[typ]
        if free:
            iv = min(free, key=lambda iv: iv["start_s"])
            iv["left"] -= 1
            iv["hits"].append(t)
            r["tp"] += 1
            r["delays"].append(max(0.0, t - iv["start_s"]))
            verdict = "tp"
        elif real:
            r["dup"] += 1
            verdict = "dup"
        elif hit:
            r["ignored"] += 1
            verdict = "ignored"
        else:
            r["fp"] += 1
            verdict = "fp"
        detail.append({"type": typ, "t": t, "track": e.get("track_id"), "verdict": verdict})
    for iv in ivs:
        if not iv.get("disputed"):
            rows[iv["type"]]["fn"] += iv["left"]
    return {"rows": dict(rows), "events": detail,
            "intervals": [{k: iv[k] for k in ("type", "start_s", "end_s", "hits") if k in iv} | {"missed": iv["left"]}
                          for iv in ivs if not iv.get("disputed")]}


def metrics(r: dict, hours: float) -> dict:
    tp, fp, fn = r["tp"], r["fp"], r["fn"]
    d = r["delays"]
    return {"tp": tp, "fp": fp, "fn": fn, "dup": r["dup"], "ignored": r["ignored"],
            "precision": round(tp / (tp + fp), 3) if tp + fp else None,
            "recall": round(tp / (tp + fn), 3) if tp + fn else None,
            "delay_p50_s": round(float(np.median(d)), 1) if d else None,
            "fp_per_hour": round(fp / hours, 1) if hours else None}


def fmt(v):
    return "-" if v is None else v


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", nargs="+", default=["data/linq/events_gt.json", "data/linq/events_gt_fall.json"])
    ap.add_argument("--gt", default="data/event_gt")
    ap.add_argument("--tol", type=float, default=5, help="допуск после конца интервала, с")
    ap.add_argument("--pre", type=float, default=1, help="допуск до начала интервала, с (неточность времени кадра)")
    args = ap.parse_args()

    runs = {}
    for p in args.events:
        for run in json.loads((ROOT / p).read_text(encoding="utf-8")):
            runs[run["video"]] = run
    per_video, total, skipped = {}, defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0, "dup": 0, "ignored": 0, "delays": []}), {}
    hours = 0.0
    for f in sorted((ROOT / args.gt).glob("*.yaml")):
        gt = yaml.safe_load(f.read_text(encoding="utf-8"))
        run = runs.get(gt["video"])
        if run is None:
            skipped[gt["video"]] = "нет прогона"
            continue
        if gt.get("excluded"):
            skipped[gt["video"]] = f"исключён из счёта, событий воркера {len(run['events'])}"
            continue
        if sorted(gt.get("required_ppe", [])) != sorted(run.get("required_ppe", [])):
            print(f"! {gt['video']}: сиз в разметке {gt.get('required_ppe')} != в прогоне {run.get('required_ppe')}")
        res = match_video(gt, run["events"], args.tol, args.pre)
        h = run["duration"] / 3600
        hours += h
        for typ, r in res["rows"].items():
            for k in ("tp", "fp", "fn", "dup", "ignored"):
                total[typ][k] += r[k]
            total[typ]["delays"] += r["delays"]
        per_video[gt["video"]] = {"duration_s": run["duration"], "proc_fps": run.get("proc_fps"),
                                  "types": {t: metrics(r, h) for t, r in res["rows"].items()},
                                  "events": res["events"], "intervals": res["intervals"]}
    all_r = {"tp": 0, "fp": 0, "fn": 0, "dup": 0, "ignored": 0, "delays": []}
    for r in total.values():
        for k in all_r:
            all_r[k] += r[k]
    report = {"tol_s": args.tol, "pre_s": args.pre, "hours": round(hours, 4), "videos": per_video, "skipped": skipped,
              "total": {t: metrics(r, hours) for t, r in total.items()}, "all": metrics(all_r, hours)}
    out = ROOT / args.gt / "report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("| ролик | тип | TP | FP | FN | дубли | precision | recall | задержка p50, с | ложных в час |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for v, pv in per_video.items():
        for t, m in sorted(pv["types"].items()):
            print(f"| {v} | {t} | {m['tp']} | {m['fp']} | {m['fn']} | {m['dup']} | {fmt(m['precision'])} | "
                  f"{fmt(m['recall'])} | {fmt(m['delay_p50_s'])} | {fmt(m['fp_per_hour'])} |")
    for t, m in sorted(report["total"].items()):
        print(f"| **всего** | {t} | {m['tp']} | {m['fp']} | {m['fn']} | {m['dup']} | {fmt(m['precision'])} | "
              f"{fmt(m['recall'])} | {fmt(m['delay_p50_s'])} | {fmt(m['fp_per_hour'])} |")
    m = report["all"]
    print(f"| **всего** | все | {m['tp']} | {m['fp']} | {m['fn']} | {m['dup']} | {fmt(m['precision'])} | "
          f"{fmt(m['recall'])} | {fmt(m['delay_p50_s'])} | {fmt(m['fp_per_hour'])} |")
    for v, why in skipped.items():
        print(f"\n{v}: {why}")
    print(f"\nвидео {hours * 3600:.0f} с, отчет: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
