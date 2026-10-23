from __future__ import annotations

import re
from pathlib import Path

SNAPSHOT = "snapshot.jpg"
CLEAN = "clean.jpg"
CLIP = "clip.mp4"

_SAFE_ID = re.compile(r"^evt_[0-9A-Z]{10}$")
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_CLIP_BYTES = 80 * 1024 * 1024
# клипы тяжелые, на диске держим только последние, стоп-кадры не трогаем
KEEP_CLIPS = 10


def event_dir(media_root: Path, event_id: str) -> Path:
    if not _SAFE_ID.match(event_id):
        raise ValueError(f"недопустимый id события: {event_id!r}")
    return media_root / "events" / event_id


def save(media_root: Path, event_id: str, name: str, data: bytes) -> str:
    folder = event_dir(media_root, event_id)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(data)
    return f"/media/events/{event_id}/{name}"


def path_for_url(media_root: Path, url: str | None) -> Path | None:
    if not url or not url.startswith("/media/"):
        return None
    path = (media_root / url.removeprefix("/media/")).resolve()
    return path if media_root.resolve() in path.parents and path.is_file() else None
