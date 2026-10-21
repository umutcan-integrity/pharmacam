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
    tr = Tracker(min_hits=2, max_lost_seconds=1.0, max_lost_static_seconds=3.0)
    for i in range(5):
        tr.update([Detection((0.4, 0.4, 0.5, 0.8), 0.9)], i * 0.1)
    tid = tr.tracks[0].id
    # occluded: low score detection should still extend the same track
    active, _ = tr.update([Detection((0.4, 0.4, 0.5, 0.8), 0.35)], 0.5)
    assert active and active[0].id == tid
    # a standing person is remembered for max_lost_static, then ended
    _, ended = tr.update([], 2.0)
    assert ended == [] and tr.tracks[0].id == tid
    _, ended = tr.update([], 4.0)
    assert [t.id for t in ended] == [tid]


def test_walker_is_forgotten_sooner_than_someone_standing():
    tr = Tracker(min_hits=2, max_lost_seconds=1.0, max_lost_static_seconds=3.0)
    for i in range(10):  # walking 0.3 box heights per frame
        x = 0.1 + 0.03 * i
        tr.update([Detection((x, 0.4, x + 0.1, 0.8), 0.9)], i * 0.1)
    _, ended = tr.update([], 2.2)
    assert len(ended) == 1


def test_kalman_carries_a_walker_through_a_gap():
    tr = Tracker(min_hits=2)
    for i in range(10):  # 0.15 / s to the right
        x = 0.1 + 0.015 * i
        active, _ = tr.update([Detection((x, 0.3, x + 0.08, 0.7), 0.9)], i * 0.1)
    tid = active[0].id
    for i in range(10, 18):  # hidden for 0.8 s
        tr.update([], i * 0.1)
    x = 0.1 + 0.015 * 18
    active, _ = tr.update([Detection((x, 0.3, x + 0.08, 0.7), 0.9)], 1.8)
    assert [t.id for t in active] == [tid]


def test_standing_person_hidden_for_seconds_keeps_identity():
    # waiting in the queue, hidden 4 s behind the person in front, seen again at the same spot
    tr = Tracker(min_hits=2)
    box = (0.5, 0.3, 0.58, 0.6)
    for i in range(20):
        active, _ = tr.update([Detection(box, 0.8)], i * 0.1)
    tid = active[0].id
    for i in range(20, 60):
        _, ended = tr.update([], i * 0.1)
        assert ended == []
    active, _ = tr.update([Detection((0.505, 0.30, 0.585, 0.6), 0.45)], 6.0)  # low score, partly hidden
    assert [t.id for t in active] == [tid]


def test_hidden_legs_do_not_move_the_foot_point():
    tr = Tracker(min_hits=2)
    for i in range(10):
        active, _ = tr.update([Detection((0.5, 0.3, 0.6, 0.7), 0.9)], i * 0.1)
    for i in range(10, 25):  # only the upper half is visible, low score
        active, _ = tr.update([Detection((0.5, 0.3, 0.6, 0.5), 0.4)], i * 0.1)
    assert active and abs(active[0].foot[1] - 0.7) < 0.03


def test_assignment_is_optimal_not_greedy():
    from pharmacam.vision.tracker import _hungarian

    # greedy would take the single best pair (0,0) and leave row 1 unmatched
    cost = [[0.40, 0.50], [0.45, 9.0]]
    assert sorted(_hungarian(cost)) == [(0, 1), (1, 0)]


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


def test_person_leaving_through_the_bottom_edge_does_not_run_away():
    # walks down out of the picture: the box is cut by the image border, not by an occluder
    tr = Tracker(min_hits=2)
    ended_at = None
    for i in range(40):
        t = i * 0.1
        top = 0.5 + 0.03 * i
        dets = [Detection((0.4, top, 0.5, min(1.0, top + 0.4)), 0.8)] if top < 0.97 else []
        active, ended = tr.update(dets, t)
        for trk in active:
            assert trk.foot[1] <= 1.02
        if ended and ended_at is None:
            ended_at = t
    assert ended_at is not None and ended_at <= 2.5


