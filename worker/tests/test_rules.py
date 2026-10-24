import numpy as np
import pytest

from shared.schemas import EventType, PpeItem, RuleSettings, Severity, SmokingRule, WorkerCamera, Zone, ZoneType
from worker.app.rules.checks import (check_fall, check_head_ppe, check_k_of_m, check_lying_immobile, check_no_vest,
                                     check_running, check_vehicle_proximity, check_zone, in_upper_part)
from worker.app.rules.engine import RuleEngine, evaluate_track
from worker.app.rules.state import Observation, TrackState
from worker.app.rules.types import EventClose, EventOpen
from worker.app.tracking import VehicleSpeeds

RULES = RuleSettings()
FPS = 6
BOX = (0.4, 0.2, 0.1, 0.4)
HELMET = frozenset({PpeItem.helmet})
ALL = frozenset({PpeItem.helmet, PpeItem.mask, PpeItem.vest})


def obs(t, seen=(), required=HELMET, zones=(), bbox=BOX, height=300.0, vehicles=(), vehicle_speeds=()):
    return Observation(t=t, bbox=bbox, height_px=height, conf=0.9, seen={c: 0.6 for c in seen},
                       zones=frozenset(zones), required=frozenset(required), vehicles=vehicles,
                       vehicle_speeds=vehicle_speeds)


def series(pattern, start=0.0, fps=FPS, **kw):
    return [obs(start + i / fps, seen=s, **kw) for i, s in enumerate(pattern)]


def camera(required=(PpeItem.helmet,), zones=(), stills=False):
    return WorkerCamera(id=1, name="c", rtsp_url="rtsp://x", required_ppe=list(required), zones=list(zones),
                        stills=stills)


def zone(zone_id, zone_type, required=(), polygon=((0, 0), (1, 0), (1, 1), (0, 1))):
    return Zone(id=zone_id, camera_id=1, name=f"z{zone_id}", type=zone_type, polygon=[list(p) for p in polygon],
                required_ppe=list(required))


def head(o, p=RULES.no_helmet):
    return check_head_ppe(o, o[-1].t, PpeItem.helmet, "helmet", "no_helmet", p)


def test_no_helmet_fires_at_fraction():
    r = head(series([("no_helmet",) if i < 7 else () for i in range(20)]))
    assert r.active and r.fraction == pytest.approx(0.35) and r.observations == 20
    assert r.confidence == pytest.approx(0.6)
    assert r.details == {"fraction": 0.35, "window_s": 4, "observations": 20}


def test_no_helmet_fraction_boundary():
    assert not head(series([("no_helmet",) if i < 6 else () for i in range(20)])).active


def test_no_helmet_min_observations_boundary():
    assert not head(series([("no_helmet",)] * 7)).active
    assert head(series([("no_helmet",)] * 8)).active


def test_no_helmet_must_exceed_helmet_fraction():
    tie = series([("no_helmet",)] * 8 + [("helmet",)] * 8)
    assert not head(tie).active
    assert head(series([("no_helmet",)] * 9 + [("helmet",)] * 7)).active
    assert not head(series([("helmet",)] * 20)).active


def test_no_helmet_window_drops_old_observations():
    o = series([("no_helmet",)] * 24 + [()] * 30)
    assert not head(o).active
    assert head(o[:30]).active


def test_no_helmet_requires_helmet_to_be_required():
    assert not head(series([("no_helmet",)] * 20, required=())).active
    o = series([("no_helmet",)] * 20, required=()) + series([("no_helmet",)] * 7, start=20 / FPS)
    assert not head(o).active
    o += series([("no_helmet",)], start=27 / FPS)
    assert head(o).active
    assert not head(o + series([("no_helmet",)], start=28 / FPS, required=())).active


def test_no_mask_uses_its_own_classes():
    o = series([("no_mask", "helmet")] * 10, required=ALL)
    assert check_head_ppe(o, o[-1].t, PpeItem.mask, "mask", "no_mask", RULES.no_mask).active
    assert not check_head_ppe(o, o[-1].t, PpeItem.helmet, "helmet", "no_helmet", RULES.no_helmet).active


def vest(o, p=RULES.no_vest):
    return check_no_vest(o, o[-1].t, "vest", p)


