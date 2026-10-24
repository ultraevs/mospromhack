from __future__ import annotations

from datetime import datetime

from shared.schemas import WorkerCamera, Zone


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


# время локальное сервера. окно через полночь (22:00-06:00) относим к дню начала
def zone_active(zone: Zone, now: datetime) -> bool:
    s = zone.schedule
    if s is None:
        return True
    start, end, cur = _minutes(s.start), _minutes(s.end), now.hour * 60 + now.minute
    day = now.isoweekday()
    if start <= end:
        return day in s.days and start <= cur <= end
    if cur >= start:
        return day in s.days
    if cur <= end:
        return (day - 2) % 7 + 1 in s.days  # хвост вчерашнего окна
    return False


# зоны вне расписания для воркера просто не существуют
def active_camera(camera: WorkerCamera, now: datetime | None = None) -> WorkerCamera:
    if not any(z.schedule for z in camera.zones):
        return camera
    now = now or datetime.now()
    return camera.model_copy(update={"zones": [z for z in camera.zones if zone_active(z, now)]})
