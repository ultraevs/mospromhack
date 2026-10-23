from __future__ import annotations

import subprocess
import time
from pathlib import Path

# общая точка отсчёта, чтобы зацикленные файлы на разных машинах шли кадр в кадр
SYNC_EPOCH_S: float = 1792700000.0


def loop_offset(duration_s: float, now: float | None = None) -> float:
    if duration_s <= 0:
        return 0.0
    return ((now if now is not None else time.time()) - SYNC_EPOCH_S) % duration_s


def probe_duration(path: str | Path) -> float | None:
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)]
    try:
        d = float(subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout.strip())
        return d if d > 0 else None
    except Exception:  # noqa: BLE001
        return None
