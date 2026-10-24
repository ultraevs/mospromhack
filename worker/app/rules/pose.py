from __future__ import annotations

import math
from typing import Sequence

from shared.schemas import CarryRule, PhoneRule
from worker.app.rules.checks import INACTIVE, RuleResult, in_upper_part, in_window, speed_heights, torso_angle
from worker.app.rules.state import Observation

# coco 17: 0 нос, 3/4 уши, 5/6 плечи, 9/10 кисти, 11/12 бёдра
EARS, SHOULDERS, WRISTS = (3, 4), (5, 6), (9, 10)
MIN_VIS = 0.5
NEAR_HEIGHTS = 0.12  # телефон у кисти/уха: ближе 0.12 роста
BENT_DEG = 60.0


def _pts(o: Observation, idx) -> list[tuple[float, float]]:
    return [(o.keypoints[i][0], o.keypoints[i][1]) for i in idx if o.keypoints[i][2] >= MIN_VIS]


def phone_near(o: Observation, upper_fraction: float) -> float:
    if not o.phones:
        return 0.0
    for ph in o.phones:
        if in_upper_part(o.bbox, ph, upper_fraction):
            return 1.0
        if o.keypoints and o.bbox[3] > 0:
            cx, cy = ph[0] + ph[2] / 2, ph[1] + ph[3] / 2
            for x, y in _pts(o, WRISTS + EARS):
                if math.hypot((x - cx) * o.aspect, y - cy) / o.bbox[3] <= NEAR_HEIGHTS:
                    return 1.0
    return 0.0


def check_phone(obs: Sequence[Observation], t: float, p: PhoneRule) -> RuleResult:
    w = in_window(obs, t, p.window_s)
    n = len(w)
    if n == 0 or not obs[-1].phones:
        return INACTIVE
    fraction = sum(phone_near(o, p.upper_fraction) for o in w) / n
    active = n >= p.min_observations and fraction >= p.fraction
    return RuleResult(active, fraction, n, fraction, p.window_s)


# обе кисти выше плеч (груз на голове/плечах)
def hands_up(o: Observation) -> bool:
    if not o.keypoints:
        return False
    k = o.keypoints
    if any(k[i][2] < MIN_VIS for i in WRISTS + SHOULDERS):
        return False
    shoulder_y = min(k[5][1], k[6][1])
    return k[9][1] < shoulder_y and k[10][1] < shoulder_y


# приближение паттерна 08: груза мы не видим, ловим позу «несёт над головой» или «идёт согнувшись»
def check_unsafe_carry(obs: Sequence[Observation], t: float, p: CarryRule) -> RuleResult:
    seen = []
    for i, o in enumerate(obs):
        if not (t - p.window_s <= o.t <= t) or not o.keypoints:
            continue
        s = speed_heights(obs, i)
        if s is None:
            continue
        a = torso_angle(o)
        seen.append(s >= p.min_speed_heights_s and (hands_up(o) or (a is not None and a > BENT_DEG)))
    n = len(seen)
    if n == 0:
        return INACTIVE
    fraction = sum(seen) / n
    active = n >= p.min_observations and fraction >= p.fraction
    return RuleResult(active, fraction, n, fraction, p.window_s)
