from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

BBox = Annotated[list[float], Field(min_length=4, max_length=4)]
Point = Annotated[list[float], Field(min_length=2, max_length=2)]


class EventType(str, Enum):
    no_helmet = "no_helmet"
    no_mask = "no_mask"
    no_vest = "no_vest"
    zone_intrusion = "zone_intrusion"
    smoking = "smoking"
    fire = "fire"
    smoke = "smoke"


class Severity(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"

    @property
    def rank(self) -> int:
        return ("low", "medium", "high").index(self.value)


class EventStatus(str, Enum):
    open = "open"
    closed = "closed"


class Review(str, Enum):
    none = "none"
    confirmed = "confirmed"
    false_positive = "false_positive"


class PpeStatus(str, Enum):
    ok = "ok"
    violation = "violation"
    unknown = "unknown"
    not_required = "not_required"


class PpeItem(str, Enum):
    helmet = "helmet"
    mask = "mask"
    vest = "vest"


class ZoneType(str, Enum):
    restricted = "restricted"
    ppe_required = "ppe_required"
    flammable = "flammable"


class SourceType(str, Enum):
    rtsp = "rtsp"
    file = "file"


class Event(BaseModel):
    id: str
    type: EventType
    severity: Severity
    status: EventStatus = EventStatus.open
    camera_id: int
    camera_name: str
    zone_id: int | None = None
    zone_name: str | None = None
    track_id: int | None = None
    started_at: datetime
    ended_at: datetime | None = None
    confidence: float
    bbox: BBox | None = None
    snapshot_url: str | None = None
    snapshot_clean_url: str | None = None
    clip_url: str | None = None
    review: Review = Review.none
    details: dict[str, Any] = Field(default_factory=dict)


class EventCreate(BaseModel):
    type: EventType
    severity: Severity
    camera_id: int
    zone_id: int | None = None
    track_id: int | None = None
    started_at: datetime
    confidence: float
    bbox: BBox | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    captured_ts: int | None = None  # ms


class EventUpdate(BaseModel):
    status: EventStatus | None = None
    ended_at: datetime | None = None


class EventReview(BaseModel):
    review: Review


class EventMessage(BaseModel):
    type: Literal["event_created", "event_updated"]
    event: Event


class FrameSize(BaseModel):
    w: int
    h: int


class PersonBox(BaseModel):
    track_id: int
    bbox: BBox
    conf: float
    ppe: dict[PpeItem, PpeStatus] = Field(default_factory=dict)
    zones: list[int] = Field(default_factory=list)
    violations: list[EventType] = Field(default_factory=list)


class ObjectBox(BaseModel):
    cls: str
    bbox: BBox
    conf: float
    track_id: int | None = None


# то что летит в плеер по ws
class DetectionsMessage(BaseModel):
    type: Literal["detections"] = "detections"
    camera_id: int
    ts: int
    frame: FrameSize
    people: list[PersonBox] = Field(default_factory=list)
    objects: list[ObjectBox] = Field(default_factory=list)
