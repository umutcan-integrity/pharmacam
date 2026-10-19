import numpy as np

from pharmacam.camera.base import Detection
from pharmacam.vision.geometry import iou, point_in_polygon, projection_t, signed_side
from pharmacam.vision.staff import StaffClassifier, torso_crop, white_ratio
from pharmacam.vision.tracker import Track, Tracker


def test_point_in_polygon():
    sq = [(0, 0), (1, 0), (1, 1), (0, 1)]
    assert point_in_polygon((0.5, 0.5), sq)
    assert not point_in_polygon((1.5, 0.5), sq)
    tri = [(0, 0), (1, 0), (0, 1)]
    assert point_in_polygon((0.2, 0.2), tri)
    assert not point_in_polygon((0.8, 0.8), tri)


def test_line_side_and_projection():
    a, b = (0.0, 0.5), (1.0, 0.5)
    assert signed_side((0.5, 0.4), a, b) * signed_side((0.5, 0.6), a, b) < 0
    assert abs(projection_t((0.25, 0.9), a, b) - 0.25) < 1e-9


def test_iou():
    assert iou((0, 0, 1, 1), (0, 0, 1, 1)) == 1.0
    assert iou((0, 0, 1, 1), (2, 2, 3, 3)) == 0.0
    assert abs(iou((0, 0, 2, 1), (1, 0, 3, 1)) - 1 / 3) < 1e-9


def test_tracker_keeps_ids_for_two_crossing_people():
    tr = Tracker(min_hits=2)
    ids = {}
    for i in range(40):
        t = i * 0.1
        a = Detection((0.1 + 0.01 * i, 0.3, 0.2 + 0.01 * i, 0.7), 0.9)
        b = Detection((0.7 - 0.01 * i, 0.35, 0.8 - 0.01 * i, 0.75), 0.9)
        active, _ = tr.update([a, b], t)
        for trk in active:
            side = "a" if trk.box[0] < 0.45 and i < 15 else None
            if side:
                ids.setdefault(trk.id, 0)
    assert len({t.id for t in tr.tracks}) == 2


def test_tracker_low_score_recovery_and_expiry():
    tr = Tracker(min_hits=2, max_lost_seconds=1.0)
    for i in range(5):
        tr.update([Detection((0.4, 0.4, 0.5, 0.8), 0.9)], i * 0.1)
    tid = tr.tracks[0].id
    # occluded: low score detection should still extend the same track
    active, _ = tr.update([Detection((0.4, 0.4, 0.5, 0.8), 0.35)], 0.5)
    assert active and active[0].id == tid
    # gone for longer than max_lost -> ended
    _, ended = tr.update([], 2.0)
    assert [t.id for t in ended] == [tid]


def test_tracker_ignores_single_spurious_box():
    tr = Tracker(min_hits=3)
    active, _ = tr.update([Detection((0.1, 0.1, 0.2, 0.4), 0.9)], 0.0)
    assert active == []
    _, ended = tr.update([], 1.0)
    assert ended == [] and tr.tracks == []


def test_white_ratio_separates_coat_from_clothes():
    white = np.full((20, 20, 3), 240, np.uint8)
    blue = np.zeros((20, 20, 3), np.uint8)
    blue[...] = (30, 60, 160)
    assert white_ratio(white) > 0.9
    assert white_ratio(blue) < 0.05


def test_staff_classifier_uses_coat_and_zone():
    img = np.zeros((100, 100, 3), np.uint8)
    img[20:80, 40:60] = 245
    clf = StaffClassifier(min_samples=3)
    t = Track(id=1, box=(0.4, 0.2, 0.6, 0.8), score=0.9, first_ts=0, last_ts=0)
    assert torso_crop(img, t.box) is not None
    for _ in range(4):
        clf.observe(t, img)
    assert clf.is_staff(t)

    other = Track(id=2, box=(0.0, 0.2, 0.2, 0.8), score=0.9, first_ts=0, last_ts=0)
    for _ in range(4):
        clf.observe(other, img)
    assert not clf.is_staff(other)
    other.meta.update(seen_s=20.0, staff_zone_s=15.0)  # mostly behind the counter
    assert clf.is_staff(other)
