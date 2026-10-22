#!/usr/bin/env python3
import ctypes
import importlib.metadata as md
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

EXPECTED = {
    "ppe": (["person", "helmet", "no_helmet", "vest", "mask", "no_mask", "vehicle"], 9000),
    "hazard": (["fire", "smoke", "cigarette"], 8000),
}
PINNED = ["torch", "torchvision", "ultralytics", "onnx", "onnxslim", "tensorboard"]
MIN_FREE_DISK_GB = 5
MIN_FREE_VRAM_GB = 7
MIN_FREE_COMMIT_GB = 8

results = []


def check(name, ok, detail, fatal=True):
    results.append((ok, fatal))
    mark = "OK  " if ok else ("FAIL" if fatal else "WARN")
    print(f"[{mark}] {name}: {detail}")


def free_commit_gb():
    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    m = MEMORYSTATUSEX()
    m.dwLength = ctypes.sizeof(m)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return m.ullAvailPageFile / 2**30, m.ullAvailPhys / 2**30


def sleep_timeout_ac():
    out = subprocess.run(["powercfg", "/query", "SCHEME_CURRENT", "SUB_SLEEP", "STANDBYIDLE"],
                         capture_output=True, text=True, encoding="cp866", errors="ignore").stdout
    vals = [l.split(":")[-1].strip() for l in out.splitlines() if "0x" in l and ":" in l]
    return int(vals[-2], 16) if len(vals) >= 2 else None


def main():
    models = sys.argv[1:] or list(EXPECTED)

    versions = {p: md.version(p) for p in PINNED}
    check("пакеты", True, ", ".join(f"{k} {v}" for k, v in versions.items()))
    pinned = {}
    for line in Path("requirements.txt").read_text(encoding="utf-8").splitlines():
        if "==" in line and not line.startswith("#"):
            k, v = line.strip().split("==")
            pinned[k.lower()] = v
    drift = [f"{k}: стоит {versions[k]}, проверено на {pinned[k]}"
             for k in versions if k in pinned and versions[k].split("+")[0] != pinned[k]]
    check("версии как в requirements.txt", not drift, "; ".join(drift) or "совпадают", fatal=False)

    import torch
    cuda = torch.cuda.is_available()
    check("CUDA", cuda, torch.cuda.get_device_name(0) if cuda else "PyTorch не видит видеокарту")
    if cuda:
        free, total = (x / 2**30 for x in torch.cuda.mem_get_info())
        check("свободная видеопамять", free >= MIN_FREE_VRAM_GB,
              f"{free:.1f} из {total:.1f} ГБ (нужно от {MIN_FREE_VRAM_GB})")

    commit, ram = free_commit_gb()
    check("свободная виртуальная память", commit >= MIN_FREE_COMMIT_GB,
          f"{commit:.1f} ГБ (нужно от {MIN_FREE_COMMIT_GB}; мало -> закрой браузер, иначе WinError 1455), RAM свободно {ram:.1f} ГБ")

    disk = shutil.disk_usage(".").free / 2**30
    check("свободный диск", disk >= MIN_FREE_DISK_GB, f"{disk:.1f} ГБ (нужно от {MIN_FREE_DISK_GB})")

    for m in models:
        names, min_train = EXPECTED[m]
        y = Path("merged") / m / "data.yaml"
        if not y.exists():
            check(f"датасет {m}", False, f"нет {y}: запусти python merge.py configs/{m}.yaml")
            continue
        data = yaml.safe_load(y.read_text(encoding="utf-8"))
        got = [data["names"][k] for k in sorted(data["names"])]
        check(f"классы {m}", got == names, ", ".join(got))
        root = Path(data["path"])
        n_img = len(list((root / "images" / "train").glob("*")))
        n_lbl = len(list((root / "labels" / "train").glob("*.txt")))
        n_val = len(list((root / "images" / "val").glob("*")))
        check(f"данные {m}", n_img >= min_train and n_img == n_lbl and n_val > 0,
              f"train {n_img} картинок / {n_lbl} разметок, val {n_val}")
        stale = Path("runs") / m
        check(f"папка runs/{m} свободна", not stale.exists(),
              "свободна" if not stale.exists() else "уже есть прошлый прогон: он будет перезаписан (или используй --resume)",
              fatal=False)

    import psutil
    running = False
    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            if "python" in (p.info["name"] or "").lower() and any("train.py" in a for a in p.info["cmdline"] or []):
                running = True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    check("другое обучение не идёт", not running, "нет" if not running else "train.py уже запущен")

    t = sleep_timeout_ac()
    check("сон Windows отключён", t == 0, "отключён" if t == 0 else f"заснёт через {t} с простоя: отключи в настройках питания",
          fatal=False)

    failed = sum(1 for ok, fatal in results if not ok and fatal)
    warned = sum(1 for ok, fatal in results if not ok and not fatal)
    print(f"\nИтог: {'МОЖНО ЗАПУСКАТЬ' if not failed else 'ЗАПУСКАТЬ НЕЛЬЗЯ'}"
          f" (ошибок {failed}, предупреждений {warned})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
