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


# зацикленный файл с позиции offset. входной -ss вместе с -stream_loop на стыке сбрасывает время и -re встает секунд на 20,
# поэтому режем на выходе, а до offset читаем без ограничения скорости
def loop_input(path: str | Path, offset: float | None) -> list[str]:
    if not offset:
        return ["-re", "-stream_loop", "-1", "-i", str(path)]
    return ["-readrate", "1", "-readrate_initial_burst", f"{offset + 1:.3f}", "-stream_loop", "-1", "-i", str(path),
            "-ss", f"{offset:.3f}"]


def probe_duration(path: str | Path) -> float | None:
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)]
    try:
        d = float(subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout.strip())
        return d if d > 0 else None
    except Exception:  # noqa: BLE001
        return None
