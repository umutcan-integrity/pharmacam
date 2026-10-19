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
