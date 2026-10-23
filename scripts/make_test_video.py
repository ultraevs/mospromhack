#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.config import get_settings, merged_dir  # noqa: E402


def main() -> int:
    settings = get_settings()
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=3.0, help="сколько держится одна картинка")
    ap.add_argument("--fps", type=int, default=10)
    ap.add_argument("--limit", type=int, default=0, help="взять только первые N картинок, 0 = все")
    ap.add_argument("--out", type=Path, default=settings.data_dir / "videos" / "css_test.mp4")
    args = ap.parse_args()

    if not shutil.which("ffmpeg"):
        print("ffmpeg не найден в PATH. Установка: winget install Gyan.FFmpeg (или choco install ffmpeg)")
        return 1
    src = merged_dir(settings) / "ppe" / "images" / "test"
    images = sorted(src.glob("css_*"))
    if args.limit:
        images = images[:args.limit]
    if not images:
        print(f"нет картинок css_* в {src}; проверь MERGED_DIR в .env")
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        listing = Path(tmp) / "list.txt"
        lines = []
        for img in images:
            lines += [f"file '{img.as_posix()}'", f"duration {args.seconds}"]
        lines.append(f"file '{images[-1].as_posix()}'")
        listing.write_text("\n".join(lines), encoding="utf-8")
        # concat хочет последний файл еще раз
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
               "-vf", f"fps={args.fps},scale=trunc(iw/2)*2:trunc(ih/2)*2", "-c:v", "libx264", "-bf", "0",
               "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(args.out)]
        subprocess.run(cmd, check=True)
    print(f"{args.out}: {len(images)} картинок по {args.seconds} с, {len(images) * args.seconds:.0f} с всего")
    return 0


if __name__ == "__main__":
    sys.exit(main())
