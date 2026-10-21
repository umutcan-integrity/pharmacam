"""API, sync -> hub, forecast, report and simulator-accuracy tests."""
import gzip
import json
from datetime import date, timedelta

import pytest
from conftest import Scene, sim_config, ts_at
from fastapi.testclient import TestClient

from pharmacam import timeutil
from pharmacam.analytics import forecast as fc
from pharmacam.analytics.engine import AnalyticsEngine
from pharmacam.service import Runtime
from pharmacam.storage.db import Store
from test_engine import customer_path

DAY = "2026-10-14"


@pytest.fixture
def runtime(cfg):
    rt = Runtime(cfg)
    for f in Scene(ts_at(DAY, 10)).add(customer_path()).frames(52):
        rt.engine.process(f)
    rt.engine.flush()
    return rt


@pytest.fixture
def client(runtime):
    from pharmacam.api.app import create_app

    return TestClient(create_app(runtime))


def test_api_endpoints(client):
    assert client.get("/api/health").json()["device_id"] == "sim-001"
    info = client.get("/api/info").json()
    assert DAY in info["days"] and len(info["zones"]) == 8
    s = client.get("/api/summary", params={"day": DAY}).json()
    assert s["entries"] == 1 and s["served"] == 1
    h = client.get("/api/hourly", params={"day": DAY}).json()
    assert len(h) == 24 and h[10]["entries"] == 1
    assert client.get("/api/queue", params={"day": DAY}).json()["served"] == 1
    z = client.get("/api/zones/stats", params={"start": DAY, "end": DAY}).json()
    assert {r["zone"] for r in z} <= {"derma", "vitamin", "bebek", "otc"}
    v = client.get("/api/visits", params={"day": DAY}).json()
    assert len(v) == 1 and v[0]["zones"]
    csv = client.get("/api/export/visits.csv", params={"start": DAY, "end": DAY})
    assert csv.status_code == 200 and "queue_wait_s" in csv.text
    png = client.get("/api/heatmap", params={"start": DAY, "end": DAY})
    assert png.headers["content-type"] == "image/png" and len(png.content) > 1000
    assert client.get("/api/insights", params={"day": DAY}).status_code == 200
    assert client.get("/api/forecast").status_code == 200
    assert client.get("/api/summary", params={"day": "bad"}).status_code == 422
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200


def test_zone_editing_validates_and_persists(client, runtime):
    zones = client.get("/api/config/zones").json()
    bad = zones + [{"id": "x", "type": "shelf", "name": "X", "polygon": [[0, 0], [1, 1]]}]
    assert client.put("/api/config/zones", json=bad).status_code == 400
    good = zones + [{"id": "yeni", "type": "shelf", "name": "Yeni Raf", "polygon": [[0, 0], [0.1, 0], [0.1, 0.1]]}]
    r = client.put("/api/config/zones", json=good)
    assert r.status_code == 200
    assert "Yeni Raf" in runtime.cfg.path.read_text(encoding="utf-8")


def test_settings_patch_is_whitelisted(client, runtime):
    assert client.patch("/api/config/settings", json={"server": {"password": "x"}}).status_code == 400
    r = client.patch("/api/config/settings", json={"staff": {"white_ratio_threshold": 0.4}})
    assert r.status_code == 200 and runtime.engine.staff.threshold == 0.4


def test_auth_required_when_password_set(tmp_path):
    from conftest import sim_config

    from pharmacam.api.app import create_app

    cfg = sim_config(tmp_path, server={"password": "gizli"})
    c = TestClient(create_app(Runtime(cfg)))
    assert c.get("/api/summary").status_code == 401
    assert c.get("/api/summary", auth=("admin", "gizli")).status_code == 200
    assert c.get("/api/health").status_code == 200  # health stays open for monitoring


