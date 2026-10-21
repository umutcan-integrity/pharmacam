"""PharmaCam Hub — central server for many pharmacies.

Devices push their data here (see pharmacam/sync/client.py). The hub keeps a
copy per device and serves a fleet dashboard: which pharmacy is online, how
busy each is right now, daily trends and comparisons.

Configuration (environment):
    PHARMACAM_HUB_DB              sqlite path (default data/hub.db)
    PHARMACAM_HUB_TOKENS          "device_id:token,device_id2:token2"
    PHARMACAM_HUB_ADMIN_USER      dashboard user (default admin)
    PHARMACAM_HUB_ADMIN_PASSWORD  dashboard password (empty = no auth, dev only)

Run:  uvicorn --factory pharmacam.hub.app:create_hub --host 0.0.0.0 --port 9000
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles

from .. import __version__

STATIC = Path(__file__).parent / "static"
EDGE_STATIC = Path(__file__).parent.parent / "api" / "static"
ONLINE_S = 180
DAY_RE = r"^\d{4}-\d{2}-\d{2}$"

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY,
    pharmacy_name TEXT,
    timezone TEXT,
    version TEXT,
    zones TEXT,
    last_seen REAL,
    live TEXT,
    health TEXT
);
CREATE TABLE IF NOT EXISTS visits (
    device_id TEXT NOT NULL, uid TEXT NOT NULL, day TEXT, hour INTEGER, started_at REAL, ended_at REAL,
    duration_s REAL, is_staff INTEGER, entered INTEGER, exited INTEGER, queue_wait_s REAL, service_s REAL,
    served INTEGER, abandoned INTEGER, zones TEXT, gender TEXT, age REAL,
    PRIMARY KEY (device_id, uid)
);
CREATE INDEX IF NOT EXISTS idx_hv_day ON visits(device_id, day);
CREATE TABLE IF NOT EXISTS minutes (
    device_id TEXT NOT NULL, ts REAL NOT NULL, day TEXT, hour INTEGER, entries INTEGER, exits INTEGER,
    staff_entries INTEGER, occupancy_avg REAL, occupancy_max INTEGER, visible_avg REAL, queue_avg REAL,
    queue_max INTEGER, staff_avg REAL, served INTEGER, wait_sum REAL, samples INTEGER,
    PRIMARY KEY (device_id, ts)
);
CREATE INDEX IF NOT EXISTS idx_hm_day ON minutes(device_id, day);
CREATE TABLE IF NOT EXISTS zone_hours (
    device_id TEXT NOT NULL, hour_key TEXT NOT NULL, day TEXT, zone TEXT NOT NULL, visits INTEGER,
    passes INTEGER, dwell_total REAL, dwell_max REAL, updated_at REAL,
    PRIMARY KEY (device_id, hour_key, zone)
);
CREATE TABLE IF NOT EXISTS heatmaps (
    device_id TEXT NOT NULL, hour_key TEXT NOT NULL, day TEXT, w INTEGER, h INTEGER, data BLOB, updated_at REAL,
    PRIMARY KEY (device_id, hour_key)
);
"""
MINUTE_COLS = ("ts", "day", "hour", "entries", "exits", "staff_entries", "occupancy_avg", "occupancy_max",
               "visible_avg", "queue_avg", "queue_max", "staff_avg", "served", "wait_sum", "samples")
VISIT_COLS = ("uid", "day", "hour", "started_at", "ended_at", "duration_s", "is_staff", "entered", "exited",
              "queue_wait_s", "service_s", "served", "abandoned", "zones", "gender", "age")
ZONE_COLS = ("hour_key", "day", "zone", "visits", "passes", "dwell_total", "dwell_max", "updated_at")


