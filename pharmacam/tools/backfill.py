"""Generate realistic history by running the *real* analytics pipeline over
simulated pharmacy days (one process per day). Used for demos, the public
GitHub Pages demo and for testing reports/forecasts."""
from __future__ import annotations

import copy
import logging
import os
import random
import sqlite3
import tempfile
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime
from pathlib import Path

from .. import timeutil

log = logging.getLogger(__name__)

WEEKDAY_SCALE = [1.05, 1.0, 0.95, 1.0, 1.12, 0.85, 0.0]  # Mon..Sun (Sunday closed)
BASE_SCALE = 0.6  # ~450-650 customers on a weekday: a busy neighbourhood pharmacy
MERGE_APPEND = {
    "visits": "uid,day,hour,started_at,ended_at,duration_s,is_staff,coat_score,entered,exited,queue_wait_s,"
              "service_s,served,abandoned,zones,gender,age,demo_n",
    "minutes": "ts,day,hour,entries,exits,staff_entries,occupancy_avg,occupancy_max,visible_avg,queue_avg,"
               "queue_max,staff_avg,served,wait_sum,samples",
    "events": "ts,day,type,track_uid,zone,value,payload",
}


def _simulate_day(args) -> str | None:
    cfg_data, cfg_path, day, idx, total = args
    from ..analytics.engine import AnalyticsEngine
    from ..camera.simulator import SimulatorSource
    from ..config import Config
    from ..storage.db import Store

    cfg = Config(copy.deepcopy(cfg_data), Path(cfg_path) if cfg_path else None)
    timeutil.set_timezone(cfg["device"]["timezone"])
    d = date.fromisoformat(day)
    scale = WEEKDAY_SCALE[d.weekday()]
    if scale == 0:
        return None
    rng = random.Random(f"{cfg['device']['id']}-{day}")
    scale *= BASE_SCALE * rng.gauss(1.0, 0.08) * (1 + 0.004 * idx)
    o, c = int(cfg["reports"]["opening_hour"]), int(cfg["reports"]["closing_hour"])
    start = datetime(d.year, d.month, d.day, o, tzinfo=timeutil.tz()).timestamp()
    sim = cfg.data.setdefault("simulator", {})
    sim.update(realtime=False, start_ts=start, duration_s=(c - o) * 3600, seed=rng.randint(0, 10**9),
               arrival_scale=scale, hour_override=None, staff=3 if d.weekday() in (0, 4) else 2)
    fd, tmp = tempfile.mkstemp(suffix=".db", prefix=f"pc-{day}-")
    os.close(fd)
    store = Store(tmp)
    engine = AnalyticsEngine(cfg, store)
    for frame in SimulatorSource(cfg).frames():
        engine.process(frame)
    engine.flush()
    store.close()
    return tmp


def merge(dst: sqlite3.Connection, src_path: str) -> None:
    dst.execute("ATTACH DATABASE ? AS src", (src_path,))  # not allowed inside a transaction
    try:
        dst.execute("BEGIN")
        for table, cols in MERGE_APPEND.items():
            dst.execute(f"INSERT OR IGNORE INTO {table}({cols}) SELECT {cols} FROM src.{table}")
        dst.execute("INSERT OR REPLACE INTO zone_hours SELECT * FROM src.zone_hours")
        dst.execute("INSERT OR REPLACE INTO heatmaps SELECT * FROM src.heatmaps")
        dst.execute("COMMIT")
    finally:
        dst.execute("DETACH DATABASE src")


def backfill(cfg, days: int = 35, end: str | None = None, workers: int = 0, reports_last: int = 14) -> None:
    from ..reports.daily import generate_report
    from ..storage.db import Store

    end = end or timeutil.shift_day(timeutil.today(), -1)
    day_list = [timeutil.shift_day(end, -i) for i in range(days - 1, -1, -1)]
    store = Store(cfg.resolve(cfg["storage"]["path"]))
    workers = workers or max(1, (os.cpu_count() or 2) - 1)
    jobs = [(cfg.data, str(cfg.path) if cfg.path else None, d, i, len(day_list)) for i, d in enumerate(day_list)]
    log.info("%d gün simüle ediliyor (%d işçi)...", len(jobs), workers)
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for day, tmp in zip(day_list, ex.map(_simulate_day, jobs)):
            if tmp is None:
                log.info("%s kapalı (Pazar)", day)
                continue
            with store.lock:
                merge(store.conn, tmp)
            os.unlink(tmp)
            for ext in ("-wal", "-shm"):
                if os.path.exists(tmp + ext):
                    os.unlink(tmp + ext)
            log.info("%s tamam: %s giriş", day, store.day_counts(day)["entries"])
    from ..analytics.heatmap import encode_jpeg
    from ..camera.simulator import render_background

    s = cfg.data.get("simulator", {})
    background = render_background(int(s.get("width", 640)), int(s.get("height", 480)))
    bg_path = cfg.resolve(cfg["storage"]["path"]).parent / "background.jpg"
    bg_path.write_bytes(encode_jpeg(background, 90))
    out_dir = cfg.resolve(cfg["reports"]["dir"])
    for day in [d for d in day_list if store.day_counts(d)["entries"]][-reports_last:]:
        generate_report(store, cfg, day, out_dir, background=background)
    log.info("Bitti: %s", cfg.resolve(cfg["storage"]["path"]))