def test_no_vest_fires_when_vest_is_rarely_seen():
    assert vest(series([()] * 20, required=ALL)).active
    r = vest(series([("vest",) if i < 2 else () for i in range(20)], required=ALL))
    assert r.active and r.confidence == pytest.approx(0.9)
    assert not vest(series([("vest",) if i < 3 else () for i in range(20)], required=ALL)).active


def test_no_vest_min_observations_boundary():
    assert not vest(series([()] * 14, required=ALL)).active
    assert vest(series([()] * 15, required=ALL)).active


def test_no_vest_ignores_small_people():
    assert not vest(series([()] * 30, required=ALL, height=119.0)).active
    assert vest(series([()] * 30, required=ALL, height=120.0)).active
    mixed = series([()] * 20, required=ALL, height=80.0) + series([()] * 14, start=20 / FPS, required=ALL)
    assert not vest(mixed).active


def test_no_vest_not_required():
    assert not vest(series([()] * 30, required=HELMET)).active


def test_zone_fraction_boundary():
    p = RULES.zone_intrusion
    inside7 = [obs(i / FPS, zones=(3,) if i < 7 else ()) for i in range(10)]
    r = check_zone(inside7, inside7[-1].t, 3, p)
    assert r.active and r.fraction == pytest.approx(0.7)
    inside6 = [obs(i / FPS, zones=(3,) if i < 6 else ()) for i in range(10)]
    assert not check_zone(inside6, inside6[-1].t, 3, p).active
    assert not check_zone(inside7, inside7[-1].t, 4, p).active


def test_zone_min_observations_boundary():
    three = [obs(i / FPS, zones=(3,)) for i in range(3)]
    assert not check_zone(three, three[-1].t, 3, RULES.zone_intrusion).active
    four = three + [obs(3 / FPS, zones=(3,))]
    r = check_zone(four, four[-1].t, 3, RULES.zone_intrusion)
    assert r.active and r.observations == 4
    loose = RULES.zone_intrusion.model_copy(update={"min_observations": 1})
    assert check_zone(three[:1], 0.0, 3, loose).active
    old = [obs(0.0, zones=(3,)), obs(0.1, zones=(3,)), obs(5.0, zones=(3,)), obs(5.1, zones=(3,))]
    assert not check_zone(old, 5.1, 3, RULES.zone_intrusion).active


def test_default_smoking_rule_is_3_of_5_everywhere():
    p = RULES.smoking
    assert (p.k, p.m, p.upper_fraction, p.everywhere) == (3, 5, 0.33, True)
    hits = lambda n: [(i, i < n, 0.5) for i in range(5)]      # noqa: E731
    assert not check_k_of_m(hits(2), p).active and check_k_of_m(hits(3), p).active


def test_in_upper_part():
    person = (0.4, 0.2, 0.1, 0.6)
    assert in_upper_part(person, (0.44, 0.25, 0.02, 0.02), 0.33)
    assert in_upper_part(person, (0.44, 0.387, 0.02, 0.02), 0.33)
    assert not in_upper_part(person, (0.44, 0.39, 0.02, 0.02), 0.33)
    assert not in_upper_part(person, (0.44, 0.6, 0.02, 0.02), 0.33)
    assert not in_upper_part(person, (0.44, 0.15, 0.02, 0.02), 0.33)
    assert not in_upper_part(person, (0.55, 0.25, 0.02, 0.02), 0.33)
    assert in_upper_part(person, (0.44, 0.6, 0.02, 0.02), 1.0)


def test_k_of_m():
    p = RULES.fire
    checks = [(i, hit, 0.5) for i, hit in enumerate([True, True, False, False, True])]
    assert check_k_of_m(checks, p).active
    assert not check_k_of_m(checks[:4], p).active
    assert not check_k_of_m([(0, True, 0.5)] * 2 + [(1, False, 0.0)] * 3, p).active
    assert not check_k_of_m([(0, True, 0.5)] * 3 + [(1, False, 0.0)] * 5, p).active
    assert not check_k_of_m([], p).active


