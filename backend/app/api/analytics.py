from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.models import EventRow
from backend.app.state import get_db
from shared.schemas import EventType, Review, Severity

router = APIRouter(prefix="/api", tags=["analytics"])


def _naive(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def _review_counts(reviews: list[str]) -> dict:
    c = Counter(reviews)
    confirmed, false = c[Review.confirmed.value], c[Review.false_positive.value]
    marked = confirmed + false
    return {"total": len(reviews), "confirmed": confirmed, "false_positive": false, "unreviewed": c[Review.none.value],
            "forwarded": c[Review.forwarded.value],
            "false_share": round(false / marked, 3) if marked else None}


@router.get("/analytics/summary")
async def summary(
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = None,
    camera_id: int | None = None,
    tz_offset_min: int = Query(default=0, ge=-840, le=840, description="смещение местного времени от UTC, минуты"),
    db: Session = Depends(get_db),
) -> dict:
    conds = []
    if from_ is not None:
        conds.append(EventRow.started_at >= _naive(from_))
    if to is not None:
        conds.append(EventRow.started_at <= _naive(to))
    if camera_id is not None:
        conds.append(EventRow.camera_id == camera_id)
    rows = db.execute(select(EventRow.type, EventRow.severity, EventRow.camera_id, EventRow.camera_name, EventRow.zone_id,
                             EventRow.zone_name, EventRow.started_at, EventRow.review).where(*conds)).all()

    shift = timedelta(minutes=tz_offset_min)
    by_type: dict[str, list[str]] = {t.value: [] for t in EventType}
    by_camera: dict[int, list] = {}
    by_zone: dict[int, list] = {}
    hours, days = Counter(), Counter()
    for typ, _sev, cam_id, cam_name, zone_id, zone_name, started, review in rows:
        by_type.setdefault(typ, []).append(review)
        by_camera.setdefault(cam_id, [cam_name, 0])[1] += 1
        if zone_id is not None:
            by_zone.setdefault(zone_id, [zone_name, cam_name, 0])[2] += 1
        local = started + shift
        hours[local.hour] += 1
        days[local.date().isoformat()] += 1

    severity = Counter(r[1] for r in rows)
    return {
        "from": from_, "to": to, "camera_id": camera_id,
        **_review_counts([r[7] for r in rows]),
        "by_type": [{"type": t, **_review_counts(reviews)} for t, reviews in by_type.items()],
        "by_severity": [{"severity": s.value, "total": severity[s.value]} for s in (Severity.high, Severity.medium, Severity.low)],
        "by_camera": sorted(({"camera_id": cid, "camera_name": name, "total": n} for cid, (name, n) in by_camera.items()),
                            key=lambda x: -x["total"]),
        "by_zone": sorted(({"zone_id": zid, "zone_name": name, "camera_name": cam, "total": n}
                           for zid, (name, cam, n) in by_zone.items()), key=lambda x: -x["total"]),
        "without_zone": sum(1 for r in rows if r[4] is None),
        "by_hour": [{"hour": h, "total": hours[h]} for h in range(24)],
        "by_day": [{"day": d, "total": n} for d, n in sorted(days.items())],
    }
