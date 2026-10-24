from __future__ import annotations

from collections import deque

import numpy as np

from shared.schemas import (DetectionsMessage, EventType, FrameSize, ObjectBox, PersonBox, PpeItem, PpeStatus,
                            RuleSettings, WorkerCamera, ZoneType)
from worker.app.association import anchor, assign_items, point_in_polygon
from worker.app.model_spec import ModelSpec
from worker.app.postprocess import Detections
from worker.app.crops import crop_box
from worker.app.rules.checks import check_k_of_m, in_upper_part, in_window
from worker.app.rules.engine import Candidate, RuleEngine
from worker.app.rules.state import (ATTACHABLE, CIGARETTE, HAZARD_EVENTS, HEAD_CLASSES, PERSON, PPE_CLASSES,
                                    VEHICLE, Observation, TrackState)
from worker.app.rules.types import EventClose, EventOpen
from worker.app.tracking import IouTracker

EVENT_ITEM = {EventType.no_helmet: PpeItem.helmet, EventType.no_mask: PpeItem.mask, EventType.no_vest: PpeItem.vest}


def norm_box(xyxy: np.ndarray, w: int, h: int) -> tuple[float, float, float, float]:
    x1, y1, x2, y2 = (float(v) for v in xyxy)
    return round(x1 / w, 4), round(y1 / h, 4), round((x2 - x1) / w, 4), round((y2 - y1) / h, 4)


def check_classes(spec: ModelSpec, hazard: ModelSpec | None = None) -> None:
    for name in (PERSON, *sorted(ATTACHABLE)):
        spec.class_id(name)
    if hazard is not None:
        for name in (CIGARETTE, *HAZARD_EVENTS):
            hazard.class_id(name)


CHECKS_KEPT = 30
HAZARD_VISIBLE_S = 2.5  # сколько держим рамки огня/дыма на плеере
RECENT_S = 0.6


def ppe_status(track: TrackState, t: float, item: PpeItem, candidates: list[Candidate],
               rules: RuleSettings) -> PpeStatus:
    if item not in track.last.required:
        return PpeStatus.not_required
    event_type = next(e for e, i in EVENT_ITEM.items() if i == item)
    if any(c.type == event_type and c.result.active for c in candidates):
        return PpeStatus.violation
    ok_cls, bad_cls, _ = PPE_CLASSES[item]
    window = {PpeItem.helmet: rules.no_helmet.window_s, PpeItem.mask: rules.no_mask.window_s,
              PpeItem.vest: rules.no_vest.window_s}[item]
    obs = in_window(track.obs, t, window)
    ok = sum(1 for o in obs if ok_cls in o.seen)
    bad = sum(1 for o in obs if bad_cls and bad_cls in o.seen)
    return PpeStatus.ok if ok > 0 and ok >= bad else PpeStatus.unknown