def test_report_pdf(client, runtime):
    r = client.post(f"/api/reports/{DAY}")
    assert r.status_code == 200
    pdf = client.get(f"/api/reports/{DAY}.pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"


def test_sync_to_hub(runtime, tmp_path, monkeypatch):
    from pharmacam.hub.app import create_hub
    from pharmacam.sync.client import build_payload

    monkeypatch.setenv("PHARMACAM_HUB_TOKENS", "sim-001:tok123,other:tok999")
    hub = TestClient(create_hub(str(tmp_path / "hub.db")))
    payload, cursors = build_payload(runtime)
    assert payload["visits"] and payload["minutes"]
    body = gzip.compress(json.dumps(payload).encode())
    hdr = {"Content-Encoding": "gzip", "Content-Type": "application/json"}
    assert hub.post("/api/v1/ingest", content=body, headers={**hdr, "Authorization": "Bearer nope"}).status_code == 401
    assert hub.post("/api/v1/ingest", content=body, headers={**hdr, "Authorization": "Bearer tok999"}).status_code == 403
    r = hub.post("/api/v1/ingest", content=body, headers={**hdr, "Authorization": "Bearer tok123"})
    assert r.status_code == 200 and r.json()["accepted"]["visits"] == 1
    # idempotent re-send
    assert hub.post("/api/v1/ingest", content=body, headers={**hdr, "Authorization": "Bearer tok123"}).status_code == 200
    for k, v in cursors.items():
        runtime.store.set_meta(k, v)
    again, _ = build_payload(runtime)
    assert again["visits"] == [] and again["minutes"] == []
    devs = hub.get("/api/v1/devices").json()
    assert devs[0]["id"] == "sim-001" and devs[0]["online"]
    daily = hub.get("/api/v1/devices/sim-001/daily", params={"start": DAY, "end": DAY}).json()
    assert daily[0]["entries"] == 1
    assert hub.get("/api/v1/devices/sim-001/heatmap.png", params={"start": DAY, "end": DAY}).status_code == 200
    assert hub.get("/").status_code == 200


def test_forecast_follows_weekly_pattern():
    store = Store(":memory:")
    start = date(2026, 8, 3)  # Monday
    for i in range(42):
        d = start + timedelta(days=i)
        if d.weekday() == 6:
            continue  # closed on Sundays
        base = 40 if d.weekday() == 4 else 20  # Fridays are busy
        for hour in (10, 18):
            ts = ts_at(d.isoformat(), hour)
            store.add_minute({"ts": ts, "entries": base, "exits": base, "staff_entries": 0, "occupancy_avg": 1,
                              "occupancy_max": 2, "visible_avg": 1, "queue_avg": 0, "queue_max": 0,
                              "staff_avg": 1, "served": 0, "wait_sum": 0, "samples": 60})
    f = fc.forecast(store, 7, start=(start + timedelta(days=42)).isoformat())
    by_wd = {date.fromisoformat(d["day"]).weekday(): d for d in f["days"]}
    assert by_wd[4]["expected_total"] == pytest.approx(80, rel=0.05)
    assert by_wd[0]["expected_total"] == pytest.approx(40, rel=0.05)
    assert by_wd[6]["expected_total"] == 0
    assert by_wd[4]["peak_hour"] in (10, 18)
    assert fc.staffing_hint(60, 90) == 2


def test_simulator_counts_match_ground_truth(cfg):
    from pharmacam.camera.simulator import SimulatorSource

    start = ts_at(DAY, 17)
    cfg.data["simulator"].update(realtime=False, start_ts=start, duration_s=1200, seed=11)
    timeutil.set_timezone("Europe/Istanbul")
    store = Store(":memory:")
    eng = AnalyticsEngine(cfg, store)
    src = SimulatorSource(cfg)
    prev, truth_in = {}, 0
    for f in src.frames():
        cur = {a.id: a.pos[1] for a in src.sim.agents if not a.staff}
        truth_in += sum(1 for i, y in cur.items() if prev.get(i, 0) >= 0.93 > y)
        prev = cur
        eng.process(f)
    eng.flush()
    measured = store.day_counts(DAY)["entries"]
    assert truth_in >= 10
    assert abs(measured - truth_in) <= max(1, round(0.05 * truth_in)), (measured, truth_in)


def test_model_presets_resolve(tmp_path):
    from pharmacam.camera import models
    from pharmacam.config import Config

    cfg = Config({"camera": {"model": "yolo11n"}}, tmp_path / "config" / "pharmacam.yaml")
    m = models.resolve(cfg)
    assert m.spec.key == "yolo11n" and m.fps == 15 and m.bbox_order == "xy" and m.bbox_normalization is True
    assert m.path == tmp_path / "models" / "imx500_network_yolo11n_pp.rpk"
    # explicit settings win over the preset
    cfg = Config({"camera": {"model": "yolo11n", "fps": 10, "bbox_order": "yx"}}, None)
    m = models.resolve(cfg)
    assert m.fps == 10 and m.bbox_order == "yx"
    # a custom .rpk keeps what the file declares
    cfg = Config({"camera": {"model": "/x/custom.rpk"}}, None)
    m = models.resolve(cfg)
    assert m.spec is None and m.bbox_order is None and m.bbox_normalization is None and m.fps == 30


def test_model_download_is_verified(tmp_path, monkeypatch):
    import io

    from pharmacam.camera import models
    from pharmacam.config import Config

    cfg = Config({"camera": {"model": "yolo11n"}}, tmp_path / "config" / "pharmacam.yaml")
    monkeypatch.setattr(models.urllib.request, "urlopen", lambda url, timeout: io.BytesIO(b"not the model"))
    try:
        models.ensure(cfg)
        raise AssertionError("bad download accepted")
    except RuntimeError as e:
        assert "sha256" in str(e)
    assert not (tmp_path / "models" / "imx500_network_yolo11n_pp.rpk").exists()
    assert models.fallback().spec.key == "ssd"


def test_installation_assistant_endpoints(client, runtime):
    c = client.get("/api/setup/checks").json()
    keys = {x["key"] for x in c["checks"]}
    assert {"camera", "zones"} <= keys and all(x["status"] in ("good", "warn", "bad", "info") for x in c["checks"])
    d = client.get("/api/setup/door", params={"mount": "above_door"}).json()
    assert d["preset"]["type"] == "entrance" and d["current"]["id"] == "kapi"
    assert client.get("/api/setup/door", params={"mount": "nonsense"}).status_code == 422
    new = {**d["preset"], "line": [[0.1, 0.9], [0.4, 0.9]]}
    assert client.post("/api/setup/door", json=new).json()["line"] == [[0.1, 0.9], [0.4, 0.9]]
    zones = runtime.cfg.zones
    assert sum(z.type == "entrance" for z in zones) == 1 and len(zones) == 8  # the others were kept
    coat = client.get("/api/setup/coat").json()
    assert "threshold" in coat
    assert client.post("/api/setup/coat", json={"threshold": 2}).status_code == 400
    assert client.post("/api/setup/coat", json={"threshold": 0.3}).json()["threshold"] == 0.3
    assert client.get("/api/demographics", params={"start": DAY, "end": DAY}).json()["visits"] == 1


def test_count_audit_flow(client):
    assert client.get("/api/audit").json() == {"active": False}
    assert client.post("/api/audit/tap", json={"kind": "in"}).status_code == 409
    st = client.post("/api/audit/start").json()
    assert st["active"] and st["manual_in"] == 0
    client.post("/api/audit/tap", json={"kind": "in"})
    client.post("/api/audit/tap", json={"kind": "in"})
    st = client.post("/api/audit/tap", json={"kind": "in", "undo": True}).json()
    assert st["manual_in"] == 1
    res = client.post("/api/audit/stop", json={"note": "deneme"}).json()
    assert res["result"]["in"]["manual"] == 1 and res["result"]["in"]["missed"] == 1  # nobody actually came in
    hist = client.get("/api/audits").json()
    assert hist[0]["id"] == res["id"] and hist[0]["note"] == "deneme"


def test_audit_matching_one_to_one():
    from pharmacam.analytics.setup import audit_result

    taps = [{"kind": "in", "ts": 101.0}, {"kind": "in", "ts": 105.0}, {"kind": "in", "ts": 109.0}, {"kind": "out", "ts": 130.0}]
    events = [{"type": "entry", "ts": 102.5}, {"type": "entry", "ts": 106.0}, {"type": "entry", "ts": 140.0},
              {"type": "exit", "ts": 131.0}]
    r = audit_result(taps, events)
    assert r["in"] == {"manual": 3, "system": 3, "matched": 2, "missed": 1, "extra": 1,
                       "count_accuracy": 100.0, "event_accuracy": 66.7}
    assert r["out"]["matched"] == 1 and r["overall"] == 75.0


def test_setup_checks_report_the_age_gender_model(tmp_path):
    from pharmacam.analytics.setup import SetupMonitor

    cfg = sim_config(tmp_path, camera={"source": "imx500"}, demographics={"enabled": True})
    mon = SetupMonitor()
    rows = {c["key"]: c for c in mon.checks(cfg, {"camera_ok": True, "fps": 15, "demographics": None})}
    assert rows["demographics"]["status"] == "warn"  # switched on but no model on the device
    health = {"camera_ok": True, "fps": 15, "demographics": {"model": "mivolo", "ms_per_person": 1260,
                                                             "analysed": 40, "faces": 2}}
    rows = {c["key"]: c for c in mon.checks(cfg, health)}
    assert rows["demographics"]["status"] == "good" and "1.3 sn" in rows["demographics"]["value"]
    assert rows["faces"]["status"] == "warn"  # 2 faces in 40 looks: people show the camera their backs
    cfg = sim_config(tmp_path, camera={"source": "imx500"}, demographics={"enabled": False})
    assert "demographics" not in {c["key"] for c in mon.checks(cfg, health)}


def test_setup_checks_warn_about_a_weak_power_supply(tmp_path):
    from pharmacam.analytics.setup import SetupMonitor

    cfg = sim_config(tmp_path)
    rows = lambda system: {c["key"]: c for c in SetupMonitor().checks(cfg, {"camera_ok": True, "fps": 15,  # noqa: E731
                                                                              "system": system})}
    assert "power" not in rows({})  # not a Raspberry Pi
    assert rows({"undervoltage_now": False, "undervoltage_seen": False})["power"]["status"] == "good"
    assert rows({"undervoltage_now": False, "undervoltage_seen": True})["power"]["status"] == "warn"
    assert rows({"undervoltage_now": True, "undervoltage_seen": True})["power"]["status"] == "bad"
