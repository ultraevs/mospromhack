from pathlib import Path

import numpy as np

from worker.app.model_spec import ModelSpec
from worker.app.postprocess import Detections, merge_people

PPE = ModelSpec("ppe", ("person", "helmet", "no_helmet", "vest", "mask", "no_mask", "vehicle"),
                np.full(7, 0.4, dtype=np.float32), 0.7, 640, Path("x.onnx"))
PEOPLE = ModelSpec("people", ("person", "car", "motorcycle", "bus", "truck"),
                   np.full(5, 0.5, dtype=np.float32), 0.7, 640, Path("y.onnx"))


def dets(boxes, conf, cls):
    return Detections(np.array(boxes, dtype=np.float32).reshape(-1, 4), np.array(conf, dtype=np.float32),
                      np.array(cls, dtype=np.int64))


def test_person_from_coco_vehicles_from_both():
    # ppe: человек, каска, погрузчик; coco: человек, грузовик поверх погрузчика (дубль) и машина в стороне
    ppe = dets([[10, 10, 50, 150], [20, 10, 40, 30], [200, 50, 320, 200]], [0.9, 0.8, 0.7], [0, 1, 6])
    coco = dets([[12, 12, 52, 152], [205, 55, 318, 198], [400, 60, 500, 140]], [0.9, 0.6, 0.8], [0, 4, 1])
    out = merge_people(ppe, PPE, coco, PEOPLE)
    names = sorted(PPE.classes[c] for c in out.cls)
    assert names == ["helmet", "person", "vehicle", "vehicle"]
    persons = out.xyxy[out.cls == 0]
    assert persons[0][0] == 12  # человек от coco, свой выкинут
    vehicles = out.xyxy[out.cls == 6]
    assert {float(v[0]) for v in vehicles} == {200.0, 400.0}  # свой погрузчик + машина из coco, дубль убран