class Sim:

    def __init__(self, cam=None, rules=RULES):
        self.engine, self.cam, self.rules = RuleEngine(), cam or camera(), rules
        self.tracks: dict[int, TrackState] = {}
        self.actions = []

    def frame(self, t, people: dict):
        for tid, kw in people.items():
            self.tracks.setdefault(tid, TrackState(tid)).add(obs(t, **kw))
        actions, evaluated = self.engine.step(t, self.tracks, self.cam, self.rules)
        self.actions += actions
        return actions, evaluated

    def run(self, start, seconds, people):
        out = []
        for i in range(int(seconds * FPS)):
            out += self.frame(start + i / FPS, people)[0]
        return out

    def opens(self):
        return [a for a in self.actions if isinstance(a, EventOpen)]

    def closes(self):
        return [a for a in self.actions if isinstance(a, EventClose)]


NO_HELMET = {"seen": ("no_helmet",)}
CLEAN = {"seen": ("helmet",)}


def test_event_opens_once_while_condition_holds():
    sim = Sim()
    sim.run(100.0, 20, {7: NO_HELMET})
    assert len(sim.opens()) == 1 and not sim.closes()
    ev = sim.opens()[0]
    assert (ev.type, ev.severity, ev.track_id, ev.zone_id) == (EventType.no_helmet, Severity.medium, 7, None)
    assert ev.t == pytest.approx(100.0 + 7 / FPS)
    assert ev.bbox == BOX and ev.details["observations"] == 8


def test_event_closes_5_seconds_after_condition_stops():
    sim = Sim()
    sim.run(100.0, 4, {7: NO_HELMET})
    sim.run(104.0, 12, {7: CLEAN})
    assert len(sim.closes()) == 1 and sim.closes()[0].key == sim.opens()[0].key
    assert sim.closes()[0].t == pytest.approx(111.0, abs=0.35)


def test_event_closes_when_track_is_lost():
    sim = Sim()
    sim.run(100.0, 4, {7: NO_HELMET})
    sim.run(104.0, 3, {9: CLEAN})
    assert len(sim.closes()) == 1
    assert sim.closes()[0].t == pytest.approx(104.0 - 1 / FPS + RULES.lifecycle.track_lost_s, abs=0.2)


def test_same_track_cooldown_60_seconds():
    sim = Sim()
    sim.run(100.0, 4, {7: NO_HELMET})
    sim.run(104.0, 12, {7: CLEAN})
    closed_at = sim.closes()[0].t
    sim.run(116.0, 40, {7: NO_HELMET})
    assert len(sim.opens()) == 1
    sim.run(156.0, 20, {7: NO_HELMET})
    assert len(sim.opens()) == 2
    assert sim.opens()[1].t >= closed_at + RULES.lifecycle.cooldown_s


def test_track_id_switch_is_suppressed_by_open_event():
    sim = Sim()
    sim.run(100.0, 3, {7: NO_HELMET})
    sim.run(103.0, 1.5, {8: NO_HELMET})
    assert len(sim.opens()) == 1


def test_track_id_switch_is_suppressed_after_close_and_inherits_cooldown():
    sim = Sim()
    sim.run(100.0, 3, {7: NO_HELMET})
    sim.run(103.0, 30, {8: NO_HELMET})
    assert len(sim.opens()) == 1 and len(sim.closes()) == 1
    closed_at = sim.closes()[0].t
    sim.run(133.0, 40, {8: NO_HELMET})
    assert len(sim.opens()) == 2
    assert sim.opens()[1].t >= closed_at + RULES.lifecycle.cooldown_s
    assert sim.opens()[1].track_id == 8


def test_different_person_elsewhere_is_not_suppressed():
    sim = Sim()
    far = {"seen": ("no_helmet",), "bbox": (0.8, 0.2, 0.1, 0.4)}
    sim.run(100.0, 3, {7: NO_HELMET, 8: far})
    assert sorted(e.track_id for e in sim.opens()) == [7, 8]


def test_slightly_overlapping_person_is_not_suppressed():
    sim = Sim()
    near = {"seen": ("no_helmet",), "bbox": (0.47, 0.2, 0.1, 0.4)}
    sim.run(100.0, 3, {7: NO_HELMET, 8: near})
    assert len(sim.opens()) == 2


def test_different_event_types_on_one_track_are_independent():
    sim = Sim(camera(required=(PpeItem.helmet, PpeItem.mask, PpeItem.vest)))
    sim.run(100.0, 5, {7: {"seen": ("no_helmet", "no_mask"), "required": ALL}})
    assert sorted(e.type.value for e in sim.opens()) == ["no_helmet", "no_mask", "no_vest"]
    assert {e.type: e.severity for e in sim.opens()}[EventType.no_vest] == Severity.low