class HubDB:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        have = {r[1] for r in self.conn.execute("PRAGMA table_info(visits)")}
        for col, typ in (("gender", "TEXT"), ("age", "REAL")):  # hubs created before the customer profile
            if col not in have:
                self.conn.execute(f"ALTER TABLE visits ADD COLUMN {col} {typ}")
        self.lock = threading.RLock()

    def q(self, sql: str, args=()) -> list[dict[str, Any]]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, tuple(args)).fetchall()]

    def ingest(self, device_id: str, p: dict[str, Any]) -> dict[str, int]:
        d = p.get("device", {})
        acc = {}
        with self.lock:
            c = self.conn
            c.execute("BEGIN")
            try:
                c.execute(
                    """INSERT INTO devices(id,pharmacy_name,timezone,version,zones,last_seen,live,health)
                       VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET pharmacy_name=excluded.pharmacy_name,
                       timezone=excluded.timezone, version=excluded.version, zones=excluded.zones,
                       last_seen=excluded.last_seen, live=excluded.live, health=excluded.health""",
                    (device_id, d.get("pharmacy_name"), d.get("timezone"), d.get("version"),
                     json.dumps(d.get("zones", [])), time.time(), json.dumps(p.get("live", {})),
                     json.dumps(p.get("health", {}))))
                rows = p.get("minutes", [])
                c.executemany(
                    f"INSERT OR REPLACE INTO minutes(device_id,{','.join(MINUTE_COLS)}) "
                    f"VALUES(?,{','.join('?' * len(MINUTE_COLS))})",
                    [(device_id, *[r.get(k) for k in MINUTE_COLS]) for r in rows])
                acc["minutes"] = len(rows)
                rows = p.get("visits", [])
                c.executemany(
                    f"INSERT OR REPLACE INTO visits(device_id,{','.join(VISIT_COLS)}) "
                    f"VALUES(?,{','.join('?' * len(VISIT_COLS))})",
                    [(device_id, *[r.get(k) for k in VISIT_COLS]) for r in rows])
                acc["visits"] = len(rows)
                rows = p.get("zone_hours", [])
                c.executemany(
                    f"INSERT OR REPLACE INTO zone_hours(device_id,{','.join(ZONE_COLS)}) "
                    f"VALUES(?,{','.join('?' * len(ZONE_COLS))})",
                    [(device_id, *[r.get(k) for k in ZONE_COLS]) for r in rows])
                acc["zone_hours"] = len(rows)
                rows = p.get("heatmaps", [])
                c.executemany(
                    "INSERT OR REPLACE INTO heatmaps(device_id,hour_key,day,w,h,data,updated_at) VALUES(?,?,?,?,?,?,?)",
                    [(device_id, r["hour_key"], r["day"], r["w"], r["h"], base64.b64decode(r["data"]), r["updated_at"])
                     for r in rows])
                acc["heatmaps"] = len(rows)
                c.execute("COMMIT")
            except Exception:
                c.execute("ROLLBACK")
                raise
        return acc


def _tokens() -> dict[str, str]:
    """token -> device_id"""
    out = {}
    for pair in filter(None, os.environ.get("PHARMACAM_HUB_TOKENS", "").split(",")):
        dev, _, tok = pair.strip().partition(":")
        if dev and tok:
            out[hashlib.sha256(tok.encode()).hexdigest()] = dev
    return out


