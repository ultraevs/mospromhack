from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from shared.schemas import EventType, Severity


@dataclass(frozen=True)
class EventOpen:
    key: int  # локальный, id из бэка придет после POST
    type: EventType
    severity: Severity
    t: float
    confidence: float
    bbox: tuple[float, float, float, float] | None  # x y w h в долях
    track_id: int | None = None
    zone_id: int | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EventClose:
    key: int
    t: float