def test_evaluated_candidates_report_active_violations():
    sim = Sim()
    for i in range(8):
        _, evaluated = sim.frame(100.0 + i / FPS, {7: NO_HELMET, 8: CLEAN})
    assert [c.type for c in evaluated[7] if c.result.active] == [EventType.no_helmet]
    assert [c.type for c in evaluated[8] if c.result.active] == []


def test_restricted_zone_event():
    cam = camera(required=(), zones=[zone(3, ZoneType.restricted), zone(4, ZoneType.restricted)])
    sim = Sim(cam)
    sim.run(100.0, 0.5, {7: {"required": (), "zones": ()}})
    assert not sim.opens()
    sim.run(100.5, 2, {7: {"required": (), "zones": (4,)}})
    assert len(sim.opens()) == 1
    ev = sim.opens()[0]
    assert (ev.type, ev.severity, ev.zone_id, ev.track_id) == (EventType.zone_intrusion, Severity.high, 4, 7)
    assert ev.t == pytest.approx(100.5 + 6 / FPS)


def test_person_outside_restricted_zone_gives_no_event():
    sim = Sim(camera(required=(), zones=[zone(3, ZoneType.restricted)]))
    sim.run(100.0, 10, {7: {"required": (), "zones": ()}})
    assert not sim.opens()


def test_ppe_required_zone_is_reported_as_event_zone():
    cam = camera(required=(), zones=[zone(5, ZoneType.ppe_required, required=(PpeItem.helmet,))])
    sim = Sim(cam)
    sim.run(100.0, 5, {7: {"seen": ("no_helmet",), "required": (), "zones": ()}})
    assert not sim.opens()
    sim.run(105.0, 3, {7: {"seen": ("no_helmet",), "required": (PpeItem.helmet,), "zones": (5,)}})
    assert len(sim.opens()) == 1 and sim.opens()[0].zone_id == 5
    assert sim.opens()[0].t == pytest.approx(105.0 + 7 / FPS)


def test_camera_requirement_has_no_zone():
    cam = camera(required=(PpeItem.helmet,), zones=[zone(5, ZoneType.ppe_required, required=(PpeItem.helmet,))])
    sim = Sim(cam)
    sim.run(100.0, 3, {7: {"seen": ("no_helmet",), "zones": (5,)}})
    assert sim.opens()[0].zone_id is None


def smoking_sim(zones, rules=RULES, hits=3):
    sim = Sim(camera(required=(), zones=[zone(9, ZoneType.flammable)]), rules)
    sim.tracks[7] = TrackState(7)
    sim.tracks[7].checks.extend([(100.0 + i, True, 0.5) for i in range(hits)])
    sim.frame(102.0, {7: {"required": (), "zones": zones}})
    return sim


def test_smoking_in_flammable_zone_is_high():
    ev = smoking_sim((9,)).opens()[0]
    assert (ev.type, ev.severity, ev.zone_id) == (EventType.smoking, Severity.high, 9)
    assert not smoking_sim((9,), hits=2).opens()


FLAMMABLE_ONLY = RuleSettings(smoking=SmokingRule(everywhere=False))


def test_smoking_outside_flammable_zone_only_with_everywhere():
    assert not smoking_sim((), FLAMMABLE_ONLY).opens()
    ev = smoking_sim(()).opens()[0]
    assert (ev.type, ev.severity, ev.zone_id) == (EventType.smoking, Severity.medium, None)
    assert smoking_sim((9,)).opens()[0].severity == Severity.high


def test_smoking_event_closes_after_person_leaves_flammable_zone():
    sim = smoking_sim((9,), FLAMMABLE_ONLY)
    sim.run(102.2, 7, {7: {"required": (), "zones": ()}})
    assert len(sim.opens()) == 1 and len(sim.closes()) == 1
    assert sim.closes()[0].t == pytest.approx(107.0, abs=0.35)


