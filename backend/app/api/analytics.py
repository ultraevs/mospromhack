from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.models import EventRow
from backend.app.state import get_db, get_setting, require_admin
from shared.schemas import EventType, Review, Severity

router = APIRouter(prefix="/api", tags=["analytics"], dependencies=[Depends(require_admin)])


SHIFTS_KEY = "shifts"
DEFAULT_SHIFTS = [{"name": "Дневная", "start": "08:00", "end": "20:00"}, {"name": "Ночная", "start": "20:00", "end": "08:00"}]


class Shift(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    start: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    end: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("пустое название смены")
        return v


class Shifts(BaseModel):
    shifts: list[Shift] = Field(min_length=1, max_length=4)


def load_shifts(db: Session) -> list[dict]:
    raw = get_setting(db, SHIFTS_KEY)
    try:
        return [s.model_dump() for s in Shifts(shifts=raw).shifts] if raw else DEFAULT_SHIFTS
    except ValueError:
        return DEFAULT_SHIFTS


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def shift_of(local: datetime, shifts: list[dict]) -> str | None:
    t = local.hour * 60 + local.minute
    for s in shifts:
        a, b = _minutes(s["start"]), _minutes(s["end"])
        if (a <= t < b) if a < b else (t >= a or t < b):  # a>=b — через полночь
            return s["name"]
    return None


def _pct(values: list[float], q: float) -> float:
    i = min(len(values) - 1, max(0, round(q * (len(values) - 1))))
    return round(values[i], 1)


def _reaction(pairs: list[tuple[float, str]]) -> dict:
    allv = sorted(v for v, _ in pairs)
    high = sorted(v for v, sev in pairs if sev == Severity.high.value)
    return {"n": len(allv), "median_s": _pct(allv, 0.5) if allv else None, "p90_s": _pct(allv, 0.9) if allv else None,
            "high_median_s": _pct(high, 0.5) if high else None}


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
    days: int | None = Query(default=None, ge=1, le=3650),
    camera_id: int | None = None,
    tz_offset_min: int = Query(default=0, ge=-840, le=840, description="смещение местного времени от UTC, минуты"),
    db: Session = Depends(get_db),
) -> dict:
    if days is not None and from_ is None:
        now = datetime.now(timezone.utc)
        from_ = now - timedelta(days=days)
        to = to or now
    conds = []
    if from_ is not None:
        conds.append(EventRow.started_at >= _naive(from_))
    if to is not None:
        conds.append(EventRow.started_at <= _naive(to))
    if camera_id is not None:
        conds.append(EventRow.camera_id == camera_id)
    rows = db.execute(select(EventRow.type, EventRow.severity, EventRow.camera_id, EventRow.camera_name, EventRow.zone_id,
                             EventRow.zone_name, EventRow.started_at, EventRow.review,
                             EventRow.reviewed_at).where(*conds)).all()

    shift = timedelta(minutes=tz_offset_min)
    by_type: dict[str, list[str]] = {t.value: [] for t in EventType}
    by_camera: dict[int, list] = {}
    by_zone: dict[int, list] = {}
    hours, days = Counter(), Counter()
    shifts = load_shifts(db)
    per_shift, per_shift_type = Counter(), Counter()
    heat = [[0] * 24 for _ in range(7)]
    reaction: list[tuple[float, str]] = []
    for typ, sev, cam_id, cam_name, zone_id, zone_name, started, review, reviewed_at in rows:
        by_type.setdefault(typ, []).append(review)
        by_camera.setdefault(cam_id, [cam_name, 0])[1] += 1
        if zone_id is not None:
            by_zone.setdefault(zone_id, [zone_name, cam_name, 0])[2] += 1
        local = started + shift
        hours[local.hour] += 1
        days[local.date().isoformat()] += 1
        heat[local.weekday()][local.hour] += 1
        name = shift_of(local, shifts)
        if name is not None:
            per_shift[name] += 1
            per_shift_type[name, typ] += 1
        if reviewed_at is not None and review != Review.none.value and reviewed_at >= started:
            reaction.append(((reviewed_at - started).total_seconds(), sev))

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
        "by_shift": [{"name": sh["name"], "total": per_shift[sh["name"]]} for sh in shifts],
        "by_shift_type": sorted(({"name": n, "type": t, "total": c} for (n, t), c in per_shift_type.items()),
                                key=lambda x: (x["name"], -x["total"])),
        "heatmap": heat,
        "reaction": _reaction(reaction),
    }
