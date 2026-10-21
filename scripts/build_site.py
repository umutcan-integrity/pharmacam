"""Build the GitHub Pages site: product landing page + interactive dashboard demo.

The demo is the real dashboard (pharmacam/api/static) running against static
JSON files. Every file is produced by calling the real FastAPI app on a
simulated database, so the demo shows exactly what a device would serve.

    python scripts/build_site.py            # -> site/
Requires data/sim.db (python -m pharmacam -c config/pharmacam.simulator.yaml backfill).
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from pharmacam import timeutil  # noqa: E402
from pharmacam.config import Config  # noqa: E402

SITE = ROOT / "site"
DEMO = SITE / "demo"
DATA = DEMO / "demo-data"
LIVE_FRAMES = 80  # 40 s at 2 fps


def api_key(path: str, params: dict | None = None) -> str:
    """Mirror of apiKey() in pharmacam/api/static/app.js."""
    k = re.sub(r"[^a-zA-Z0-9]+", "_", re.sub(r"^/?api/", "", path))
    for a, v in sorted((params or {}).items()):
        if v is None or v == "":
            continue
        k += f"__{a}-{v}"
    return k


def main() -> None:
    cfg_path = ROOT / "config" / "pharmacam.demo.yaml"
    src_db = ROOT / "data" / "sim.db"
    if not src_db.exists():
        sys.exit("data/sim.db yok — önce backfill çalıştırın")

    # freeze the demo on the last complete day
    con = sqlite3.connect(src_db)
    days = [r[0] for r in con.execute("SELECT DISTINCT day FROM minutes ORDER BY day")]
    con.close()
    tmp = Path(tempfile.mkdtemp())
    db = tmp / "demo.db"
    shutil.copy(src_db, db)
    con = sqlite3.connect(db)
    con.execute("PRAGMA journal_mode=DELETE")
    full_days = [d for d in days if con.execute("SELECT COUNT(*) FROM minutes WHERE day=?", (d,)).fetchone()[0] > 600]
    today = full_days[-1]
    for t in ("visits", "events", "minutes", "zone_hours", "heatmaps", "reports"):
        con.execute(f"DELETE FROM {t} WHERE day > ?", (today,))
    con.commit()
    con.close()

    cfg = Config.load(cfg_path)
    cfg.data["storage"]["path"] = str(db)
    cfg.data["reports"]["dir"] = str(tmp / "reports")
    cfg.path = None  # never write the demo config back
    timeutil.set_timezone(cfg["device"]["timezone"])
    timeutil.today = lambda: today  # type: ignore[assignment]
    import time as _time

    real_time = _time.time
    end_of_today = timeutil.day_bounds(today)[1] - 60
    _time.time = lambda: end_of_today  # type: ignore[assignment]  # "now" = closing of the demo day

    from pharmacam.service import Runtime

    rt = Runtime(cfg)
    rt.background_path = ROOT / "data" / "background.jpg"
    from pharmacam.api.app import create_app

    client = TestClient(create_app(rt))

    if SITE.exists():
        shutil.rmtree(SITE)
    DATA.mkdir(parents=True)
    n_files = 0

    def save(path: str, params: dict | None = None, ext: str = ".json") -> None:
        nonlocal n_files
        r = client.get(path, params=params)
        r.raise_for_status()
        (DATA / (api_key(path, params) + ext)).write_bytes(r.content)
        n_files += 1

    info = client.get("/api/info").json()
    info["live_view"] = True
    info["blur_people"] = False
    info["demographics"] = True
    (DATA / (api_key("/api/info") + ".json")).write_text(json.dumps(info, ensure_ascii=False))
    demo_days = info["days"]
    print(f"demo günü: {today}, {len(demo_days)} gün veri")

    for d in demo_days:
        for p in ("/api/summary", "/api/hourly", "/api/insights", "/api/queue"):
            save(p, {"day": d})
        for inc in ("true", "false"):
            save("/api/visits", {"day": d, "include_staff": inc, "limit": 300})
    save("/api/daily", {"start": timeutil.shift_day(today, -29), "end": today})
    for n in (1, 7, 30):
        rng = {"start": timeutil.shift_day(today, -(n - 1)), "end": today}
        save("/api/zones/stats", rng)
        save("/api/demographics", rng)
        save("/api/heatmap", rng, ".png")
    save("/api/forecast", {"days": 7})
    save("/api/reports")
    save("/api/config/zones")
    save("/api/config/settings")
    shutil.copy(ROOT / "data" / "background.jpg", DATA / "snapshot.jpg")

    # reports: the last two weeks of PDFs
    (DATA / "reports").mkdir()
    for r in client.get("/api/reports").json()[:14]:
        src = ROOT / "data" / "sim-reports" / f"PharmaCam_{r['day']}.pdf"
        if src.exists():
            shutil.copy(src, DATA / "reports" / src.name)
    reports = [r for r in client.get("/api/reports").json() if (DATA / "reports" / f"PharmaCam_{r['day']}.pdf").exists()]
    (DATA / (api_key("/api/reports") + ".json")).write_text(json.dumps(reports))

    _time.time = real_time  # type: ignore[assignment]
    record_live(cfg, today)

    # dashboard app
    shutil.copytree(ROOT / "pharmacam" / "api" / "static", DEMO / "static",
                    ignore=shutil.ignore_patterns("index.html"))
    html = (ROOT / "pharmacam" / "api" / "static" / "index.html").read_text(encoding="utf-8")
    inject = (f'<script>window.PHARMACAM_DEMO = {{ base: "demo-data/", liveFrames: {LIVE_FRAMES}, home: "../" }};</script>\n'
              '<script src="static/app.js"></script>')
    html = html.replace('<script src="static/app.js"></script>', inject)
    html = html.replace('<title>PharmaCam Paneli</title>', '<title>PharmaCam Demo Paneli</title>')
    (DEMO / "index.html").write_text(html, encoding="utf-8")

    # landing page + assets
    shutil.copy(ROOT / "scripts" / "site" / "index.html", SITE / "index.html")
    (SITE / "assets").mkdir()
    for f in [*(ROOT / "docs" / "assets").glob("*.png"), *(ROOT / "scripts" / "site" / "assets").glob("*.png")]:
        shutil.copy(f, SITE / "assets" / f.name)
    shutil.copy(ROOT / "docs" / "PharmaCam-Kurulum-Kilavuzu.pdf", SITE / "PharmaCam-Kurulum-Kilavuzu.pdf")
    if (ROOT / "scripts" / "site" / "media").exists():  # ft-motion videos (see video/README.md)
        shutil.copytree(ROOT / "scripts" / "site" / "media", SITE / "media")
    (SITE / ".nojekyll").write_text("")
    size = sum(f.stat().st_size for f in SITE.rglob("*") if f.is_file()) / 1e6
    print(f"site/ hazır: {n_files} API yanıtı, toplam {size:.1f} MB")


def record_live(cfg: Config, today: str) -> None:
    """Record a 40 s live sequence from the simulator: plain frames (no drawings — the panel draws
    zones and people itself) and the matching live snapshots, both at 2 fps."""
    from pharmacam.analytics.engine import AnalyticsEngine
    from pharmacam.analytics.heatmap import encode_jpeg
    from pharmacam.camera.simulator import SimulatorSource
    from pharmacam.storage.db import Store

    live_dir = DATA / "live"
    live_dir.mkdir()
    y, m, d = map(int, today.split("-"))
    start = datetime(y, m, d, 18, 0, tzinfo=timeutil.tz()).timestamp()
    cfg.data["simulator"].update(realtime=False, start_ts=start, duration_s=200, seed=5, hour_override=18)
    engine = AnalyticsEngine(cfg, Store(":memory:"))
    base = json.loads((DATA / (api_key("/api/summary", {"day": today}) + ".json")).read_text())
    seq = []
    for i, frame in enumerate(SimulatorSource(cfg).frames()):
        live = engine.process(frame)
        if i < 600 or i % 5:  # 60 s warm-up, then 2 samples per second
            continue
        snap = engine.snapshot()
        snap["camera_ok"] = True
        snap["zone_names"] = {z.id: z.name for z in cfg.zones}
        # show the day's real totals so far (18:00) instead of the short simulation's
        snap["entries_today"] = int(base["entries"] * 0.78) + live.entries_today
        snap["exits_today"] = snap["entries_today"] - live.occupancy
        seq.append(snap)
        (live_dir / f"frame_{len(seq) - 1:03d}.jpg").write_bytes(encode_jpeg(frame.image, 72))
        if len(seq) >= LIVE_FRAMES:
            break
    (live_dir / "seq.json").write_text(json.dumps(seq, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