def test_fire_event_lifecycle_and_camera_cooldown():
    engine = RuleEngine()
    hit = check_k_of_m([(0, True, 0.5)] * 3, RULES.fire)
    miss = check_k_of_m([(0, False, 0.0)] * 5, RULES.fire)
    box = (0.7, 0.5, 0.1, 0.12)
    a = engine.step_camera(100.0, EventType.fire, hit, box, RULES)
    assert len(a) == 1 and a[0].severity == Severity.high and a[0].track_id is None and a[0].bbox == box
    assert engine.step_camera(101.0, EventType.fire, hit, box, RULES) == []
    assert engine.step_camera(105.9, EventType.fire, miss, None, RULES) == []
    closed = engine.step_camera(106.0, EventType.fire, miss, None, RULES)
    assert len(closed) == 1 and isinstance(closed[0], EventClose) and closed[0].key == a[0].key
    assert engine.step_camera(120.0, EventType.fire, hit, box, RULES) == []
    assert len(engine.step_camera(120.0, EventType.smoke, hit, box, RULES)) == 1
    assert len(engine.step_camera(166.0, EventType.fire, hit, box, RULES)) == 1


def moving(speed, seconds, start=0.0, h=0.2, w=0.08, **kw):
    return [obs(start + i / FPS, bbox=(0.1 + speed * i / FPS, 0.5, w, h), **kw) for i in range(int(seconds * FPS))]


def test_running_fires_walking_does_not():
    run = moving(0.5, 3)  # 2.5 роста/с
    assert check_running(run, run[-1].t, RULES.running).active
    walk = moving(0.15, 3)  # 0.75 роста/с
    assert not check_running(walk, walk[-1].t, RULES.running).active


def test_short_sprint_does_not_fire():
    o = moving(0.0, 2) + moving(0.5, 0.8, start=2.0)
    assert not check_running(o, o[-1].t, RULES.running).active


def test_running_in_restricted_zone_is_high():
    eng, cam = RuleEngine(), camera(required=(), zones=[zone(4, ZoneType.restricted)])
    tr = TrackState(7)
    for o in moving(0.5, 3, zones=(4,)):
        tr.add(o)
    cands = {c.type: c for c in evaluate_track(tr, tr.last.t, cam, RULES)}
    assert cands[EventType.running].result.active and cands[EventType.running].severity == Severity.high


def near(vehicle, seconds, start=0.0, speed=1.0):
    return [obs(start + i / FPS, bbox=(0.3, 0.3, 0.08, 0.2), vehicles=(vehicle,), vehicle_speeds=(speed,))
            for i in range(int(seconds * FPS))]


def test_vehicle_proximity_moving_near_fires_far_does_not():
    o = near((0.42, 0.3, 0.3, 0.2), 3)  # рядом, ~0.4 роста
    assert check_vehicle_proximity(o, o[-1].t, RULES.vehicle_proximity).active
    o = near((0.8, 0.3, 0.15, 0.2), 3)  # ~2.5 роста
    assert not check_vehicle_proximity(o, o[-1].t, RULES.vehicle_proximity).active


def test_vehicle_proximity_standing_vehicle_does_not_fire():
    o = near((0.42, 0.3, 0.3, 0.2), 3, speed=0.0)
    assert not check_vehicle_proximity(o, o[-1].t, RULES.vehicle_proximity).active
    p = RULES.vehicle_proximity.model_copy(update={"min_vehicle_speed": 0.0})  # 0 — и стоящая считается
    assert check_vehicle_proximity(o, o[-1].t, p).active


def test_vehicle_proximity_unknown_speed_uses_distance():  # stills
    o = [obs(i / FPS, bbox=(0.3, 0.3, 0.08, 0.2), vehicles=((0.42, 0.3, 0.3, 0.2),)) for i in range(3 * FPS)]
    assert check_vehicle_proximity(o, o[-1].t, RULES.vehicle_proximity).active


def test_vehicle_proximity_short_episode_does_not_fire():
    o = near((0.8, 0.3, 0.15, 0.2), 2) + near((0.42, 0.3, 0.3, 0.2), 0.5, start=2.0)
    assert not check_vehicle_proximity(o, o[-1].t, RULES.vehicle_proximity).active


def test_vehicle_speeds_estimate():
    vs = VehicleSpeeds()
    for i in range(20):  # едет 50 px/с при высоте 100 -> 0.5
        ids, sp = vs.update(np.array([[100 + 5 * i, 100, 300 + 5 * i, 200], [600, 100, 800, 200]]), i / 10)
    assert abs(sp[0] - 0.5) < 0.05 and sp[1] < 0.01 and ids[0] != ids[1]


STAND = (0.4, 0.3, 0.1, 0.4)
LIE = (0.3, 0.6, 0.4, 0.1)
CROUCH = (0.4, 0.45, 0.1, 0.25)


