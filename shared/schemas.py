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
    running = "running"
    vehicle_proximity = "vehicle_proximity"
    fall = "fall"
    lying_immobile = "lying_immobile"
    phone_usage = "phone_usage"  # 03 телефон при работе / на ходу
    barrier_crossing = "barrier_crossing"  # 05 перелез через ограждение
    under_load = "under_load"  # 05 под подвешенным грузом / конвейером
    no_harness = "no_harness"  # 06 на высоте без привязи
    unsafe_carry = "unsafe_carry"  # 08 переноска груза с нарушением


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
    forwarded = "forwarded"


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
    smoking_allowed = "smoking_allowed"  # курилка: курение тут не нарушение
    barrier = "barrier"  # полоса ограждения: пересёк — нарушение
    suspended_load = "suspended_load"  # под краном / конвейером
    height = "height"  # работа на высоте, нужна привязь


class SourceType(str, Enum):
    rtsp = "rtsp"
    file = "file"
    images = "images"


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
    clip_anon_url: str | None = None
    review: Review = Review.none
    reviewed_at: datetime | None = None
    reviewed_by: str | None = None
    review_note: str | None = None
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
    note: Annotated[str, Field(max_length=500)] | None = None


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


HHMM = Annotated[str, Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]


# когда зона действует; через полночь можно (22:00–06:00). нет расписания — всегда
class ZoneSchedule(BaseModel):
    start: HHMM = "00:00"
    end: HHMM = "23:59"
    days: list[Annotated[int, Field(ge=1, le=7)]] = Field(default_factory=lambda: [1, 2, 3, 4, 5, 6, 7])  # 1 — пн


class ZoneBase(BaseModel):
    name: str
    type: ZoneType
    polygon: list[Point] = Field(min_length=3)
    required_ppe: list[PpeItem] = Field(default_factory=list)
    schedule: ZoneSchedule | None = None


class ZoneCreate(ZoneBase):
    pass


class ZoneUpdate(BaseModel):
    name: str | None = None
    type: ZoneType | None = None
    polygon: list[Point] | None = Field(default=None, min_length=3)
    required_ppe: list[PpeItem] | None = None
    schedule: ZoneSchedule | None = None


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
    everywhere: bool = True  # везде кроме курилок (smoking_allowed); False — только в зонах flammable


class RunningRule(BaseModel):
    window_s: Seconds = 2
    min_observations: Count = 6
    fraction: Share = 0.6
    speed_heights_s: Annotated[float, Field(gt=0, le=100)] = 1.5


class VehicleProximityRule(BaseModel):
    window_s: Seconds = 1.5
    min_observations: Count = 4
    fraction: Share = 0.7
    distance_heights: Annotated[float, Field(gt=0, le=100)] = 1.0
    min_vehicle_speed: Annotated[float, Field(ge=0, le=100)] = 0.1  # своих высот рамки в секунду; 0 — и стоящая техника


class FallRule(BaseModel):
    window_s: Seconds = 1.5
    min_observations: Count = 2
    upright_ratio: Annotated[float, Field(gt=0, le=10)] = 1.3
    lying_ratio: Annotated[float, Field(gt=0, le=10)] = 1.0
    drop_heights: Annotated[float, Field(ge=0, le=10)] = 0.2


class LyingImmobileRule(BaseModel):
    window_s: Seconds = 6
    min_observations: Count = 12
    fraction: Share = 0.7
    lying_ratio: Annotated[float, Field(gt=0, le=10)] = 1.0
    max_speed: Annotated[float, Field(ge=0, le=10)] = 0.1
    spot_fraction: Share = 0.5
    spot_radius: Annotated[float, Field(gt=0, le=5)] = 0.5


class PhoneRule(BaseModel):
    window_s: Seconds = 3
    min_observations: Count = 6
    fraction: Share = 0.5
    upper_fraction: Share = 0.6  # телефон в верхней части фигуры (у уха, в руках перед собой)


class BarrierRule(BaseModel):
    window_s: Seconds = 3  # за сколько секунд ноги должны пройти полосу ограждения насквозь
    min_observations: Count = 3


class UnderLoadRule(BaseModel):
    window_s: Seconds = 1.5
    fraction: Share = 0.7
    min_observations: Count = 4


class HarnessRule(BaseModel):
    window_s: Seconds = 4
    min_observations: Count = 8
    max_fraction: Share = 0.2  # привязь видна меньше чем в 20% кадров — нарушение


class CarryRule(BaseModel):
    window_s: Seconds = 2
    min_observations: Count = 6
    fraction: Share = 0.6
    min_speed_heights_s: Annotated[float, Field(ge=0, le=100)] = 0.3  # несёт на ходу


class LifecycleRule(BaseModel):
    close_after_s: Seconds = 5
    cooldown_s: Annotated[float, Field(ge=0, le=86_400)] = 60
    retrack_iou: Share = 0.3
    retrack_window_s: Annotated[float, Field(ge=0, le=600)] = 10
    retrack_dist_heights: Annotated[float, Field(ge=0, le=5)] = 0.5
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
    running: RunningRule = Field(default_factory=RunningRule)
    vehicle_proximity: VehicleProximityRule = Field(default_factory=VehicleProximityRule)
    fall: FallRule = Field(default_factory=FallRule)
    lying_immobile: LyingImmobileRule = Field(default_factory=LyingImmobileRule)
    phone_usage: PhoneRule = Field(default_factory=PhoneRule)
    barrier_crossing: BarrierRule = Field(default_factory=BarrierRule)
    under_load: UnderLoadRule = Field(default_factory=UnderLoadRule)
    no_harness: HarnessRule = Field(default_factory=HarnessRule)
    unsafe_carry: CarryRule = Field(default_factory=CarryRule)
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
    stills: bool = False  # камера на стоп-кадрах: правила движения не считаем


# отдаем воркеру через /internal/config
class WorkerConfig(BaseModel):
    version: int
    cameras: list[WorkerCamera] = Field(default_factory=list)
    rules: RuleSettings = Field(default_factory=RuleSettings)