def test_clothing_signature_tells_people_apart():
    from pharmacam.vision import appearance

    rng = np.random.default_rng(1)
    img = np.zeros((200, 200, 3), np.uint8)
    img[:, :100] = (40, 70, 170)  # blue coat
    img[:, 100:] = (200, 40, 40)  # red coat
    noisy = np.clip(img.astype(int) + rng.integers(-12, 12, img.shape), 0, 255).astype(np.uint8)
    blue1 = appearance.signature(img, (0.05, 0.05, 0.45, 0.95))
    blue2 = appearance.signature(noisy, (0.06, 0.08, 0.46, 0.97))
    red = appearance.signature(img, (0.55, 0.05, 0.95, 0.95))
    assert appearance.distance(blue1, blue2) < 0.15
    assert appearance.distance(blue1, red) > 0.6
    assert appearance.signature(img, (0.1, 0.1, 0.11, 0.12)) is None  # too small to judge


class _FakeBackend:
    """Stands in for a model: a face whenever the picture's top-left pixel is bright."""

    name = "fake"
    min_person_px = 20

    def __init__(self):
        self.seen = []

    def find_face(self, s):
        return (0, 0, 40, 40, 0.9, 1.0) if s.crop[0, 0, 0] > 128 else None

    def estimate(self, s, face):
        self.seen.append(s.track_id)
        return {"female": 1.0, "age": 30.0, "w": 1.0, "src": "face" if face else "back"}


def _wait_for(cond, timeout=3.0):
    import time

    end = time.monotonic() + timeout
    while time.monotonic() < end and not cond():
        time.sleep(0.01)
    return cond()


def test_demographics_takes_people_in_turns_and_keeps_only_their_latest_picture():
    from pharmacam.vision.demographics import DemographicsAnalyzer

    an = DemographicsAnalyzer(_FakeBackend(), max_rate=1000)
    an.close()  # drive the queue by hand
    an.thread.join(timeout=2)
    an._looks[1] = 5  # person 1 has had many turns already
    img = np.zeros((200, 200, 3), np.uint8)
    for tid in (1, 2, 2, 3):
        an.submit(tid, img, (0.1, 0.1, 0.5, 0.9), 0.0)
    order = [an._next().track_id for _ in range(3)]
    assert sorted(order[:2]) == [2, 3] and order[2] == 1  # person 2's two pictures: one turn
    assert an._next() is None


def test_demographics_stops_looking_at_someones_back_until_they_turn():
    from pharmacam.vision import demographics as D

    be = _FakeBackend()
    an = D.DemographicsAnalyzer(be, max_rate=1000)
    back = np.zeros((200, 200, 3), np.uint8)
    for _ in range(D.BACK_LOOKS + 2):
        an.submit(7, back, (0.1, 0.1, 0.5, 0.9), 0.0)
        _wait_for(lambda: not an._pending)
    assert be.seen.count(7) == D.BACK_LOOKS
    front = back.copy()
    front[0, 0] = 255
    an.submit(7, front, (0.0, 0.0, 0.5, 0.9), 0.0)
    assert _wait_for(lambda: be.seen.count(7) == D.BACK_LOOKS + 1)
    an.drop(7)
    assert 7 not in an._backs
    an.close()


def test_demographics_crop_maps_boxes_into_the_picture():
    from pharmacam.vision.demographics import DemographicsAnalyzer

    be = _FakeBackend()
    an = DemographicsAnalyzer(be, max_rate=1000)
    an.close()  # keep the sample in the queue
    an.thread.join(timeout=2)
    img = np.zeros((100, 200, 3), np.uint8)
    assert an.submit(4, img, (0.25, 0.2, 0.5, 0.8), 0.0, others=[(0.4, 0.3, 0.9, 0.9), (0.9, 0.0, 1.0, 0.1)])
    s = an._pending[4]
    bx1, by1, bx2, by2 = s.box
    assert (bx2 - bx1, by2 - by1) == (50, 60)
    assert len(s.others) == 1  # the far box does not touch the crop
    assert not an.submit(5, img, (0.1, 0.1, 0.2, 0.15), 0.0)  # 5 px tall: too small
