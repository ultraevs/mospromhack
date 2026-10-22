import numpy as np
import pytest

from worker.app.association import anchor, assign_items, inside_fraction, iou_xywh, point_in_polygon
from worker.app.tracking import IouTracker


def boxes(*rows):
    return np.array(rows, dtype=np.float32).reshape(-1, 4)


def test_ids_are_stable_for_moving_people():
    tr = IouTracker()
    ids = []
    for i in range(10):
        ids.append(tr.update(boxes([100 + 8 * i, 100, 200 + 8 * i, 400], [600, 100, 700, 400]), i / 6).tolist())
    assert all(x == ids[0] for x in ids) and ids[0][0] != ids[0][1]


def test_ids_follow_boxes_not_input_order():
    tr = IouTracker()
    a, b = tr.update(boxes([100, 100, 200, 400], [600, 100, 700, 400]), 0.0)
    assert tr.update(boxes([602, 100, 702, 400], [103, 100, 203, 400]), 0.17).tolist() == [b, a]


def test_new_person_gets_new_id_and_missed_frames_keep_id():
    tr = IouTracker(max_age_s=1.5)
    (a,) = tr.update(boxes([100, 100, 200, 400]), 0.0)
    assert tr.update(boxes(), 0.17).size == 0
    assert tr.update(boxes(), 0.33).size == 0
    a2, c = tr.update(boxes([104, 100, 204, 400], [600, 100, 700, 400]), 0.5)
    assert a2 == a and c != a


def test_track_expires_after_max_age():
    tr = IouTracker(max_age_s=1.5)
    (a,) = tr.update(boxes([100, 100, 200, 400]), 0.0)
    (b,) = tr.update(boxes([100, 100, 200, 400]), 2.0)
    assert b != a


def test_velocity_prediction_keeps_fast_walker():
    tr = IouTracker()
    ids = [int(tr.update(boxes([100 + 70 * i, 100, 200 + 70 * i, 400]), i / 6)[0]) for i in range(8)]
    assert len(set(ids)) == 1


def test_distance_fallback_does_not_merge_far_or_differently_sized_people():
    tr = IouTracker()
    (a,) = tr.update(boxes([100, 100, 200, 400]), 0.0)
    (far,) = tr.update(boxes([350, 100, 450, 400]), 0.17)
    assert far != a
    tr2 = IouTracker()
    (b,) = tr2.update(boxes([100, 100, 200, 400]), 0.0)
    (small,) = tr2.update(boxes([190, 100, 230, 220]), 0.17)
    assert small != b


def test_each_track_matches_at_most_one_detection():
    tr = IouTracker()
    (a,) = tr.update(boxes([100, 100, 200, 400]), 0.0)
    ids = tr.update(boxes([100, 100, 200, 400], [110, 100, 210, 400]), 0.17).tolist()
    assert ids.count(a) == 1 and len(set(ids)) == 2


PERSON_A = [100, 100, 200, 400]
PERSON_B = [180, 100, 280, 400]


def test_item_goes_to_person_containing_at_least_half_of_it():
    people = boxes(PERSON_A, [600, 100, 700, 400])
    items = boxes([120, 110, 160, 150],
                  [180, 110, 220, 150],
                  [190, 110, 230, 150],
                  [400, 110, 440, 150])
    assert assign_items(people, items, [True] * 4) == [0, 0, -1, -1]


def test_overlapping_people_item_goes_to_larger_overlap():
    people = boxes(PERSON_A, PERSON_B)
    items = boxes([170, 110, 210, 150])
    assert assign_items(people, items, [True]) == [0]
    items = boxes([175, 110, 215, 150])
    assert assign_items(people, items, [True]) == [1]


def test_head_class_must_be_in_top_45_percent():
    people = boxes(PERSON_A)
    head_ok = [130, 200, 170, 260]
    head_low = [130, 210, 170, 270]
    assert assign_items(people, boxes(head_ok, head_low), [True, True]) == [0, -1]
    assert assign_items(people, boxes(head_low), [False]) == [0]


def test_assign_with_no_people_or_no_items():
    assert assign_items(boxes(), boxes([1, 1, 5, 5]), [True]) == [-1]
    assert assign_items(boxes(PERSON_A), boxes(), []) == []


def test_inside_fraction():
    assert inside_fraction(np.array([0, 0, 10, 10]), np.array([5, 0, 20, 20])) == pytest.approx(0.5)
    assert inside_fraction(np.array([0, 0, 10, 10]), np.array([20, 20, 30, 30])) == 0.0
    assert inside_fraction(np.array([0, 0, 0, 10]), np.array([0, 0, 30, 30])) == 0.0


SQUARE = [[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]]
L_SHAPE = [[0.0, 0.0], [1.0, 0.0], [1.0, 0.4], [0.4, 0.4], [0.4, 1.0], [0.0, 1.0]]


@pytest.mark.parametrize("x,y,inside", [(0.5, 0.5, True), (0.21, 0.79, True), (0.1, 0.5, False), (0.5, 0.9, False),
                                        (0.9, 0.9, False)])
def test_point_in_square(x, y, inside):
    assert point_in_polygon(x, y, SQUARE) is inside


@pytest.mark.parametrize("x,y,inside", [(0.2, 0.2, True), (0.8, 0.2, True), (0.2, 0.8, True), (0.8, 0.8, False),
                                        (0.5, 0.5, False)])
def test_point_in_concave_polygon(x, y, inside):
    assert point_in_polygon(x, y, L_SHAPE) is inside


def test_point_in_polygon_is_independent_of_winding():
    assert point_in_polygon(0.5, 0.5, SQUARE[::-1]) and not point_in_polygon(0.1, 0.5, SQUARE[::-1])


def test_anchor_is_bottom_center():
    assert anchor((0.4, 0.2, 0.1, 0.3)) == pytest.approx((0.45, 0.5))


def test_iou_xywh():
    assert iou_xywh((0, 0, 0.2, 0.2), (0, 0, 0.2, 0.2)) == pytest.approx(1.0)
    assert iou_xywh((0, 0, 0.2, 0.2), (0.1, 0, 0.2, 0.2)) == pytest.approx(1 / 3)
    assert iou_xywh((0, 0, 0.2, 0.2), (0.5, 0.5, 0.2, 0.2)) == 0.0
