from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from shared.schemas import FractionRule, KofMRule, NoVestRule, PpeItem, ZoneIntrusionRule
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


# smoking/fire/smoke: k из последних m проверок
def check_k_of_m(checks: Sequence[tuple[float, bool, float]], p: KofMRule) -> RuleResult:
    last = list(checks)[-p.m:]
    hits = [c[2] for c in last if c[1]]
    if not last:
        return INACTIVE
    return RuleResult(len(hits) >= p.k, len(hits) / len(last), len(last), sum(hits) / len(hits) if hits else 0.0, 0.0)
