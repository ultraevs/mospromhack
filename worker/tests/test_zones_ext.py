from datetime import datetime

from shared.schemas import EventType, RuleSettings, Severity, SmokingRule, ZoneSchedule, ZoneType
from worker.app.rules.checks import check_barrier
from worker.app.rules.engine import evaluate_track
from worker.app.rules.state import SEVERITY, TrackState
from worker.app.rules.zones import active_camera, zone_active
from worker.tests.test_rules import camera, obs, zone

RULES = RuleSettings()
FPS = 6
# вертикальная полоса-ограждение x 0.48..0.52
STRIP = ((0.48, 0.1), (0.52, 0.1), (0.52, 0.9), (0.48, 0.9))


def sched(start, end, days=(1, 2, 3, 4, 5, 6, 7)):
    z = zone(1, ZoneType.restricted)
    return z.model_copy(update={"schedule": ZoneSchedule(start=start, end=end, days=list(days))})


def at(day, hhmm):  # 2026-10-19 - понедельник
    h, m = hhmm.split(":")
    return datetime(2026, 10, 18 + day, int(h), int(m))


def test_zone_without_schedule_always_active():
    assert zone_active(zone(1, ZoneType.restricted), at(7, "03:00"))


def test_schedule_until_noon():
    z = sched("00:00", "12:00", days=(1, 2, 3, 4, 5))
    assert zone_active(z, at(1, "11:59")) and zone_active(z, at(1, "12:00"))
    assert not zone_active(z, at(1, "12:01"))
    assert not zone_active(z, at(6, "10:00"))  # суббота выходной


def test_schedule_over_midnight_belongs_to_start_day():
    z = sched("22:00", "06:00", days=(5,))  # ночная смена с пятницы
    assert zone_active(z, at(5, "23:00"))
    assert zone_active(z, at(6, "05:00"))  # хвост пятничного окна в субботу
    assert not zone_active(z, at(5, "05:00"))  # утро пятницы - это хвост четверга
    assert not zone_active(z, at(6, "12:00")) and not zone_active(z, at(6, "23:00"))
    sunday = sched("22:00", "06:00", days=(7,))
    assert zone_active(sunday, at(8, "01:00"))  # пн после вс


def test_inactive_zone_dropped_from_camera():
    cam = camera(zones=[sched("00:00", "12:00"), zone(2, ZoneType.flammable)])
    assert [z.id for z in active_camera(cam, at(1, "10:00")).zones] == [1, 2]
    assert [z.id for z in active_camera(cam, at(1, "13:00")).zones] == [2]


def track_of(points, zones_of=lambda p: (), t0=100.0, **kw):
    tr = TrackState(1)
    for i, (x, y) in enumerate(points):
        tr.add(obs(t0 + i / FPS, required=(), zones=zones_of((x, y)), bbox=(x - 0.05, y - 0.4, 0.1, 0.4), **kw))
    return tr


def in_strip(p):
    return (5,) if 0.48 <= p[0] <= 0.52 and 0.1 <= p[1] <= 0.9 else ()


def types(cands, active=True):
    return {c.type: c for c in cands if c.result.active == active}


def smoker(zones, cam_zones, rules=RULES):
    tr = track_of([(0.3, 0.5)] * 3, zones_of=lambda p: zones)
    tr.checks.extend([(100.0 + i, True, 0.5) for i in range(3)])
    return types(evaluate_track(tr, tr.last.t, camera(required=(), zones=cam_zones), rules)).get(EventType.smoking)


def test_smoking_everywhere_except_smoking_room():
    zs = [zone(3, ZoneType.smoking_allowed), zone(9, ZoneType.flammable)]
    assert smoker((), zs).severity == Severity.medium
    assert smoker((3,), zs) is None
    assert smoker((9,), zs).severity == Severity.high
    assert smoker((3, 9), zs).severity == Severity.high  # курилка внутри склада гсм не спасает
    only = RuleSettings(smoking=SmokingRule(everywhere=False))
    assert smoker((), zs, only) is None and smoker((9,), zs, only).severity == Severity.high


def barrier(points):
    tr = track_of(points, zones_of=in_strip)
    cam = camera(required=(), zones=[zone(5, ZoneType.barrier, polygon=STRIP)])
    return types(evaluate_track(tr, tr.last.t, cam, RULES)).get(EventType.barrier_crossing)


def test_barrier_crossing_through_strip():
    c = barrier([(0.40, 0.5), (0.45, 0.5), (0.50, 0.5), (0.55, 0.5), (0.60, 0.5)])
    assert c and c.severity == Severity.high and c.zone_id == 5
    # перепрыгнул, ни одного кадра внутри полосы
    assert barrier([(0.40, 0.5), (0.45, 0.5), (0.55, 0.5), (0.60, 0.5)])


def test_walking_along_barrier_is_not_crossing():
    assert barrier([(0.42, 0.2 + i * 0.05) for i in range(12)]) is None
    assert barrier([(0.58, 0.8 - i * 0.05) for i in range(12)]) is None


def test_going_around_barrier_end_is_not_crossing():
    assert barrier([(0.40, 0.95), (0.45, 0.97), (0.50, 0.97), (0.55, 0.97), (0.60, 0.95)]) is None


def test_standing_in_strip_counts():
    assert barrier([(0.42, 0.5), (0.50, 0.5), (0.50, 0.5)]) is None
    assert barrier([(0.42, 0.5), (0.50, 0.5), (0.50, 0.5), (0.50, 0.5)])


def test_check_barrier_respects_window():
    tr = track_of([(0.40, 0.5)] + [(0.60, 0.5)] * 30, zones_of=in_strip)  # перелез давно, окно ушло
    assert not check_barrier(tr.obs, tr.last.t, 5, [list(p) for p in STRIP], RULES.barrier_crossing).active


def test_under_load_like_zone_intrusion():
    cam = camera(required=(), zones=[zone(4, ZoneType.suspended_load)])
    tr = track_of([(0.3, 0.5)] * 9, zones_of=lambda p: (4,))
    c = types(evaluate_track(tr, tr.last.t, cam, RULES))[EventType.under_load]
    assert c.severity == Severity.high and c.zone_id == 4
    tr = track_of([(0.3, 0.5)] * 9)
    assert EventType.under_load not in types(evaluate_track(tr, tr.last.t, cam, RULES))


def test_height_without_harness():
    cam = camera(required=(), zones=[zone(6, ZoneType.height)])
    tr = track_of([(0.3, 0.5)] * 12, zones_of=lambda p: (6,))
    c = types(evaluate_track(tr, tr.last.t, cam, RULES))[EventType.no_harness]
    assert c.severity == Severity.high and c.zone_id == 6
    tr = track_of([(0.3, 0.5)] * 12, zones_of=lambda p: (6,), seen=("harness",))
    assert EventType.no_harness not in types(evaluate_track(tr, tr.last.t, cam, RULES))
    tr = track_of([(0.3, 0.5)] * 12)
    assert EventType.no_harness not in types(evaluate_track(tr, tr.last.t, cam, RULES))


def test_new_severities():
    assert all(SEVERITY[t] == Severity.high for t in (EventType.barrier_crossing, EventType.under_load,
                                                      EventType.no_harness))
    assert SEVERITY[EventType.phone_usage] == SEVERITY[EventType.unsafe_carry] == Severity.medium
