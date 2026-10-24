from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable, Sequence

from shared.schemas import FallRule, FractionRule, LyingImmobileRule, KofMRule, NoVestRule, PpeItem, RunningRule, VehicleProximityRule, ZoneIntrusionRule
from worker.app.rules.state import Observation


@dataclass(frozen=True)
class RuleResult:
    active: bool
    fraction: float
    observations: int
    confidence: float
    window_s: float

    @property
    def details(self) -> dict:
        return {"fraction": round(self.fraction, 2), "window_s": self.window_s, "observations": self.observations}


INACTIVE = RuleResult(False, 0.0, 0, 0.0, 0.0)


def in_window(obs: Iterable[Observation], t: float, window_s: float) -> list[Observation]:
    return [o for o in obs if t - window_s <= o.t <= t]


def check_head_ppe(obs: Sequence[Observation], t: float, item: PpeItem, ok_cls: str, bad_cls: str,
                   p: FractionRule) -> RuleResult:
    if not obs or item not in obs[-1].required:
        return INACTIVE
    w = [o for o in in_window(obs, t, p.window_s) if item in o.required]
    n = len(w)
    if n == 0:
        return INACTIVE
    bad = [o.seen[bad_cls] for o in w if bad_cls in o.seen]
    ok = sum(1 for o in w if ok_cls in o.seen)
    fraction = len(bad) / n
    active = n >= p.min_observations and fraction >= p.fraction and len(bad) > ok
    return RuleResult(active, fraction, n, sum(bad) / len(bad) if bad else 0.0, p.window_s)


# мелкие рамки не считаем, там жилет не разглядеть
def check_no_vest(obs: Sequence[Observation], t: float, vest_cls: str, p: NoVestRule) -> RuleResult:
    if not obs or PpeItem.vest not in obs[-1].required or obs[-1].height_px < p.min_person_height_px:
        return INACTIVE
    w = [o for o in in_window(obs, t, p.window_s)
         if PpeItem.vest in o.required and o.height_px >= p.min_person_height_px]
    n = len(w)
    if n == 0:
        return INACTIVE
    fraction = sum(1 for o in w if vest_cls in o.seen) / n
    active = n >= p.min_observations and fraction <= p.max_fraction
    return RuleResult(active, fraction, n, 1.0 - fraction, p.window_s)


def check_zone(obs: Sequence[Observation], t: float, zone_id: int, p: ZoneIntrusionRule) -> RuleResult:
    w = in_window(obs, t, p.window_s)
    n = len(w)
    if n == 0:
        return INACTIVE
    fraction = sum(1 for o in w if zone_id in o.zones) / n
    active = n >= p.min_observations and fraction >= p.fraction
    return RuleResult(active, fraction, n, fraction, p.window_s)


def in_upper_part(person: tuple[float, float, float, float], box: tuple[float, float, float, float],
                  upper_fraction: float) -> bool:
    px, py, pw, ph = person
    cx, cy = box[0] + box[2] / 2, box[1] + box[3] / 2
    return px <= cx <= px + pw and py <= cy <= py + ph * upper_fraction


# smoking/fire/smoke: k из последних m проверок
def check_k_of_m(checks: Sequence[tuple[float, bool, float]], p: KofMRule) -> RuleResult:
    last = list(checks)[-p.m:]
    hits = [c[2] for c in last if c[1]]
    if not last:
        return INACTIVE
    return RuleResult(len(hits) >= p.k, len(hits) / len(last), len(last), sum(hits) / len(hits) if hits else 0.0, 0.0)


def foot(b: tuple[float, float, float, float]) -> tuple[float, float]:
    return b[0] + b[2] / 2, b[1] + b[3]


SPEED_LAG_S = 0.5  # скорость меряем на отрезке ~0.5с, по соседним кадрам слишком шумит


# скорость в ростах человека в секунду
def speed_heights(obs: Sequence[Observation], i: int) -> float | None:
    o = obs[i]
    prev = next((p for p in reversed(list(obs)[:i]) if o.t - p.t >= SPEED_LAG_S), None)
    if prev is None or o.t - prev.t > SPEED_LAG_S * 3:
        return None
    (x0, y0), (x1, y1) = foot(prev.bbox), foot(o.bbox)
    height = (prev.bbox[3] + o.bbox[3]) / 2
    if height <= 0:
        return None
    return math.hypot((x1 - x0) * o.aspect, y1 - y0) / height / (o.t - prev.t)


