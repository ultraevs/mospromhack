import numpy as np

from shared.schemas import CarryRule, EventType, LyingImmobileRule, PhoneRule, RuleSettings, WorkerCamera
from worker.app.model_spec import ModelSpec
from worker.app.postprocess import LetterboxInfo, decode, decode_pose
from worker.app.rules.checks import check_lying_immobile, is_lying, torso_angle
from worker.app.rules.engine import evaluate_track
from worker.app.rules.pose import check_phone, check_unsafe_carry, hands_up
from worker.app.rules.state import Observation, TrackState

FPS = 6
BOX = (0.4, 0.2, 0.1, 0.4)


def kp(shoulder=(0.45, 0.3), hip=(0.45, 0.45), wrist_y=0.45, vis=0.9):
    k = [(0.45, 0.25, vis)] * 17
    k[5] = k[6] = (*shoulder, vis)
    k[11] = k[12] = (*hip, vis)
    k[9] = k[10] = (0.45, wrist_y, vis)
    return tuple(k)


def obs(t, bbox=BOX, phones=(), keypoints=None):
    return Observation(t=t, bbox=bbox, height_px=300.0, conf=0.9, seen={}, zones=frozenset(), required=frozenset(),
                       phones=phones, keypoints=keypoints)


def spec(name="pose", classes=("person",), kpt=17):
    return ModelSpec(name, classes, np.full(len(classes), 0.4, np.float32), 0.7, 640, None, kpt)  # type: ignore[arg-type]


def test_decode_pose_synthetic():
    out = np.zeros((56, 100), np.float32)
    out[:4, 3] = [320, 320, 100, 200]
    out[4, 3] = 0.9
    out[:4, 4] = [322, 322, 100, 200]  # дубль, nms уберёт
    out[4, 4] = 0.6
    out[5:, 3] = np.tile([330, 260, 0.95], 17)
    info = LetterboxInfo(scale=0.5, pad_x=0, pad_y=140, src_w=1280, src_h=720)
    boxes, conf, kpts = decode_pose(out, spec(), info)
    assert boxes.shape == (1, 4) and kpts.shape == (1, 17, 3)
    assert np.allclose(boxes[0], [540, 160, 740, 560])
    assert np.allclose(kpts[0, 0], [660, 240, 0.95])


def test_decode_pads_linq_people_without_phone():
    people = spec("people", ("person", "car", "motorcycle", "bus", "truck", "cell phone"), 0)
    out = np.zeros((9, 10), np.float32)  # программа linq под 5 классов
    out[:4, 0] = [100, 100, 20, 40]
    out[4, 0] = 0.9
    d = decode(out, people, LetterboxInfo(1.0, 0, 0, 640, 640))
    assert len(d) == 1 and d.cls[0] == 0


def test_phone_in_upper_part_fires():
    p = PhoneRule()
    ph = ((0.44, 0.25, 0.02, 0.03),)
    w = [obs(i / FPS, phones=ph) for i in range(18)]
    assert check_phone(w, w[-1].t, p).active
    low = ((0.44, 0.55, 0.02, 0.03),)  # у колена, позы нет
    w = [obs(i / FPS, phones=low) for i in range(18)]
    assert not check_phone(w, w[-1].t, p).active


def test_phone_near_wrist_by_pose():
    p = PhoneRule(upper_fraction=0.1)
    ph = ((0.44, 0.44, 0.02, 0.02),)  # у кисти, ниже верхней части
    w = [obs(i / FPS, phones=ph, keypoints=kp()) for i in range(18)]
    assert check_phone(w, w[-1].t, p).active


def walk(n, k, speed=0.5):
    # speed ростов/с при росте 0.4 кадра
    return [obs(i / FPS, bbox=(0.1 + speed * 0.4 * i / FPS, 0.2, 0.1, 0.4), keypoints=k) for i in range(n)]


def test_unsafe_carry_hands_up_while_walking():
    p = CarryRule()
    up = kp(wrist_y=0.2)
    assert hands_up(obs(0, keypoints=up))
    w = walk(18, up)
    assert check_unsafe_carry(w, w[-1].t, p).active
    w = walk(18, kp())  # руки внизу
    assert not check_unsafe_carry(w, w[-1].t, p).active
    w = walk(18, up, speed=0.0)  # стоит, руки подняты
    assert not check_unsafe_carry(w, w[-1].t, p).active


def test_unsafe_carry_bent_while_walking():
    bent = kp(shoulder=(0.40, 0.35), hip=(0.47, 0.38))
    assert torso_angle(obs(0, keypoints=bent)) > 60
    w = walk(18, bent)
    assert check_unsafe_carry(w, w[-1].t, CarryRule()).active


def test_lying_by_pose_overrides_bbox():
    tall = (0.4, 0.2, 0.1, 0.4)
    flat = kp(shoulder=(0.40, 0.5), hip=(0.48, 0.5))
    assert is_lying(obs(0, bbox=tall, keypoints=flat), 1.5)  # рамка стоячая, а корпус горизонтально
    upright = kp()
    wide = (0.3, 0.5, 0.3, 0.1)
    assert not is_lying(obs(0, bbox=wide, keypoints=upright), 1.5)
    assert is_lying(obs(0, bbox=wide), 1.5)  # без позы — по рамке
    p = LyingImmobileRule()
    w = [obs(i / FPS, bbox=tall, keypoints=flat) for i in range(int(p.window_s * FPS) + 2)]
    assert check_lying_immobile(w, w[-1].t, p).active


def test_engine_adds_phone_and_carry_not_on_stills():
    tr = TrackState(1)
    for o in walk(18, kp(wrist_y=0.2)):
        tr.add(Observation(**{**o.__dict__, "phones": ((0.5, 0.25, 0.02, 0.03),)}))
    cam = WorkerCamera(id=1, name="c", rtsp_url="rtsp://x", required_ppe=[], zones=[])
    types = {c.type for c in evaluate_track(tr, tr.last.t, cam, RuleSettings())}
    assert {EventType.phone_usage, EventType.unsafe_carry} <= types
    still = cam.model_copy(update={"stills": True})
    types = {c.type for c in evaluate_track(tr, tr.last.t, still, RuleSettings())}
    assert EventType.phone_usage in types and EventType.unsafe_carry not in types
