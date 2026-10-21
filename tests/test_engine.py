"""End-to-end analytics on scripted scenes (zones from config/pharmacam.simulator.yaml)."""
import json

from conftest import Scene, ts_at

from pharmacam.analytics import queries as q
from pharmacam.analytics.engine import AnalyticsEngine
from pharmacam.storage.db import Store

DAY = "2026-10-14"


def run(cfg, scene, duration):
    store = Store(":memory:")
    eng = AnalyticsEngine(cfg, store)
    for f in scene.frames(duration):
        eng.process(f)
    eng.flush()
    return store, eng


def customer_path():
    # door (outside) -> inside -> derma shelf 8s -> queue 12s -> counter 20s -> out
    return [
        (0.0, 0.18, 0.99), (1.5, 0.18, 0.84), (3.0, 0.09, 0.55), (11.0, 0.09, 0.55),
        (13.0, 0.55, 0.60), (25.0, 0.55, 0.55), (26.0, 0.55, 0.43), (46.0, 0.55, 0.43),
        (49.0, 0.18, 0.84), (51.0, 0.18, 0.99),
    ]


def test_single_customer_journey(cfg):
    scene = Scene(ts_at(DAY, 10)).add(customer_path())
    store, eng = run(cfg, scene, 52)
    s = q.summary(store, DAY)
    assert s["entries"] == 1 and s["exits"] == 1
    assert s["served"] == 1 and s["abandoned"] == 0
    v = q.visits(store, DAY)
    assert len(v) == 1
    v = v[0]
    assert v["is_staff"] == 0 and v["entered"] == 1 and v["exited"] == 1
    assert 9 <= v["queue_wait_s"] <= 15, v["queue_wait_s"]
    assert 15 <= v["service_s"] <= 23, v["service_s"]
    assert v["zones"]["derma"] >= 7
    zs = {z["zone"]: z for z in q.zone_stats(store, DAY, DAY)}
    assert zs["derma"]["stops"] == 1
    assert 7 <= zs["derma"]["avg_dwell_s"] <= 10
    types = [e["type"] for e in store.query("SELECT type FROM events ORDER BY ts")]
    assert types[0] == "entry" and "queue_join" in types and "served" in types and types[-1] == "exit"
    assert store.heatmap(DAY, DAY).sum() > 40


def test_staff_in_white_coat_is_not_a_customer(cfg):
    scene = Scene(ts_at(DAY, 10))
    # pharmacist walks from the counter to a shelf and back (outside the staff zone most of the time)
    scene.add([(0, 0.55, 0.30), (3, 0.55, 0.55), (6, 0.88, 0.5), (30, 0.88, 0.5), (34, 0.55, 0.3)], coat=True)
    store, eng = run(cfg, scene, 34)
    s = q.summary(store, DAY)
    assert s["entries"] == 0 and s["visits"] == 0
    assert s["staff_people"] == 1
    zs = q.zone_stats(store, DAY, DAY)
    assert all(z["stops"] == 0 for z in zs)  # restocking is not a customer stop


def test_live_state_counts(cfg):
    scene = Scene(ts_at(DAY, 10))
    scene.add([(0, 0.55, 0.60), (30, 0.55, 0.60)])          # waiting in queue
    scene.add([(0, 0.55, 0.70), (30, 0.55, 0.70)])          # waiting in queue
    scene.add([(0, 0.88, 0.50), (30, 0.88, 0.50)])          # at vitamins
    scene.add([(0, 0.50, 0.25), (30, 0.50, 0.25)], coat=True)  # pharmacist
    store = Store(":memory:")
    eng = AnalyticsEngine(cfg, store)
    for f in scene.frames(20):
        live = eng.process(f)
    assert live.visible_customers == 3
    assert live.staff_on_duty == 1
    assert live.queue_length == 2
    assert live.zone_counts.get("vitamin") == 1
    assert live.occupancy == 3
    assert all(w >= 15 for w in live.queue_waits)


def test_queue_abandon(cfg):
    scene = Scene(ts_at(DAY, 10)).add([
        (0, 0.18, 0.99), (1.5, 0.18, 0.84), (3, 0.55, 0.65), (40, 0.55, 0.65), (43, 0.18, 0.84), (45, 0.18, 0.99)])
    store, _ = run(cfg, scene, 46)
    v = q.visits(store, DAY)[0]
    assert v["abandoned"] == 1 and v["served"] == 0 and v["queue_wait_s"] >= 30
    assert q.summary(store, DAY)["abandoned"] == 1


def test_minutes_and_restart_restore(cfg):
    scene = Scene(ts_at(DAY, 10)).add(customer_path())
    store, _ = run(cfg, scene, 52)
    rows = store.query("SELECT * FROM minutes")
    assert sum(r["entries"] for r in rows) == 1
    assert max(r["occupancy_max"] for r in rows) == 1
    # day counters survive a restart
    import pharmacam.timeutil as tu

    orig = tu.today
    tu.today = lambda: DAY
    try:
        eng2 = AnalyticsEngine(cfg, store)
        assert eng2.live.entries_today == 1 and eng2.live.exits_today == 1
    finally:
        tu.today = orig


