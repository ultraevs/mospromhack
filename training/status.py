#!/usr/bin/env python3
import csv
import json
import os
import subprocess
import sys
import time
from pathlib import Path

RUNS, LOGS = Path("runs"), Path("logs")
ERR = ("Traceback", "WinError", "out of memory", "RuntimeError", "Error:")


def training_process():
    import psutil
    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            cmd = p.info["cmdline"] or []
            if "python" in (p.info["name"] or "").lower() and "train.py" in " ".join(cmd):
                if "--run-name" in cmd:
                    return cmd[cmd.index("--run-name") + 1]
                i = next(k for k, a in enumerate(cmd) if a.endswith("train.py"))
                return cmd[i + 1] if len(cmd) > i + 1 else "?"
        except (psutil.NoSuchProcess, psutil.AccessDenied, StopIteration):
            pass
    return None


def gpu():
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True).stdout.strip()
        u, used, total, t = (x.strip() for x in out.split(","))
        return f"загрузка {u}%, память {int(used) / 1024:.1f}/{int(total) / 1024:.1f} ГБ, {t}°C"
    except Exception:
        return "нет данных"


def rows(path):
    with path.open(encoding="utf-8") as f:
        return [{k.strip(): v.strip() for k, v in r.items()} for r in csv.DictReader(f)]


def hms(sec):
    return f"{int(sec // 3600)} ч {int(sec % 3600 // 60):02d} мин"


def report_run(d, active):
    res = d / "results.csv"
    info = json.loads((d / "run_info.json").read_text(encoding="utf-8")) if (d / "run_info.json").exists() else {}
    total = info.get("args", {}).get("epochs", 80)
    state = "ГОТОВО" if (d / "DONE").exists() else ("ИДЁТ" if active else "ОСТАНОВЛЕНО")
    print(f"\n── {d.name}: {state} " + "─" * 40)
    if info:
        print(f"   старт {info['started']}, картинок train/val: {info['images'].get('train')}/{info['images'].get('val')}")
    if not res.exists():
        print("   первая эпоха ещё не закончилась")
        return
    r = rows(res)
    last = r[-1]
    f = lambda k: float(last[k])
    fit = [0.1 * float(x["metrics/mAP50(B)"]) + 0.9 * float(x["metrics/mAP50-95(B)"]) for x in r]
    best = max(range(len(fit)), key=fit.__getitem__)
    ep, elapsed = int(float(last["epoch"])), f("time")
    line = f"   эпоха {ep}/{total}, прошло {hms(elapsed)}"
    if state == "ИДЁТ" and ep < total:
        line += f", осталось до {hms(elapsed / ep * (total - ep))} (раньше, если сработает ранняя остановка)"
    print(line)
    print(f"   сейчас:  mAP50 {f('metrics/mAP50(B)'):.3f}  mAP50-95 {f('metrics/mAP50-95(B)'):.3f}  "
          f"P {f('metrics/precision(B)'):.3f}  R {f('metrics/recall(B)'):.3f}")
    print(f"   лучшая:  эпоха {int(float(r[best]['epoch']))}, mAP50 {float(r[best]['metrics/mAP50(B)']):.3f}  "
          f"mAP50-95 {float(r[best]['metrics/mAP50-95(B)']):.3f}; без улучшения {len(r) - 1 - best} эпох из 20 допустимых")
    print(f"   потери:  box {f('train/box_loss'):.3f}  cls {f('train/cls_loss'):.3f}  |  "
          f"val box {f('val/box_loss'):.3f}  cls {f('val/cls_loss'):.3f}")
    pc = d / "per_class.csv"
    if pc.exists():
        pr = [x for x in rows(pc) if x["epoch"] == str(ep)]
        seen = {}
        for x in pr:
            seen[x["class"]] = x
        print(f"   по классам (эпоха {ep}):")
        print(f"     {'класс':<12}{'P':>7}{'R':>7}{'mAP50':>8}{'mAP50-95':>10}")
        for c, x in seen.items():
            print(f"     {c:<12}{float(x['precision']):>7.3f}{float(x['recall']):>7.3f}"
                  f"{float(x['mAP50']):>8.3f}{float(x['mAP50-95']):>10.3f}")
    if (d / "weights" / "best.onnx").exists():
        print(f"   ONNX: {d / 'weights' / 'best.onnx'}")


def log_errors():
    logs = sorted(LOGS.glob("*_2*.log"), key=lambda p: p.stat().st_mtime) if LOGS.exists() else []
    if not logs:
        return
    text = logs[-1].read_text(encoding="utf-8", errors="replace").replace("\r", "\n")
    bad = [l.strip() for l in text.splitlines() if any(e in l for e in ERR)]
    print(f"\nПоследний лог: {logs[-1]}  ({'ошибок нет' if not bad else 'ОШИБКИ:'})")
    for l in bad[-5:]:
        print("   " + l[:200])


def show():
    active = training_process()
    print(time.strftime("%Y-%m-%d %H:%M:%S"),
          f"| обучение: {'идёт (' + active + ')' if active else 'не запущено'} | GPU: {gpu()}")
    j = LOGS / "run_all.log"
    if j.exists():
        print("Журнал:")
        for l in j.read_text(encoding="utf-8").splitlines()[-4:]:
            print("   " + l)
    if RUNS.exists():
        for d in sorted((p for p in RUNS.iterdir() if (p / "weights").exists() or (p / "run_info.json").exists()),
                        key=lambda p: p.stat().st_mtime):
            report_run(d, active is not None and d.name == active)
    log_errors()


if __name__ == "__main__":
    os.chdir(Path(__file__).resolve().parent)
    if "--watch" in sys.argv:
        i = sys.argv.index("--watch")
        every = int(sys.argv[i + 1]) if len(sys.argv) > i + 1 else 30
        while True:
            os.system("cls" if os.name == "nt" else "clear")
            show()
            print(f"\nобновление каждые {every} с, Ctrl+C для выхода. Графики: tensorboard --logdir runs")
            time.sleep(every)
    else:
        show()
