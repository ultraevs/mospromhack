from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from shared.schemas import EventType, PpeItem, Severity

PERSON = "person"
CIGARETTE = "cigarette"
HAZARD_EVENTS = {"fire": EventType.fire, "smoke": EventType.smoke}

PPE_CLASSES: dict[PpeItem, tuple[str, str | None, bool]] = {
    PpeItem.helmet: ("helmet", "no_helmet", True),
    PpeItem.mask: ("mask", "no_mask", True),
    PpeItem.vest: ("vest", None, False),
}
HEAD_CLASSES = frozenset(c for ok, bad, head in PPE_CLASSES.values() if head for c in (ok, bad) if c)
ATTACHABLE = frozenset(c for ok, bad, _ in PPE_CLASSES.values() for c in (ok, bad) if c)

SEVERITY: dict[EventType, Severity] = {
    EventType.fire: Severity.high,
    EventType.smoke: Severity.high,
    EventType.zone_intrusion: Severity.high,
    EventType.no_helmet: Severity.medium,
    EventType.no_mask: Severity.medium,
    EventType.smoking: Severity.medium,  # high если в зоне flammable
    EventType.no_vest: Severity.low,
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