class CameraPipeline:
    def __init__(self) -> None:
        self.tracker = IouTracker()
        self.tracks: dict[int, TrackState] = {}
        self.engine = RuleEngine()
        self.hazard_checks = {name: deque(maxlen=CHECKS_KEPT) for name in HAZARD_EVENTS}
        self._hazard_objects: list[ObjectBox] = []
        self._cigarette_objects: dict[int, ObjectBox] = {}
        self._hazard_t = 0.0
        self._crop_cursor = 0


    def process_hazard(self, dets: Detections, spec: ModelSpec, w: int, h: int, ts_ms: int,
                       rules: RuleSettings, stills: bool = False) -> list[EventOpen | EventClose]:
        t = ts_ms / 1000
        names = [spec.classes[c] for c in dets.cls]
        self._hazard_t = t
        self._hazard_objects = []
        actions: list[EventOpen | EventClose] = []
        for name, event_type in HAZARD_EVENTS.items():
            found = [i for i, n in enumerate(names) if n == name]
            best = max(found, key=lambda i: dets.conf[i], default=None)
            self.hazard_checks[name].append((t, best is not None, float(dets.conf[best]) if best is not None else 0.0))
            bbox = norm_box(dets.xyxy[best], w, h) if best is not None else None
            for i in found:
                self._hazard_objects.append(ObjectBox(cls=name, bbox=list(norm_box(dets.xyxy[i], w, h)),
                                                      conf=round(float(dets.conf[i]), 3)))
            params = getattr(rules, event_type.value)
            actions += self.engine.step_camera(t, event_type, check_k_of_m(self.hazard_checks[name], params), bbox, rules,
                                               stills)
        return actions


    def select_crops(self, t: float, w: int, h: int, max_per_tick: int, top_fraction: float, margin: float,
                     zones: frozenset[int] | None = None) -> list[tuple[int, tuple[int, int, int, int]]]:
        ids = sorted(tid for tid, tr in self.tracks.items() if tr.obs and t - tr.last.t <= RECENT_S
                     and (zones is None or tr.last.zones & zones))
        if not ids or max_per_tick <= 0:
            return []
        start = self._crop_cursor % len(ids)
        chosen = (ids[start:] + ids[:start])[:max_per_tick]
        self._crop_cursor = (start + len(chosen)) % len(ids)
        out = []
        for track_id in chosen:
            x, y, bw, bh = self.tracks[track_id].last.bbox
            box = crop_box((x * w, y * h, (x + bw) * w, (y + bh) * h), top_fraction, margin, w, h)
            if box is not None:
                out.append((track_id, box))
        return out

    def apply_cigarette(self, track_id: int, t: float, found: list[tuple[float, tuple[float, float, float, float]]],
                        upper_fraction: float) -> None:
        track = self.tracks.get(track_id)
        if track is None or not track.obs:
            return
        near_face = [f for f in found if in_upper_part(track.last.bbox, f[1], upper_fraction)]
        conf, bbox = max(near_face, key=lambda f: f[0], default=(0.0, None))
        track.checks.append((t, bbox is not None, conf))
        while len(track.checks) > CHECKS_KEPT:
            track.checks.popleft()
        if bbox is not None:
            self._cigarette_objects[track_id] = ObjectBox(cls=CIGARETTE, bbox=list(bbox), conf=round(conf, 3), track_id=track_id)
        else:
            self._cigarette_objects.pop(track_id, None)


    def process_ppe(self, dets: Detections, spec: ModelSpec, w: int, h: int, ts_ms: int, camera: WorkerCamera,
                    rules: RuleSettings, px_scale: float = 1.0) -> tuple[DetectionsMessage, list[EventOpen | EventClose]]:
        t = ts_ms / 1000
        names = [spec.classes[c] for c in dets.cls]
        person_idx = [i for i, n in enumerate(names) if n == PERSON]
        item_idx = [i for i, n in enumerate(names) if n in ATTACHABLE]

        people_xyxy = dets.xyxy[person_idx]
        track_ids = self.tracker.update(people_xyxy, t)
        owner = assign_items(people_xyxy, dets.xyxy[item_idx], [names[i] in HEAD_CLASSES for i in item_idx])
        owner_of = dict(zip(item_idx, owner))  # индекс сиз -> индекс человека или -1
        vehicles = tuple(norm_box(dets.xyxy[i], w, h) for i, n in enumerate(names) if n == VEHICLE)

        for p, det_i in enumerate(person_idx):
            bbox = norm_box(dets.xyxy[det_i], w, h)
            ax, ay = anchor(bbox)
            zones = frozenset(z.id for z in camera.zones if point_in_polygon(ax, ay, z.polygon))
            required = set(camera.required_ppe)
            for z in camera.zones:
                if z.id in zones and z.type == ZoneType.ppe_required:
                    required |= set(z.required_ppe)
            seen: dict[str, float] = {}
            for i, own in owner_of.items():
                if own == p:
                    seen[names[i]] = max(seen.get(names[i], 0.0), float(dets.conf[i]))
            track = self.tracks.setdefault(int(track_ids[p]), TrackState(int(track_ids[p])))
            track.add(Observation(t=t, bbox=bbox, height_px=float(dets.xyxy[det_i][3] - dets.xyxy[det_i][1]) * px_scale,
                                  conf=float(dets.conf[det_i]), seen=seen, zones=zones, required=frozenset(required),
                                  vehicles=vehicles, aspect=w / h if h else 1.0))

        actions, evaluated = self.engine.step(t, self.tracks, camera, rules)
        horizon = max(rules.lifecycle.track_lost_s, self.tracker.max_age_s)
        for track_id in [tid for tid, tr in self.tracks.items() if t - tr.last.t > horizon]:
            del self.tracks[track_id]

        people = []
        for p, det_i in enumerate(person_idx):
            track = self.tracks[int(track_ids[p])]
            cands = evaluated.get(track.track_id, [])
            people.append(PersonBox(
                track_id=track.track_id, bbox=list(track.last.bbox), conf=round(float(dets.conf[det_i]), 3),
                ppe={item: ppe_status(track, t, item, cands, rules) for item in PPE_CLASSES},
                zones=sorted(track.last.zones),
                violations=[c.type for c in cands if c.result.active]))

        objects = []
        for i, name in enumerate(names):
            if name == PERSON:
                continue
            own = owner_of.get(i, -1)
            objects.append(ObjectBox(cls=name, bbox=list(norm_box(dets.xyxy[i], w, h)), conf=round(float(dets.conf[i]), 3),
                                     track_id=int(track_ids[own]) if own >= 0 else None))

        if t - self._hazard_t <= HAZARD_VISIBLE_S:
            objects += self._hazard_objects
            objects += [o for tid, o in self._cigarette_objects.items() if tid in self.tracks]
        self._cigarette_objects = {tid: o for tid, o in self._cigarette_objects.items() if tid in self.tracks}

        msg = DetectionsMessage(camera_id=camera.id, ts=ts_ms, frame=FrameSize(w=w, h=h), people=people, objects=objects)
        return msg, actions