def test_visit_zones_json_is_valid(cfg):
    scene = Scene(ts_at(DAY, 10)).add(customer_path())
    store, _ = run(cfg, scene, 52)
    raw = store.one("SELECT zones FROM visits")["zones"]
    assert isinstance(json.loads(raw), dict)


def test_track_restart_at_door_is_not_a_new_customer(cfg):
    scene = Scene(ts_at(DAY, 10))
    # inside customer walks to the door; the track breaks right at the door ...
    scene.add([(0, 0.5, 0.6), (5, 0.18, 0.87)])
    # ... and a new track appears at the door moving out
    scene.add([(7.6, 0.18, 0.875), (9, 0.18, 0.99)])
    store, _ = run(cfg, scene, 10)
    counts = store.day_counts(DAY)
    assert counts["entries"] == 0
    assert counts["exits"] == 1


def test_fast_walker_born_inside_door_is_counted(cfg):
    # first seen already past the line (missed frames at the frame edge), walks in
    scene = Scene(ts_at(DAY, 10)).add([(0, 0.18, 0.90), (3, 0.3, 0.6), (10, 0.3, 0.6)])
    store, _ = run(cfg, scene, 10)
    assert store.day_counts(DAY)["entries"] == 1


def test_frames_without_new_detections_keep_counts(cfg):
    # IMX500 at 30 fps: every other frame repeats the previous detections (fresh=False).
    # Those frames must update the picture only, and the journey must count exactly as before.
    from dataclasses import replace

    scene = Scene(ts_at(DAY, 10)).add(customer_path())
    store = Store(":memory:")
    eng = AnalyticsEngine(cfg, store)
    last = None
    for f in scene.frames(52):
        if last is not None:  # a stale copy in between, half a frame later
            eng.process(replace(last, ts=(last.ts + f.ts) / 2, fresh=False))
            assert eng.last_frame_ts == (last.ts + f.ts) / 2
        eng.process(f)
        last = f
    eng.flush()
    s = q.summary(store, DAY)
    assert s["entries"] == 1 and s["exits"] == 1 and s["served"] == 1
    assert eng.live.video_fps > eng.live.fps > 0


def test_person_hidden_in_queue_keeps_identity_and_wait(cfg):
    # waits in the queue, hidden 5 s behind the person in front, seen again where they stood,
    # then served: still one visit with the full wait (the tracker remembers people standing)
    scene = Scene(ts_at(DAY, 10))
    scene.add([(0, 0.18, 0.99), (1.5, 0.18, 0.84), (3, 0.55, 0.65), (20, 0.55, 0.65)])
    scene.add([(25, 0.55, 0.645), (27, 0.55, 0.43), (50, 0.55, 0.43), (53, 0.18, 0.84), (55, 0.18, 0.99)])
    store, _ = run(cfg, scene, 56)
    s = q.summary(store, DAY)
    assert s["entries"] == 1 and s["exits"] == 1
    assert s["visits"] == 1 and s["served"] == 1 and s["abandoned"] == 0
    assert q.visits(store, DAY)[0]["queue_wait_s"] >= 24


def test_poster_is_dropped_and_remembered(cfg):
    poster = [(0, 0.89, 0.47), (200, 0.89, 0.47)]  # never moves, never came through the door
    scene = Scene(ts_at(DAY, 10)).add(poster)
    store = Store(":memory:")
    eng = AnalyticsEngine(cfg, store)
    for f in scene.frames(200):
        live = eng.process(f)
        if f.ts - scene.start == 60:
            assert live.visible_customers == 1  # benefit of the doubt at first
    assert live.visible_customers == 0 and live.tracks == []
    eng.flush()
    assert q.summary(store, DAY)["visits"] == 0
    assert all(z["stops"] == 0 for z in q.zone_stats(store, DAY, DAY))
    # after a restart the same spot is recognised at once
    eng2 = AnalyticsEngine(cfg, store)
    for f in Scene(ts_at(DAY, 11)).add(poster).frames(5):
        live = eng2.process(f)
    assert live.visible_customers == 0


def test_ignore_zone_hides_detections(cfg):
    cfg.set_zones(cfg.data["zones"] + [{"id": "ayna", "type": "ignore", "name": "Ayna",
                                         "polygon": [[0.84, 0.25], [0.95, 0.25], [0.95, 0.5], [0.84, 0.5]]}])
    scene = Scene(ts_at(DAY, 10)).add([(0, 0.89, 0.47), (20, 0.89, 0.47)])
    store = Store(":memory:")
    eng = AnalyticsEngine(cfg, store)
    for f in scene.frames(20):
        live = eng.process(f)
    assert live.visible_customers == 0 and eng.tracker.tracks == []



