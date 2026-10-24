from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.db.models import EventRow
from backend.app.services import events as svc
from backend.app.state import AppState, get_db, get_current_user, get_state
from shared.schemas import Event, EventReview, EventStatus, EventType, Review, Severity

router = APIRouter(prefix="/api", tags=["events"], dependencies=[Depends(get_current_user)])


class EventPage(BaseModel):
    items: list[Event]
    total: int
    limit: int
    offset: int


def _naive(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


@router.get("/events", response_model=EventPage)
async def list_events(
    camera_id: int | None = None,
    zone_id: int | None = None,
    type: EventType | None = None,
    severity: Severity | None = None,
    status: EventStatus | None = None,
    review: Review | None = None,
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    conds = []
    if camera_id is not None:
        conds.append(EventRow.camera_id == camera_id)
    if zone_id is not None:
        conds.append(EventRow.zone_id == zone_id)
    if type is not None:
        conds.append(EventRow.type == type.value)
    if severity is not None:
        conds.append(EventRow.severity == severity.value)
    if status is not None:
        conds.append(EventRow.status == status.value)
    if review is not None:
        conds.append(EventRow.review == review.value)
    if from_ is not None:
        conds.append(EventRow.started_at >= _naive(from_))
    if to is not None:
        conds.append(EventRow.started_at <= _naive(to))

    total = db.execute(select(func.count()).select_from(EventRow).where(*conds)).scalar_one()
    rows = db.execute(select(EventRow).where(*conds).order_by(EventRow.started_at.desc(), EventRow.id.desc())
                      .limit(limit).offset(offset)).scalars().all()
    return EventPage(items=[svc.to_schema(r) for r in rows], total=total, limit=limit, offset=offset)


@router.get("/events/{event_id}", response_model=Event)
async def get_event(event_id: str, db: Session = Depends(get_db)):
    row = db.get(EventRow, event_id)
    if row is None:
        raise HTTPException(404, "событие не найдено")
    return svc.to_schema(row)


@router.patch("/events/{event_id}", response_model=Event)
async def review_event(event_id: str, body: EventReview, db: Session = Depends(get_db),
                       state: AppState = Depends(get_state)):
    row = db.get(EventRow, event_id)
    if row is None:
        raise HTTPException(404, "событие не найдено")
    row.review = body.review.value
    db.commit()
    await state.hub.send_event(svc.message("event_updated", row))
    return svc.to_schema(row)
