from __future__ import annotations

import numpy as np

from shared.schemas import (DetectionsMessage, EventType, FrameSize, ObjectBox, PersonBox, PpeItem, PpeStatus,
                            RuleSettings, WorkerCamera, ZoneType)
from worker.app.association import anchor, assign_items, point_in_polygon
from worker.app.model_spec import ModelSpec
from worker.app.postprocess import Detections
from worker.app.rules.checks import in_window
from worker.app.rules.engine import Candidate, RuleEngine
from worker.app.rules.state import ATTACHABLE, HEAD_CLASSES, PERSON, PPE_CLASSES, Observation, TrackState
from worker.app.rules.types import EventClose, EventOpen
from worker.app.tracking import IouTracker

EVENT_ITEM = {EventType.no_helmet: PpeItem.helmet, EventType.no_mask: PpeItem.mask, EventType.no_vest: PpeItem.vest}


def norm_box(xyxy: np.ndarray, w: int, h: int) -> tuple[float, float, float, float]:
    x1, y1, x2, y2 = (float(v) for v in xyxy)
    return round(x1 / w, 4), round(y1 / h, 4), round((x2 - x1) / w, 4), round((y2 - y1) / h, 4)


def check_classes(spec: ModelSpec) -> None:
    for name in (PERSON, *sorted(ATTACHABLE)):
        spec.class_id(name)


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

    def process_ppe(self, dets: Detections, spec: ModelSpec, w: int, h: int, ts_ms: int, camera: WorkerCamera,
                    rules: RuleSettings) -> tuple[DetectionsMessage, list[EventOpen | EventClose]]:
        t = ts_ms / 1000
        names = [spec.classes[c] for c in dets.cls]
        person_idx = [i for i, n in enumerate(names) if n == PERSON]
        item_idx = [i for i, n in enumerate(names) if n in ATTACHABLE]

        people_xyxy = dets.xyxy[person_idx]
        track_ids = self.tracker.update(people_xyxy, t)
        owner = assign_items(people_xyxy, dets.xyxy[item_idx], [names[i] in HEAD_CLASSES for i in item_idx])
        owner_of = dict(zip(item_idx, owner))  # индекс сиз -> индекс человека или -1

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
            track.add(Observation(t=t, bbox=bbox, height_px=float(dets.xyxy[det_i][3] - dets.xyxy[det_i][1]),
                                  conf=float(dets.conf[det_i]), seen=seen, zones=zones, required=frozenset(required)))

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

        msg = DetectionsMessage(camera_id=camera.id, ts=ts_ms, frame=FrameSize(w=w, h=h), people=people, objects=objects)
        return msg, actions