def check_running(obs: Sequence[Observation], t: float, p: RunningRule) -> RuleResult:
    speeds = [s for i, o in enumerate(obs) if t - p.window_s <= o.t <= t
              for s in [speed_heights(obs, i)] if s is not None]
    n = len(speeds)
    if n == 0:
        return INACTIVE
    fast = [s for s in speeds if s >= p.speed_heights_s]
    fraction = len(fast) / n
    active = n >= p.min_observations and fraction >= p.fraction
    conf = min(1.0, sum(fast) / len(fast) / (2 * p.speed_heights_s)) if fast else 0.0
    return RuleResult(active, fraction, n, conf, p.window_s)


# от ног человека до нижней кромки рамки техники, в ростах. у длинной фуры центр далеко, поэтому до кромки
def vehicle_distance(o: Observation, min_speed: float = 0.0) -> float | None:
    if not o.vehicles or o.bbox[3] <= 0:
        return None
    fx, fy = foot(o.bbox)
    best = None
    for k, (vx, vy, vw, vh) in enumerate(o.vehicles):
        # стоящая техника не опасна; скорость неизвестна (stills) — считаем по близости
        if o.vehicle_speeds and o.vehicle_speeds[k] < min_speed:
            continue
        dx = (min(max(fx, vx), vx + vw) - fx) * o.aspect
        d = math.hypot(dx, vy + vh - fy) / o.bbox[3]
        best = d if best is None else min(best, d)
    return best


def check_vehicle_proximity(obs: Sequence[Observation], t: float, p: VehicleProximityRule) -> RuleResult:
    w = in_window(obs, t, p.window_s)
    n = len(w)
    if n == 0 or not obs[-1].vehicles:
        return INACTIVE
    dists = [vehicle_distance(o, p.min_vehicle_speed) for o in w]
    close = [d for d in dists if d is not None and d < p.distance_heights]
    fraction = len(close) / n
    active = n >= p.min_observations and fraction >= p.fraction
    conf = 1.0 - min(close) / p.distance_heights if close else 0.0
    return RuleResult(active, fraction, n, max(conf, 0.5) if active else conf, p.window_s)


# h/w рамки в пикселях
def tallness(o: Observation) -> float:
    return o.bbox[3] / (o.bbox[2] * o.aspect) if o.bbox[2] > 0 else 0.0


LYING_TORSO_DEG = 55.0


# угол корпуса от вертикали: середина плеч -> середина бёдер, coco 5,6 / 11,12. None если точки не видны
def torso_angle(o: Observation, min_vis: float = 0.5) -> float | None:
    k = o.keypoints
    if not k:
        return None
    sh = [k[i] for i in (5, 6) if k[i][2] >= min_vis]
    hp = [k[i] for i in (11, 12) if k[i][2] >= min_vis]
    if not sh or not hp:
        return None
    dx = (sum(p[0] for p in hp) / len(hp) - sum(p[0] for p in sh) / len(sh)) * o.aspect
    dy = sum(p[1] for p in hp) / len(hp) - sum(p[1] for p in sh) / len(sh)
    if dx == 0 and dy == 0:
        return None
    return math.degrees(math.atan2(abs(dx), dy))  # голова вниз = 180


# лежит: по позе если есть, иначе по рамке как раньше
def is_lying(o: Observation, lying_ratio: float) -> bool:
    a = torso_angle(o)
    if a is not None:
        return a > LYING_TORSO_DEG
    return tallness(o) > 0 and 1 / tallness(o) >= lying_ratio


# стоял -> лег за window_s и центр рамки резко ушел вниз. эвристика по bbox, позы нет
def check_fall(obs: Sequence[Observation], t: float, p: FallRule) -> RuleResult:
    w = in_window(obs, t, p.window_s)
    upright = [i for i, o in enumerate(w) if tallness(o) >= p.upright_ratio]
    if not upright:
        return INACTIVE
    stood = w[upright[-1]]
    after = w[upright[-1] + 1:]
    lying = [o for o in after if is_lying(o, p.lying_ratio)]
    if not after or not lying or lying[-1] is not after[-1]:
        return INACTIVE
    drop = ((w[-1].bbox[1] + w[-1].bbox[3] / 2) - (stood.bbox[1] + stood.bbox[3] / 2)) / stood.bbox[3]
    fraction = len(lying) / len(after)
    active = len(lying) >= p.min_observations and fraction >= 0.8 and drop >= p.drop_heights
    return RuleResult(active, fraction, len(after), min(1.0, max(drop, 0.0) / (2 * p.drop_heights)), p.window_s)


