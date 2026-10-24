#!/usr/bin/env python3
# автопилот после обучения модели голов: ждёт DONE, пороги на val, сравнение со старой ppe на нашем эталоне,
# и только если лучше — в models/heads и компиляция под linq. лог в data/heads_candidate/after.log
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRAIN = Path("D:/hak/ppe-train/ppe-train")
STAGE = ROOT / "data" / "heads_candidate"


def sh(cmd: list[str], log, cwd: Path | None = None) -> int:
    log.write(f"\n$ {' '.join(cmd)}\n")
    log.flush()
    r = subprocess.run(cmd, cwd=cwd or ROOT, stdout=log, stderr=subprocess.STDOUT)
    log.write(f"[exit {r.returncode}]\n")
    log.flush()
    return r.returncode


def better(report: dict) -> tuple[bool, str]:
    # кандидат = лучший из heads / heads_tiles; сравниваем с лучшим из ppe / ppe_tiles
    def key(v):
        a = report[v]["all"]
        fv, fn = (int(x) for x in a["false_violations"].split("/"))
        return a["correct_of_all"], -fv
    old = max(("ppe", "ppe_tiles"), key=key)
    new = max(("heads", "heads_tiles"), key=key)
    o, n = report[old]["all"], report[new]["all"]
    fv_o, fv_n = (int(x["false_violations"].split("/")[0]) for x in (o, n))
    ok = n["correct_of_all"] >= o["correct_of_all"] + 0.05 and fv_n <= fv_o
    return ok, f"{old} {o} -> {new} {n}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="heads")
    ap.add_argument("--force", action="store_true", help="поставить в models/heads без проверки")
    args = ap.parse_args()
    run = TRAIN / "runs" / args.run
    STAGE.mkdir(parents=True, exist_ok=True)
    with open(STAGE / "after.log", "a", encoding="utf-8") as log:
        log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} жду {run / 'DONE'}\n")
        log.flush()
        while not (run / "DONE").exists():
            time.sleep(60)
        train_py = TRAIN / ".venv-train" / "Scripts" / "python.exe"
        if sh([str(train_py), "evaluate.py", args.run], log, cwd=TRAIN):
            return
        shutil.copy(run / "weights" / "best.onnx", STAGE / "best.onnx")
        # прогон может называться heads2 и т.п., а воркер ищет модель по имени heads
        import yaml
        spec = yaml.safe_load((run / "model.yaml").read_text(encoding="utf-8"))
        spec["model"] = "heads"
        (STAGE / "model.yaml").write_text(yaml.safe_dump(spec, allow_unicode=True, sort_keys=False), encoding="utf-8")
        py = sys.executable
        if sh([py, "scripts/heads_gt.py", "eval", "--heads", str(STAGE)], log):
            return
        report = json.loads((ROOT / "data" / "heads_gt" / "eval.json").read_text(encoding="utf-8"))
        ok, why = better(report)
        log.write(f"\nсравнение: {why}\nпринимаем: {ok or args.force}\n")
        log.flush()
        if not (ok or args.force):
            return
        dst = ROOT / "models" / "heads"
        dst.mkdir(parents=True, exist_ok=True)
        for f in ("best.onnx", "model.yaml"):
            shutil.copy(STAGE / f, dst / f)
        # linq: калибровка и компиляция в контейнере sdk, как для остальных моделей
        merged = TRAIN / "merged"
        docker = ["docker", "run", "--rm", "-v", f"{ROOT}:/repo", "-v", f"{merged}:/merged:ro", "-w", "/repo", "linq-sdk"]
        sh(docker + ["python", "scripts/linq_calib.py", "--model", "heads", "--images", "/merged/heads/images/train",
                     "--videos", "data/videos"], log)
        sh(docker + ["python", "scripts/linq_compile.py", "--model", "heads", "--preset", "o1",
                     "--calib", "data/linq/calib_heads.npy", "--n-calib", "64"], log)
        sh(docker + ["python", "scripts/linq_eval.py", "--model", "heads", "--merged", "/merged", "--limit", "300"], log)
        log.write(f"\n=== готово {time.strftime('%H:%M:%S')}\n")


if __name__ == "__main__":
    main()
