from __future__ import annotations

import itertools
from collections import deque

from shared.schemas import EventType, RuleSettings, Severity
from worker.app.rules.types import EventClose, EventOpen

_keys = itertools.count(1)


# пока без трекинга, просто по камере. потом переделать
class SimpleNoHelmet:
    def __init__(self) -> None:
        self._obs: deque[tuple[float, float, tuple | None]] = deque()
        self._open_key: int | None = None
        self._last_true = 0.0
        self._closed_at: float | None = None

    @property
    def is_open(self) -> bool:
        return self._open_key is not None

    def update(self, t: float, boxes: list[tuple[float, tuple[float, float, float, float]]],
               rules: RuleSettings) -> list[EventOpen | EventClose]:
        p, life = rules.no_helmet, rules.lifecycle
        best = max(boxes, key=lambda b: b[0], default=None)
        self._obs.append((t, best[0] if best else 0.0, best[1] if best else None))
        while self._obs and self._obs[0][0] < t - p.window_s:
            self._obs.popleft()

        hits = [o for o in self._obs if o[1] > 0]
        fraction = len(hits) / len(self._obs)
        active = len(self._obs) >= p.min_observations and fraction >= p.fraction
        if active:
            self._last_true = t

        out: list[EventOpen | EventClose] = []
        if self._open_key is None:
            cooled = self._closed_at is None or t - self._closed_at >= life.cooldown_s
            if active and cooled:
                self._open_key = next(_keys)
                top = max(hits, key=lambda o: o[1])
                out.append(EventOpen(
                    key=self._open_key, type=EventType.no_helmet, severity=Severity.medium, t=t,
                    confidence=round(sum(o[1] for o in hits) / len(hits), 3), bbox=top[2],
                    details={"fraction": round(fraction, 2), "window_s": p.window_s, "observations": len(self._obs)}))
        elif t - self._last_true >= life.close_after_s:
            out.append(EventClose(self._open_key, t))
            self._open_key, self._closed_at = None, t
        return out
