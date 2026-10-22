#!/usr/bin/env python3
import os
import subprocess
import sys
import time
from pathlib import Path

LOGS = Path("logs")


def journal(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
    print(line, flush=True)
    with (LOGS / "run_all.log").open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def main():
    os.chdir(Path(__file__).resolve().parent)
    LOGS.mkdir(exist_ok=True)
    args = sys.argv[1:]
    extra = args[args.index("--") + 1:] if "--" in args else []
    models = (args[:args.index("--")] if "--" in args else args) or ["ppe", "hazard"]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}

    journal(f"=== старт: {', '.join(models)} ===")
    pre = subprocess.run([sys.executable, "preflight.py", *models], env=env, capture_output=True,
                         text=True, encoding="utf-8", errors="replace")
    (LOGS / "preflight.log").write_text(pre.stdout + pre.stderr, encoding="utf-8")
    print(pre.stdout)
    if pre.returncode != 0:
        journal("проверка окружения НЕ пройдена, обучение не запущено (см. logs/preflight.log)")
        return 1
    journal("проверка окружения пройдена")

    stamp = time.strftime("%Y%m%d_%H%M")
    failed = []
    for m in models:
        log = LOGS / f"{m}_{stamp}.log"
        journal(f"{m}: обучение началось, лог {log}")
        t0 = time.time()
        with log.open("w", encoding="utf-8") as f:
            code = subprocess.run([sys.executable, "-u", "train.py", m, *extra], env=env,
                                  stdout=f, stderr=subprocess.STDOUT).returncode
        hours = (time.time() - t0) / 3600
        if code == 0:
            journal(f"{m}: готово за {hours:.2f} ч")
        else:
            failed.append(m)
            journal(f"{m}: УПАЛО с кодом {code} через {hours:.2f} ч, последние строки в {log}")
    journal(f"=== конец: {'всё готово' if not failed else 'упали: ' + ', '.join(failed)} ===")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
