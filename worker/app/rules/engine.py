from __future__ import annotations

import itertools
from dataclasses import dataclass

from shared.schemas import EventType, PpeItem, RuleSettings, Severity, WorkerCamera, ZoneType
from worker.app.association import iou_xywh
from worker.app.rules.checks import (RuleResult, check_fall, check_head_ppe, check_k_of_m, check_lying_immobile,
                                     check_no_vest, check_running, check_vehicle_proximity, check_zone)
from worker.app.rules.state import PPE_CLASSES, SEVERITY, TrackState
from worker.app.rules.types import EventClose, EventOpen

_keys = itertools.count(1)

BBox = tuple[float, float, float, float]


@dataclass(frozen=True)
class Candidate:
    type: EventType
    result: RuleResult
    severity: Severity
    bbox: BBox | None
    zone_id: int | None = None


@dataclass
class _Open:
    key: int
    type: EventType
    last_true: float
    bbox: BBox | None
    first_bbox: BBox | None = None


def _requirement_zone(camera: WorkerCamera, track: TrackState, item: PpeItem) -> int | None:
    if item in camera.required_ppe:
        return None
    for zone in camera.zones:
        if zone.id in track.last.zones and zone.type == ZoneType.ppe_required and item in zone.required_ppe:
            return zone.id
    return None


def evaluate_track(track: TrackState, t: float, camera: WorkerCamera, rules: RuleSettings) -> list[Candidate]:
    obs, bbox = track.obs, track.last.bbox
    out = []
    for item, event_type, params in ((PpeItem.helmet, EventType.no_helmet, rules.no_helmet),
                                     (PpeItem.mask, EventType.no_mask, rules.no_mask)):
        ok_cls, bad_cls, _ = PPE_CLASSES[item]
        out.append(Candidate(event_type, check_head_ppe(obs, t, item, ok_cls, bad_cls, params),
                             SEVERITY[event_type], bbox, _requirement_zone(camera, track, item)))
    out.append(Candidate(EventType.no_vest, check_no_vest(obs, t, PPE_CLASSES[PpeItem.vest][0], rules.no_vest),
                         SEVERITY[EventType.no_vest], bbox, _requirement_zone(camera, track, PpeItem.vest)))

    best: Candidate | None = None
    for zone in camera.zones:
        if zone.type != ZoneType.restricted:
            continue
        cand = Candidate(EventType.zone_intrusion, check_zone(obs, t, zone.id, rules.zone_intrusion),
                         SEVERITY[EventType.zone_intrusion], bbox, zone.id)
        if best is None or (cand.result.active, cand.result.fraction) > (best.result.active, best.result.fraction):
            best = cand
    if best is not None:
        out.append(best)

    flammable = {z.id for z in camera.zones if z.type == ZoneType.flammable}
    if track.checks and (rules.smoking.everywhere or track.last.zones & flammable):
        severity = Severity.high if track.last.zones & flammable else SEVERITY[EventType.smoking]
        zone_id = next(iter(sorted(track.last.zones & flammable)), None)
        out.append(Candidate(EventType.smoking, check_k_of_m(track.checks, rules.smoking), severity, bbox, zone_id))

    out.append(Candidate(EventType.vehicle_proximity, check_vehicle_proximity(obs, t, rules.vehicle_proximity),
                         SEVERITY[EventType.vehicle_proximity], bbox))
    if camera.stills:  # на картинке все неподвижны, движение не считаем
        return out
    restricted = {z.id for z in camera.zones if z.type == ZoneType.restricted}
    severity = Severity.high if track.last.zones & restricted else SEVERITY[EventType.running]
    zone_id = next(iter(sorted(track.last.zones & restricted)), None)
    out.append(Candidate(EventType.running, check_running(obs, t, rules.running), severity, bbox, zone_id))
    out.append(Candidate(EventType.fall, check_fall(obs, t, rules.fall), SEVERITY[EventType.fall], bbox))
    out.append(Candidate(EventType.lying_immobile, check_lying_immobile(obs, t, rules.lying_immobile),
                         SEVERITY[EventType.lying_immobile], bbox))
    return out


