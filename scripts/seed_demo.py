"""Демо-история для аналитики: камеры « (демо)» и события за N дней.

python scripts/seed_demo.py --days 30
python scripts/seed_demo.py --clear        # убрать всё демо
python scripts/seed_demo.py --clear-live   # убрать НЕ демо события (мусор с отладки)
"""
from __future__ import annotations

import argparse
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from backend.app.db.models import CameraRow, EventRow  # noqa: E402
from backend.app.db.session import make_engine, make_session_factory  # noqa: E402
from backend.app.services.events import new_event_id  # noqa: E402
from shared.config import get_settings  # noqa: E402

SUFFIX = " (демо)"
NOTE = "[демо]"

CAMERAS = ["Цех 1, станки", "Цех 2, ворота", "Склад ГСМ", "Погрузочная рампа", "Проходная"]

# тип: (вес, критичность) — веса на глаз: СИЗ чаще всего, огонь/падения редко
TYPES = {
    "no_helmet": (30, "medium"), "no_vest": (20, "low"), "no_mask": (8, "low"),
    "zone_intrusion": (12, "medium"), "smoking": (6, "medium"), "running": (6, "low"),
    "vehicle_proximity": (7, "high"), "fall": (3, "high"), "lying_immobile": (2, "high"),
    "smoke": (3, "high"), "fire": (1, "high"),
}


def is_demo(row: EventRow) -> bool:
    return bool((row.details or {}).get("demo"))


def ensure_cameras(db: Session) -> list[CameraRow]:
    have = {c.name: c for c in db.execute(select(CameraRow).where(CameraRow.name.endswith(SUFFIX))).scalars()}
    out = []
    for name in CAMERAS:
        full = name + SUFFIX
        cam = have.get(full)
        if cam is None:
            # выключена и без файла — воркер её не возьмёт
            cam = CameraRow(name=full, source_type="file", source="", required_ppe=["helmet", "vest"], enabled=False)
            db.add(cam)
        out.append(cam)
    db.commit()
    return out


def _rate(t: datetime) -> float:
    # смены 8–20 основная масса, ночь и выходные тише
    r = 1.0 if 8 <= t.hour < 20 else 0.15
    return r * (0.3 if t.weekday() >= 5 else 1.0)


def seed(db: Session, days: int, now: datetime | None = None, rnd: random.Random | None = None) -> int:
    rnd = rnd or random.Random()
    now = (now or datetime.now(timezone.utc)).replace(tzinfo=None)
    cams = ensure_cameras(db)
    names, weights = list(TYPES), [w for w, _ in TYPES.values()]
    n = 0
    t = now - timedelta(days=days)
    while t < now:
        # ~1.5 события в рабочий час на всё предприятие
        for _ in range(sum(rnd.random() < _rate(t) * 0.5 for _ in range(3))):
            start = t + timedelta(seconds=rnd.randint(0, 3599))
            if start >= now:
                continue
            typ = rnd.choices(names, weights)[0]
            age_h = (now - start).total_seconds() / 3600
            x = rnd.random()
            if age_h < 24 and x < 0.6:
                review = "none"  # свежие ещё не разобраны
            elif x < 0.10:
                review = "false_positive"
            elif x < 0.25:
                review = "forwarded"
            elif x < 0.95:
                review = "confirmed"
            else:
                review = "none"
            cam = rnd.choice(cams)
            db.add(EventRow(
                id=new_event_id(), type=typ, severity=TYPES[typ][1], status="closed",
                camera_id=cam.id, camera_name=cam.name, track_id=rnd.randint(1, 900),
                started_at=start, ended_at=start + timedelta(seconds=rnd.randint(4, 90)),
                confidence=round(rnd.uniform(0.45, 0.95), 2), review=review,
                details={"demo": True, "note": NOTE},
            ))
            n += 1
        t += timedelta(hours=1)
    db.commit()
    return n


def clear_demo(db: Session) -> tuple[int, int]:
    cam_ids = list(db.execute(select(CameraRow.id).where(CameraRow.name.endswith(SUFFIX))).scalars())
    ev = [r.id for r in db.execute(select(EventRow)).scalars() if is_demo(r) or r.camera_id in cam_ids]
    if ev:
        db.execute(delete(EventRow).where(EventRow.id.in_(ev)))
    for cam in db.execute(select(CameraRow).where(CameraRow.id.in_(cam_ids))).scalars():
        db.delete(cam)
    db.commit()
    return len(ev), len(cam_ids)


def clear_live(db: Session) -> int:
    cam_ids = set(db.execute(select(CameraRow.id).where(CameraRow.name.endswith(SUFFIX))).scalars())
    ev = [r.id for r in db.execute(select(EventRow)).scalars() if not is_demo(r) and r.camera_id not in cam_ids]
    if ev:
        db.execute(delete(EventRow).where(EventRow.id.in_(ev)))
    db.commit()
    return len(ev)


def main() -> None:
    ap = argparse.ArgumentParser(description="демо-история для аналитики")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--clear", action="store_true", help="удалить демо-камеры и демо-события")
    ap.add_argument("--clear-live", action="store_true", help="удалить все НЕ демо события")
    a = ap.parse_args()
    s = get_settings()
    factory = make_session_factory(make_engine(s.data_dir / "db.sqlite"))  # та же база, что у backend
    with factory() as db:
        if a.clear_live:
            print(f"удалено настоящих событий: {clear_live(db)}")
        if a.clear:
            e, c = clear_demo(db)
            print(f"удалено демо: событий {e}, камер {c}")
        if not (a.clear or a.clear_live):
            print(f"добавлено демо-событий: {seed(db, a.days)} за {a.days} дн.")


if __name__ == "__main__":
    main()
