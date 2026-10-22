from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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


class CameraBase(BaseModel):
    name: str
    source_type: SourceType
    source: str
    required_ppe: list[PpeItem] = Field(default_factory=list)
    enabled: bool = True


class CameraCreate(CameraBase):
    pass


class CameraUpdate(BaseModel):
    name: str | None = None
    source_type: SourceType | None = None
    source: str | None = None
    required_ppe: list[PpeItem] | None = None
    enabled: bool | None = None


class Camera(CameraBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    status: Literal["online", "offline"] = "offline"
    open_events: int = 0


class ZoneBase(BaseModel):
    name: str
    type: ZoneType
    polygon: list[Point] = Field(min_length=3)
    required_ppe: list[PpeItem] = Field(default_factory=list)


class ZoneCreate(ZoneBase):
    pass


class ZoneUpdate(BaseModel):
    name: str | None = None
    type: ZoneType | None = None
    polygon: list[Point] | None = Field(default=None, min_length=3)
    required_ppe: list[PpeItem] | None = None


class Zone(ZoneBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    camera_id: int


Seconds = Annotated[float, Field(gt=0, le=600)]
Share = Annotated[float, Field(ge=0, le=1)]
Count = Annotated[int, Field(ge=1, le=10_000)]


# дефолты должны совпадать с config.yaml!!
class FractionRule(BaseModel):
    window_s: Seconds = 4
    min_observations: Count = 8
    fraction: Share = 0.35


class NoVestRule(BaseModel):
    window_s: Seconds = 6
    min_observations: Count = 15
    max_fraction: Share = 0.1
    min_person_height_px: Annotated[int, Field(ge=0, le=10_000)] = 120


class ZoneIntrusionRule(BaseModel):
    window_s: Seconds = 1.5
    fraction: Share = 0.7
    min_observations: Count = 4


class KofMRule(BaseModel):
    k: Count
    m: Count

    @model_validator(mode="after")
    def _k_not_above_m(self):
        if self.k > self.m:
            raise ValueError("k не может быть больше m")
        return self


class SmokingRule(KofMRule):
    k: Count = 3
    m: Count = 5
    upper_fraction: Share = 0.33
    everywhere: bool = False


class LifecycleRule(BaseModel):
    close_after_s: Seconds = 5
    cooldown_s: Annotated[float, Field(ge=0, le=86_400)] = 60
    retrack_iou: Share = 0.3
    retrack_window_s: Annotated[float, Field(ge=0, le=600)] = 10
    camera_cooldown_s: Annotated[float, Field(ge=0, le=86_400)] = 60
    track_lost_s: Seconds = 2


class RuleSettings(BaseModel):
    no_helmet: FractionRule = Field(default_factory=FractionRule)
    no_mask: FractionRule = Field(default_factory=FractionRule)
    no_vest: NoVestRule = Field(default_factory=NoVestRule)
    zone_intrusion: ZoneIntrusionRule = Field(default_factory=ZoneIntrusionRule)
    smoking: SmokingRule = Field(default_factory=SmokingRule)
    fire: KofMRule = Field(default_factory=lambda: KofMRule(k=3, m=5))
    smoke: KofMRule = Field(default_factory=lambda: KofMRule(k=3, m=5))
    lifecycle: LifecycleRule = Field(default_factory=LifecycleRule)


class TelegramSettings(BaseModel):
    instant_min_severity: Severity = Severity.high
    min_interval_s: Annotated[float, Field(ge=0.05, le=60)] = 1
    summary_interval_s: Annotated[float, Field(ge=1, le=86_400)] = 60
    repeat_interval_s: Annotated[float, Field(ge=0, le=86_400)] = 300


class WorkerCamera(BaseModel):
    id: int
    name: str
    rtsp_url: str
    required_ppe: list[PpeItem] = Field(default_factory=list)
    zones: list[Zone] = Field(default_factory=list)


# отдаем воркеру через /internal/config
class WorkerConfig(BaseModel):
    version: int
    cameras: list[WorkerCamera] = Field(default_factory=list)
    rules: RuleSettings = Field(default_factory=RuleSettings)