class RuleEngine:
    def __init__(self) -> None:
        self._open: dict[tuple[int | None, EventType], _Open] = {}
        self._closed: list[tuple[EventType, BBox | None, float]] = []
        self._not_before: dict[tuple[int | None, EventType], float] = {}

    def open_types(self, track_id: int | None) -> list[EventType]:
        return [typ for (tid, typ) in self._open if tid == track_id]

    def step(self, t: float, tracks: dict[int, TrackState], camera: WorkerCamera,
             rules: RuleSettings) -> tuple[list[EventOpen | EventClose], dict[int, list[Candidate]]]:
        life = rules.lifecycle
        self._closed = [c for c in self._closed if t - c[2] < life.retrack_window_s]
        actions: list[EventOpen | EventClose] = []
        evaluated: dict[int, list[Candidate]] = {}

        for track_id, track in tracks.items():
            if not track.obs or track.last.t != t:
                continue
            evaluated[track_id] = evaluate_track(track, t, camera, rules)
            for cand in evaluated[track_id]:
                if cand.result.active:
                    self._activate((track_id, cand.type), cand, t, life.cooldown_s, life, actions,
                                   retrack=not camera.stills)  # на слайдшоу каждая картинка новая сцена

        for key, op in list(self._open.items()):
            track_id = key[0]
            if track_id is None:  # камерные события закрывает step_camera
                continue
            track = tracks.get(track_id)
            lost = track is None or not track.obs or t - track.last.t >= life.track_lost_s
            if lost or t - op.last_true >= life.close_after_s:
                self._close(key, t, life.cooldown_s, actions)
        return actions, evaluated

    def step_camera(self, t: float, event_type: EventType, result: RuleResult, bbox: BBox | None,
                    rules: RuleSettings, stills: bool = False) -> list[EventOpen | EventClose]:
        life = rules.lifecycle
        cooldown = 0.0 if stills else life.camera_cooldown_s  # картинки сменяются, кулдаун не нужен
        key = (None, event_type)
        actions: list[EventOpen | EventClose] = []
        if result.active:
            cand = Candidate(event_type, result, SEVERITY[event_type], bbox)
            self._activate(key, cand, t, cooldown, life, actions, retrack=False)
        op = self._open.get(key)
        if op and t - op.last_true >= life.close_after_s:
            self._close(key, t, cooldown, actions)
        return actions

    def _activate(self, key, cand: Candidate, t: float, cooldown_s: float, life, actions, retrack: bool) -> None:
        op = self._open.get(key)
        if op:
            op.last_true, op.bbox = t, cand.bbox
            return
        if t < self._not_before.get(key, float("-inf")):
            return
        # трекер дал новый id тому же человеку - не плодим событие
        if retrack and cand.bbox is not None:
            for (other_id, typ), other in self._open.items():
                if typ == cand.type and other_id != key[0] and other.bbox and iou_xywh(cand.bbox, other.bbox) > life.retrack_iou:
                    return
            # сверяем и с концом и с началом закрытого: человек мог уйти, а потом вернуться туда же
            for typ, bbox, closed_t in self._closed:
                if typ == cand.type and bbox and iou_xywh(cand.bbox, bbox) > life.retrack_iou:
                    self._not_before[key] = closed_t + cooldown_s
                    return
        new = _Open(next(_keys), cand.type, t, cand.bbox, cand.bbox)
        self._open[key] = new
        actions.append(EventOpen(key=new.key, type=cand.type, severity=cand.severity, t=t,
                                 confidence=round(cand.result.confidence, 3), bbox=cand.bbox, track_id=key[0],
                                 zone_id=cand.zone_id, details=cand.result.details))

    def _close(self, key, t: float, cooldown_s: float, actions) -> None:
        op = self._open.pop(key)
        self._closed.append((op.type, op.bbox, t))
        if op.first_bbox and op.first_bbox != op.bbox:
            self._closed.append((op.type, op.first_bbox, t))
        self._not_before[key] = t + cooldown_s
        actions.append(EventClose(op.key, t))