def frames(boxes, start=0.0):
    return [obs(start + i / FPS, bbox=b) for i, b in enumerate(boxes)]


def test_fall_fires():
    o = frames([STAND] * 6 + [(0.35, 0.5, 0.25, 0.25)] + [LIE] * 3)
    assert check_fall(o, o[-1].t, RULES.fall).active


def test_crouch_or_brief_bend_is_not_fall():
    o = frames([STAND] * 6 + [CROUCH] * 6)
    assert not check_fall(o, o[-1].t, RULES.fall).active
    o = frames([STAND] * 6 + [(0.35, 0.45, 0.25, 0.25)] + [STAND] * 4)
    assert not check_fall(o, o[-1].t, RULES.fall).active


def test_lying_immobile_long_fires_short_does_not():
    o = frames([LIE] * (11 * FPS))
    assert check_lying_immobile(o, o[-1].t, RULES.lying_immobile).active
    o = frames([LIE] * (3 * FPS))
    assert not check_lying_immobile(o, o[-1].t, RULES.lying_immobile).active


def test_lying_but_crawling_is_not_immobile():
    o = [obs(i / FPS, bbox=(0.1 + 0.05 * i / FPS, 0.6, 0.4, 0.1)) for i in range(11 * FPS)]
    assert not check_lying_immobile(o, o[-1].t, RULES.lying_immobile).active


def test_retrack_matches_first_bbox_of_closed_event():
    # человек сдвинулся, событие закрылось; новый трек у начальной позиции в окне - события нет
    start, end = (0.2, 0.2, 0.1, 0.4), (0.7, 0.2, 0.1, 0.4)
    sim = Sim()
    sim.run(100.0, 2, {7: {**NO_HELMET, "bbox": start}})
    sim.run(102.0, 2, {7: {**NO_HELMET, "bbox": end}})
    assert len(sim.opens()) == 1
    sim.run(104.0, 3, {9: CLEAN})
    assert len(sim.closes()) == 1
    t = sim.closes()[0].t
    assert 107.0 - t < RULES.lifecycle.retrack_window_s
    sim.run(107.0, 2, {8: {**NO_HELMET, "bbox": start}})
    assert len(sim.opens()) == 1


def test_retrack_far_from_closed_event_opens_new():
    start, end, far = (0.2, 0.2, 0.1, 0.4), (0.7, 0.2, 0.1, 0.4), (0.45, 0.2, 0.1, 0.4)
    sim = Sim()
    sim.run(100.0, 2, {7: {**NO_HELMET, "bbox": start}})
    sim.run(102.0, 2, {7: {**NO_HELMET, "bbox": end}})
    sim.run(104.0, 3, {9: CLEAN})
    assert len(sim.closes()) == 1
    sim.run(107.0, 2, {8: {**NO_HELMET, "bbox": far}})
    assert len(sim.opens()) == 2 and sim.opens()[1].track_id == 8


def test_stills_camera_skips_motion_rules():
    def cands(seq, stills):
        tr = TrackState(7)
        for o in seq:
            tr.add(o)
        return {c.type: c for c in evaluate_track(tr, tr.last.t, camera(stills=stills), RULES)}

    lying = [obs(i / FPS, bbox=LIE, seen=("no_helmet",)) for i in range(11 * FPS)]
    assert cands(lying, False)[EventType.lying_immobile].result.active
    c = cands(lying, True)
    assert not {EventType.lying_immobile, EventType.fall, EventType.running} & c.keys()
    assert c[EventType.no_helmet].result.active  # каска на картинке считается
    assert EventType.fall not in cands(frames([STAND] * 6 + [(0.35, 0.5, 0.25, 0.25)] + [LIE] * 3), True)
    assert EventType.running not in cands(moving(0.5, 3), True)


def test_stills_camera_no_retrack_suppression():
    def opens(stills):
        sim = Sim(cam=camera(stills=stills))
        sim.run(100.0, 6, {7: NO_HELMET})
        sim.tracks.pop(7)
        sim.run(106.0, 1, {})  # трек 7 потерян, событие закрылось
        sim.run(108.0, 6, {8: NO_HELMET})  # та же рамка, новая картинка
        return len(sim.opens())

    assert opens(True) == 2
    assert opens(False) == 1