def check_lying_immobile(obs: Sequence[Observation], t: float, p: LyingImmobileRule) -> RuleResult:
    w = in_window(obs, t, p.window_s)
    n = len(w)
    if n == 0 or w[-1].t - w[0].t < p.window_s * 0.9:
        return INACTIVE
    lying = [o for o in w if is_lying(o, p.lying_ratio)]
    fraction = len(lying) / n
    if not lying:
        return RuleResult(False, 0.0, n, 0.0, p.window_s)
    a, b = lying[0], lying[-1]
    size = max(b.bbox[2] * b.aspect, b.bbox[3])
    dt = max(b.t - a.t, 1e-6)
    (x0, y0), (x1, y1) = foot(a.bbox), foot(b.bbox)
    speed = math.hypot((x1 - x0) * b.aspect, y1 - y0) / size / dt
    active = n >= p.min_observations and fraction >= p.fraction and speed < p.max_speed
    return RuleResult(active, fraction, n, fraction, p.window_s)


# то же по месту в кадре, а не по треку: рамка лежащего мерцает и трек рвётся, а человек лежит там же
def check_lying_spot(frames: Sequence[tuple[float, list[tuple[float, float, float, float]]]], t: float, aspect: float,
                     p: LyingImmobileRule) -> tuple[RuleResult, tuple[float, float, float, float] | None]:
    w = [f for f in frames if t - f[0] <= p.window_s]
    if not w or w[-1][0] - w[0][0] < p.window_s * 0.9:
        return INACTIVE, None
    ref = next((boxes[-1] for _, boxes in reversed(w) if boxes), None)
    if ref is None:
        return RuleResult(False, 0.0, len(w), 0.0, p.window_s), None
    size = max(ref[2] * aspect, ref[3])
    cx, cy = ref[0] + ref[2] / 2, ref[1] + ref[3] / 2

    def here(b) -> bool:
        return math.hypot((b[0] + b[2] / 2 - cx) * aspect, b[1] + b[3] / 2 - cy) < p.spot_radius * size

    hits = sum(1 for _, boxes in w if any(here(b) for b in boxes))
    fraction = hits / len(w)
    active = hits >= p.min_observations and fraction >= p.spot_fraction
    return RuleResult(active, fraction, len(w), fraction, p.window_s), ref


# ось полосы ограждения: самое длинное ребро, x в пикселях через aspect
def _strip_axes(polygon, aspect: float):
    pts = [(x * aspect, y) for x, y in polygon]
    cx, cy = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
    best = max(range(len(pts)), key=lambda i: math.dist(pts[i], pts[(i + 1) % len(pts)]))
    (x1, y1), (x2, y2) = pts[best], pts[(best + 1) % len(pts)]
    L = math.hypot(x2 - x1, y2 - y1) or 1.0
    ax, ay = (x2 - x1) / L, (y2 - y1) / L
    along = [(p[0] - cx) * ax + (p[1] - cy) * ay for p in pts]
    return (cx, cy), (ax, ay), (min(along), max(along))


# перелез: ноги были по одну сторону полосы, потом по другую (в пределах длины полосы),
# либо топчется внутри полосы >= min_observations кадров. проход вдоль - одна сторона, не считаем
def check_barrier(obs: Sequence[Observation], t: float, zone_id: int, polygon, p) -> RuleResult:
    w = in_window(obs, t, p.window_s)
    n = len(w)
    if n == 0:
        return INACTIVE
    (cx, cy), (ax, ay), (lo, hi) = _strip_axes(polygon, w[-1].aspect)
    inside = sum(1 for o in w if zone_id in o.zones)
    crossed, last_side = False, 0
    for o in w:
        if zone_id in o.zones:
            continue
        fx, fy = foot(o.bbox)
        dx, dy = fx * o.aspect - cx, fy - cy
        if not lo <= dx * ax + dy * ay <= hi:
            continue  # обошел полосу с торца - не перелез
        side = 1 if dx * -ay + dy * ax > 0 else -1
        if last_side and side != last_side:
            crossed = True
        last_side = side
    fraction = inside / n
    active = crossed or inside >= p.min_observations
    return RuleResult(active, fraction, n, 1.0 if crossed else fraction, p.window_s)


# 06: человек в зоне высоты, привязь почти не видна.
# моделей с классом harness пока нет -> по факту срабатывает на любого в зоне, это "контроль зоны высоты"
def check_harness(obs: Sequence[Observation], t: float, zone_id: int, harness_cls: str, p) -> RuleResult:
    if not obs or zone_id not in obs[-1].zones:
        return INACTIVE
    w = [o for o in in_window(obs, t, p.window_s) if zone_id in o.zones]
    n = len(w)
    if n == 0:
        return INACTIVE
    fraction = sum(1 for o in w if harness_cls in o.seen) / n
    active = n >= p.min_observations and fraction < p.max_fraction
    return RuleResult(active, fraction, n, 1.0 - fraction, p.window_s)