def create_hub(db_path: str | None = None) -> FastAPI:
    db = HubDB(db_path or os.environ.get("PHARMACAM_HUB_DB", "data/hub.db"))
    tokens = _tokens()
    app = FastAPI(title="PharmaCam Hub API", version=__version__,
                  description="Çoklu eczane merkezi: cihazlardan veri toplar ve filo panelini sunar")
    security = HTTPBasic(auto_error=False)
    admin_user = os.environ.get("PHARMACAM_HUB_ADMIN_USER", "admin")
    admin_pw = os.environ.get("PHARMACAM_HUB_ADMIN_PASSWORD", "")

    def admin(creds: HTTPBasicCredentials | None = Depends(security)) -> None:
        if not admin_pw:
            return
        if not (creds and secrets.compare_digest(creds.username, admin_user)
                and secrets.compare_digest(creds.password, admin_pw)):
            raise HTTPException(401, "Yetkisiz", headers={"WWW-Authenticate": 'Basic realm="PharmaCam Hub"'})

    @app.post("/api/v1/ingest", tags=["Cihaz"])
    async def ingest(request: Request, authorization: str = Header("")):
        tok = authorization.removeprefix("Bearer ").strip()
        device_id = tokens.get(hashlib.sha256(tok.encode()).hexdigest()) if tok else None
        if not device_id:
            raise HTTPException(401, "Geçersiz cihaz anahtarı")
        raw = await request.body()
        if request.headers.get("content-encoding") == "gzip":
            raw = gzip.decompress(raw)
        try:
            payload = json.loads(raw)
        except ValueError:
            raise HTTPException(400, "Geçersiz JSON")
        claimed = payload.get("device", {}).get("id")
        if claimed and claimed != device_id:
            raise HTTPException(403, "Anahtar bu cihaza ait değil")
        return {"ok": True, "device_id": device_id, "accepted": db.ingest(device_id, payload)}

    def today_stats(device_id: str, day: str) -> dict[str, Any]:
        m = db.q("""SELECT COALESCE(SUM(entries),0) entries, COALESCE(MAX(occupancy_max),0) peak,
                           COALESCE(SUM(served),0) served, COALESCE(SUM(wait_sum),0) wait_sum
                    FROM minutes WHERE device_id=? AND day=?""", (device_id, day))[0]
        v = db.q("""SELECT SUM(abandoned) ab, AVG(duration_s) dur, SUM(gender='F') f, SUM(gender='M') m
                    FROM visits WHERE device_id=? AND day=? AND is_staff=0""", (device_id, day))[0]
        ages = [r["age"] for r in db.q("SELECT age FROM visits WHERE device_id=? AND day=? AND is_staff=0 "
                                       "AND age IS NOT NULL", (device_id, day))]
        known = (v["f"] or 0) + (v["m"] or 0)
        return {
            "entries": m["entries"], "peak_occupancy": m["peak"], "served": m["served"],
            "avg_wait_s": round(m["wait_sum"] / m["served"], 1) if m["served"] else None,
            "abandoned": v["ab"] or 0, "avg_visit_s": round(v["dur"], 1) if v["dur"] else None,
            # estimated customer profile, when the device has it switched on
            "female_pct": round(100 * (v["f"] or 0) / known) if known else None,
            "median_age": round(float(np.median(ages))) if ages else None,
        }

    def device_day(device: dict) -> str:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(device.get("timezone") or "Europe/Istanbul")).strftime("%Y-%m-%d")

    @app.get("/api/v1/devices", tags=["Panel"], dependencies=[Depends(admin)])
    def devices():
        out = []
        for d in db.q("SELECT * FROM devices ORDER BY pharmacy_name"):
            live = json.loads(d["live"] or "{}")
            health = json.loads(d["health"] or "{}")
            day = device_day(d)
            out.append({
                "id": d["id"], "pharmacy_name": d["pharmacy_name"], "version": d["version"],
                "last_seen": d["last_seen"], "online": time.time() - (d["last_seen"] or 0) < ONLINE_S,
                "camera_ok": health.get("camera_ok"), "live": {k: live.get(k) for k in (
                    "occupancy", "queue_length", "staff_on_duty", "entries_today", "queue_waits")},
                "today": today_stats(d["id"], day), "day": day,
                "system": health.get("system", {}),
            })
        return out

    @app.get("/api/v1/devices/{device_id}/daily", tags=["Panel"], dependencies=[Depends(admin)])
    def daily(device_id: str, start: str = Query(..., pattern=DAY_RE), end: str = Query(..., pattern=DAY_RE)):
        return db.q("""SELECT day, SUM(entries) entries, MAX(occupancy_max) peak_occupancy, SUM(served) served,
                              CASE WHEN SUM(served)>0 THEN ROUND(SUM(wait_sum)/SUM(served),1) END avg_wait_s
                       FROM minutes WHERE device_id=? AND day BETWEEN ? AND ? GROUP BY day ORDER BY day""",
                    (device_id, start, end))

    @app.get("/api/v1/devices/{device_id}/hourly", tags=["Panel"], dependencies=[Depends(admin)])
    def hourly(device_id: str, day: str = Query(..., pattern=DAY_RE)):
        return db.q("""SELECT hour, SUM(entries) entries, ROUND(AVG(occupancy_avg),2) occupancy_avg,
                              MAX(queue_max) queue_max
                       FROM minutes WHERE device_id=? AND day=? GROUP BY hour ORDER BY hour""", (device_id, day))

    @app.get("/api/v1/devices/{device_id}/zones", tags=["Panel"], dependencies=[Depends(admin)])
    def zones(device_id: str, start: str = Query(..., pattern=DAY_RE), end: str = Query(..., pattern=DAY_RE)):
        dev = db.q("SELECT zones FROM devices WHERE id=?", (device_id,))
        names = {z["id"]: z["name"] for z in json.loads(dev[0]["zones"] or "[]")} if dev else {}
        rows = db.q("""SELECT zone, SUM(visits) stops, SUM(passes) passes, SUM(dwell_total) dwell
                       FROM zone_hours WHERE device_id=? AND day BETWEEN ? AND ? GROUP BY zone ORDER BY stops DESC""",
                    (device_id, start, end))
        for r in rows:
            r["name"] = names.get(r["zone"], r["zone"])
            r["avg_dwell_s"] = round(r["dwell"] / r["stops"], 1) if r["stops"] else None
        return rows

    @app.get("/api/v1/devices/{device_id}/heatmap.png", tags=["Panel"], dependencies=[Depends(admin)])
    def heatmap(device_id: str, start: str = Query(..., pattern=DAY_RE), end: str = Query(..., pattern=DAY_RE)):
        from ..analytics import heatmap as hm

        total = None
        for r in db.q("SELECT w,h,data FROM heatmaps WHERE device_id=? AND day BETWEEN ? AND ?", (device_id, start, end)):
            g = np.frombuffer(r["data"], dtype=np.float32).reshape(r["h"], r["w"])
            total = g.copy() if total is None else (total + g if total.shape == g.shape else total)
        return Response(hm.encode_png(hm.render(total, None)), media_type="image/png")

    @app.get("/api/v1/overview", tags=["Panel"], dependencies=[Depends(admin)])
    def overview():
        devs = devices()
        return {
            "pharmacies": len(devs),
            "online": sum(d["online"] for d in devs),
            "inside_now": sum((d["live"]["occupancy"] or 0) for d in devs if d["online"]),
            "queue_now": sum((d["live"]["queue_length"] or 0) for d in devs if d["online"]),
            "entries_today": sum(d["today"]["entries"] for d in devs),
        }

    @app.get("/api/v1/health", tags=["Durum"])
    def health():
        return {"ok": True, "version": __version__, "devices": len(db.q("SELECT id FROM devices"))}

    @app.get("/", include_in_schema=False, dependencies=[Depends(admin)])
    def index():
        return FileResponse(STATIC / "index.html")

    app.mount("/static/vendor", StaticFiles(directory=EDGE_STATIC / "vendor"), name="vendor")
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    app.state.db = db
    return app