def _labels(eng, frames):
    seen = {}
    for f in frames:
        for t in eng.process(f).tracks:
            seen.setdefault(t["id"], t.get("n"))
            if t.get("n") is not None:
                seen[t["id"]] = t["n"]
    return seen


def test_numbers_are_given_at_the_door_not_to_broken_tracks(cfg):
    scene = Scene(ts_at(DAY, 10))
    scene.add([(0, 0.88, 0.50), (40, 0.88, 0.50)])  # already inside when we start: numbered
    scene.add([(10, 0.18, 0.99), (11.5, 0.18, 0.84), (14, 0.30, 0.60), (40, 0.30, 0.60)])  # comes in: numbered
    scene.add([(25, 0.60, 0.62), (40, 0.60, 0.62)], color=(200, 40, 40))  # pops up inside: a break, no number
    eng = AnalyticsEngine(cfg, Store(":memory:"))
    labels = _labels(eng, scene.frames(40))
    assert sorted(n for n in labels.values() if n is not None) == [1, 2]
    assert list(labels.values()).count(None) == 1


def test_broken_track_takes_back_its_number_by_clothes(cfg):
    # comes in (#1), shops at the vitamins, hidden 8 s behind a stand (longer than the tracker
    # remembers anyone), seen again nearby: same colours → still #1 and the time keeps running
    scene = Scene(ts_at(DAY, 10))
    scene.add([(0, 0.18, 0.99), (1.5, 0.18, 0.84), (6, 0.86, 0.52), (14, 0.86, 0.52)], color=(40, 70, 170))
    scene.add([(22, 0.87, 0.50), (35, 0.87, 0.50)], color=(40, 70, 170))
    eng = AnalyticsEngine(cfg, Store(":memory:"))
    for f in scene.frames(35):
        live = eng.process(f)
    assert [t["n"] for t in live.tracks] == [1]
    assert live.tracks[0]["in_s"] > 30


def test_someone_else_at_the_same_spot_is_not_relabelled(cfg):
    scene = Scene(ts_at(DAY, 10))
    scene.add([(0, 0.18, 0.99), (1.5, 0.18, 0.84), (6, 0.86, 0.52), (14, 0.86, 0.52)], color=(40, 70, 170))
    scene.add([(22, 0.87, 0.50), (35, 0.87, 0.50)], color=(200, 40, 40))  # red coat: a different person
    eng = AnalyticsEngine(cfg, Store(":memory:"))
    for f in scene.frames(35):
        live = eng.process(f)
    assert [t["n"] for t in live.tracks] == [None]


def test_a_looping_recording_starts_over_at_the_cut(cfg):
    from dataclasses import replace

    scene = Scene(ts_at(DAY, 10)).add([(0, 0.88, 0.50), (30, 0.88, 0.50)])
    eng = AnalyticsEngine(cfg, Store(":memory:"))
    frames = list(scene.frames(30))
    for f in frames[:100]:
        eng.process(f)
    old = [t["id"] for t in eng.live.tracks]
    for i, f in enumerate(frames[100:]):
        live = eng.process(replace(f, cut=(i == 0)))
    assert live.tracks and all(t["id"] not in old for t in live.tracks)
    assert [t["n"] for t in live.tracks] == [2]  # seen right after the cut: already inside → numbered


def test_a_replayed_recording_can_start_its_day_over(cfg):
    from dataclasses import replace

    store = Store(":memory:")
    eng = AnalyticsEngine(cfg, store)
    for f in Scene(ts_at(DAY, 10)).add(customer_path()).frames(52):
        eng.process(f)
    assert eng.live.entries_today == 1
    frames = list(Scene(ts_at(DAY, 10, 1)).add(customer_path()).frames(52))  # the clip again
    numbers = set()
    for i, f in enumerate(frames):
        live = eng.process(replace(f, cut=(i == 0), new_day=(i == 0)))
        numbers |= {t["n"] for t in live.tracks if t["n"] is not None}
    eng.flush()
    assert numbers == {1}  # the same person is #1 again, not #2
    assert live.entries_today == 1 and live.exits_today == 1
    s = q.summary(store, DAY)
    assert s["entries"] == 1 and s["exits"] == 1 and len(q.visits(store, DAY)) == 1


def test_a_remembered_poster_is_forgotten_after_a_week_of_scene_time(cfg):
    from conftest import person_box

    from pharmacam import timeutil

    def visible(days_later):
        store = Store(":memory:")
        store.set_meta("static_objects", json.dumps([{"box": person_box(0.89, 0.47), "seen": ts_at(DAY, 10)}]))
        eng = AnalyticsEngine(cfg, store)
        day = timeutil.shift_day(DAY, days_later)
        for f in Scene(ts_at(day, 10)).add([(0, 0.89, 0.47), (5, 0.89, 0.47)]).frames(5):
            live = eng.process(f)
        return live.visible_customers

    assert visible(1) == 0  # the poster's spot, still remembered the next day
    assert visible(8) == 1  # a week later it is forgotten: whoever stands there counts
