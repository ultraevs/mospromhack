from __future__ import annotations

import secrets
import time
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from backend.app.db.models import CameraRow, EventRow, ZoneRow
from shared.schemas import Event, EventCreate, EventMessage, EventStatus

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_event_id() -> str:
    t = int(time.time())
    head = "".join(_CROCKFORD[(t >> (5 * i)) & 31] for i in range(5, -1, -1))
    return "evt_" + head + "".join(secrets.choice(_CROCKFORD) for _ in range(4))


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _naive_utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def to_schema(row: EventRow) -> Event:
    return Event(
        id=row.id, type=row.type, severity=row.severity, status=row.status,
        camera_id=row.camera_id, camera_name=row.camera_name, zone_id=row.zone_id, zone_name=row.zone_name,
        track_id=row.track_id, started_at=_aware(row.started_at), ended_at=_aware(row.ended_at),
        confidence=row.confidence, bbox=row.bbox, snapshot_url=row.snapshot_url,
        snapshot_clean_url=row.snapshot_clean_url, clip_url=row.clip_url, review=row.review,
        details=row.details or {},
    )


def create_event(db: Session, body: EventCreate) -> EventRow:
    camera = db.get(CameraRow, body.camera_id)
    zone = db.get(ZoneRow, body.zone_id) if body.zone_id is not None else None
    row = EventRow(
        id=new_event_id(), type=body.type.value, severity=body.severity.value, status=EventStatus.open.value,
        camera_id=body.camera_id, camera_name=camera.name if camera else f"камера {body.camera_id}",
        zone_id=body.zone_id, zone_name=zone.name if zone else None, track_id=body.track_id,
        started_at=_naive_utc(body.started_at), confidence=body.confidence, bbox=body.bbox,
        details=body.details,
        latency_ms=int(time.time() * 1000) - body.captured_ts if body.captured_ts else None,
    )
    db.add(row)
    db.commit()
    return row


def message(kind: str, row: EventRow) -> str:
    return EventMessage(type=kind, event=to_schema(row)).model_dump_json()
