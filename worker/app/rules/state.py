from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from shared.schemas import EventType, PpeItem, Severity

PERSON = "person"
CIGARETTE = "cigarette"
VEHICLE = "vehicle"
HAZARD_EVENTS = {"fire": EventType.fire, "smoke": EventType.smoke}

PPE_CLASSES: dict[PpeItem, tuple[str, str | None, bool]] = {
    PpeItem.helmet: ("helmet", "no_helmet", True),
    PpeItem.mask: ("mask", "no_mask", True),
    PpeItem.vest: ("vest", None, False),
}
HEAD_CLASSES = frozenset(c for ok, bad, head in PPE_CLASSES.values() if head for c in (ok, bad) if c)
ATTACHABLE = frozenset(c for ok, bad, _ in PPE_CLASSES.values() for c in (ok, bad) if c)
HARNESS = "harness"
# необязательные классы: если модель их знает - крепим к человеку в seen, нет - не падаем
OPTIONAL_ATTACHABLE = frozenset({HARNESS})

SEVERITY: dict[EventType, Severity] = {
    EventType.fire: Severity.high,
    EventType.smoke: Severity.high,
    EventType.zone_intrusion: Severity.high,
    EventType.no_helmet: Severity.medium,
    EventType.no_mask: Severity.medium,
    EventType.smoking: Severity.medium,  # high если в зоне flammable
    EventType.no_vest: Severity.low,
    EventType.running: Severity.medium,  # high в restricted
    EventType.vehicle_proximity: Severity.high,
    EventType.fall: Severity.high,
    EventType.lying_immobile: Severity.high,
    EventType.barrier_crossing: Severity.high,
    EventType.under_load: Severity.high,
    EventType.no_harness: Severity.high,
    EventType.phone_usage: Severity.medium,
    EventType.unsafe_carry: Severity.medium,
}

HISTORY_S = 12.0  # должно быть >= самого длинного окна правил


@dataclass(frozen=True)
class Observation:
    t: float
    bbox: tuple[float, float, float, float]
    height_px: float
    conf: float
    seen: dict[str, float]
    zones: frozenset[int]
    required: frozenset[PpeItem]
    vehicles: tuple[tuple[float, float, float, float], ...] = ()  # техника в этом кадре, x y w h в долях
    vehicle_speeds: tuple[float, ...] = ()  # скорость техники, высот рамки/с; пусто = неизвестна (stills)
    aspect: float = 1.0  # w/h кадра
    phones: tuple[tuple[float, float, float, float], ...] = ()  # телефоны внутри рамки человека, x y w h в долях
    keypoints: tuple[tuple[float, float, float], ...] | None = None  # 17 точек coco x y vis в долях, если была поза


@dataclass
class TrackState:
    track_id: int
    obs: deque = field(default_factory=deque)
    checks: deque = field(default_factory=deque)

    def add(self, o: Observation, history_s: float = HISTORY_S) -> None:
        self.obs.append(o)
        while self.obs and self.obs[0].t < o.t - history_s:
            self.obs.popleft()

    @property
    def last(self) -> Observation:
        return self.obs[-1]
