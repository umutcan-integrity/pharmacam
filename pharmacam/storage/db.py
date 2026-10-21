"""SQLite storage for historical analytics.

Design notes
  * WAL mode: the analytics thread writes while the API reads.
  * Every row carries local `day` (and `hour` where useful) so queries group
    by the pharmacy's calendar without timezone math in SQL.
  * Append-only tables (visits, events, minutes) sync to the hub by rowid;
    accumulating tables (zone_hours, heatmaps) carry `updated_at`.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from .. import timeutil

SCHEMA = """
CREATE TABLE IF NOT EXISTS audits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at REAL NOT NULL,
    ended_at REAL,
    day TEXT,
    taps TEXT,
    result TEXT,
    note TEXT
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS visits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT UNIQUE,
    day TEXT NOT NULL,
    hour INTEGER NOT NULL,
    started_at REAL NOT NULL,
    ended_at REAL NOT NULL,
    duration_s REAL NOT NULL,
    is_staff INTEGER NOT NULL DEFAULT 0,
    coat_score REAL,
    entered INTEGER NOT NULL DEFAULT 0,
    exited INTEGER NOT NULL DEFAULT 0,
    queue_wait_s REAL,
    service_s REAL,
    served INTEGER NOT NULL DEFAULT 0,
    abandoned INTEGER NOT NULL DEFAULT 0,
    zones TEXT,
    gender TEXT,
    age REAL,
    demo_n INTEGER
);
CREATE INDEX IF NOT EXISTS idx_visits_day ON visits(day);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    day TEXT NOT NULL,
    type TEXT NOT NULL,
    track_uid TEXT,
    zone TEXT,
    value REAL,
    payload TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_day_type ON events(day, type);

CREATE TABLE IF NOT EXISTS minutes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL UNIQUE,
    day TEXT NOT NULL,
    hour INTEGER NOT NULL,
    entries INTEGER NOT NULL DEFAULT 0,
    exits INTEGER NOT NULL DEFAULT 0,
    staff_entries INTEGER NOT NULL DEFAULT 0,
    occupancy_avg REAL NOT NULL DEFAULT 0,
    occupancy_max INTEGER NOT NULL DEFAULT 0,
    visible_avg REAL NOT NULL DEFAULT 0,
    queue_avg REAL NOT NULL DEFAULT 0,
    queue_max INTEGER NOT NULL DEFAULT 0,
    staff_avg REAL NOT NULL DEFAULT 0,
    served INTEGER NOT NULL DEFAULT 0,
    wait_sum REAL NOT NULL DEFAULT 0,
    samples INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_minutes_day ON minutes(day);

CREATE TABLE IF NOT EXISTS zone_hours (
    hour_key TEXT NOT NULL,
    day TEXT NOT NULL,
    zone TEXT NOT NULL,
    visits INTEGER NOT NULL DEFAULT 0,
    passes INTEGER NOT NULL DEFAULT 0,
    dwell_total REAL NOT NULL DEFAULT 0,
    dwell_max REAL NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL,
    PRIMARY KEY (hour_key, zone)
);
CREATE INDEX IF NOT EXISTS idx_zone_hours_day ON zone_hours(day);

CREATE TABLE IF NOT EXISTS heatmaps (
    hour_key TEXT PRIMARY KEY,
    day TEXT NOT NULL,
    w INTEGER NOT NULL,
    h INTEGER NOT NULL,
    data BLOB NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_heatmaps_day ON heatmaps(day);

CREATE TABLE IF NOT EXISTS reports (
    day TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""

SYNC_TABLES_APPEND = ("visits", "minutes", "events")
SYNC_TABLES_UPSERT = ("zone_hours", "heatmaps")


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Add columns introduced after a device was installed (SQLite has no IF NOT EXISTS for columns)."""
        have = {r["name"] for r in self.conn.execute("PRAGMA table_info(visits)").fetchall()}
        for col, decl in (("gender", "TEXT"), ("age", "REAL"), ("demo_n", "INTEGER")):
            if col not in have:
                self.conn.execute(f"ALTER TABLE visits ADD COLUMN {col} {decl}")

    # ------------------------------------------------------------- helpers
    def _exec(self, sql: str, args: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self.lock:
            return self.conn.execute(sql, tuple(args))

    def query(self, sql: str, args: Iterable[Any] = ()) -> list[dict[str, Any]]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, tuple(args)).fetchall()]

    def one(self, sql: str, args: Iterable[Any] = ()) -> dict[str, Any] | None:
        rows = self.query(sql, args)
        return rows[0] if rows else None

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        r = self.one("SELECT value FROM meta WHERE key=?", (key,))
        return r["value"] if r else default

    def set_meta(self, key: str, value: str) -> None:
        self._exec("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (key, value))

    # -------------------------------------------------------------- writes
    def add_event(self, ts: float, type_: str, track_uid: str | None, zone: str | None,
                  value: float | None, payload: dict | None) -> None:
        self._exec("INSERT INTO events(ts,day,type,track_uid,zone,value,payload) VALUES(?,?,?,?,?,?,?)",
                   (ts, timeutil.day_str(ts), type_, track_uid, zone,
                    None if value is None else round(float(value), 2),
                    json.dumps(payload) if payload else None))

    def add_visit(self, v: dict[str, Any]) -> None:
        v = dict(v)
        v["day"] = timeutil.day_str(v["started_at"])
        v["hour"] = timeutil.local(v["started_at"]).hour
        cols = ",".join(v.keys())
        qs = ",".join("?" * len(v))
        self._exec(f"INSERT OR IGNORE INTO visits({cols}) VALUES({qs})", v.values())

    def add_minute(self, m: dict[str, Any]) -> None:
        m = dict(m)
        m["day"] = timeutil.day_str(m["ts"])
        m["hour"] = timeutil.local(m["ts"]).hour
        cols = ",".join(m.keys())
        qs = ",".join("?" * len(m))
        upd = ",".join(f"{k}={k}+excluded.{k}" for k in ("entries", "exits", "staff_entries", "served", "wait_sum"))
        self._exec(f"INSERT INTO minutes({cols}) VALUES({qs}) ON CONFLICT(ts) DO UPDATE SET {upd}", m.values())

    def add_zone_hour(self, hour_key: str, stats: dict[str, dict[str, Any]]) -> None:
        now = time.time()
        day = hour_key[:10]
        with self.lock:
            for zone, s in stats.items():
                self.conn.execute(
                    """INSERT INTO zone_hours(hour_key,day,zone,visits,passes,dwell_total,dwell_max,updated_at)
                       VALUES(?,?,?,?,?,?,?,?)
                       ON CONFLICT(hour_key,zone) DO UPDATE SET
                         visits=visits+excluded.visits, passes=passes+excluded.passes,
                         dwell_total=dwell_total+excluded.dwell_total,
                         dwell_max=MAX(dwell_max,excluded.dwell_max), updated_at=excluded.updated_at""",
                    (hour_key, day, zone, s["visits"], s["passes"], round(s["dwell_total"], 2),
                     round(s["dwell_max"], 2), now))

    def add_heatmap(self, hour_key: str, grid: np.ndarray) -> None:
        grid = grid.astype(np.float32)
        with self.lock:
            r = self.conn.execute("SELECT w,h,data FROM heatmaps WHERE hour_key=?", (hour_key,)).fetchone()
            if r and r["w"] == grid.shape[1] and r["h"] == grid.shape[0]:
                grid = grid + np.frombuffer(r["data"], dtype=np.float32).reshape(r["h"], r["w"])
            self.conn.execute(
                """INSERT INTO heatmaps(hour_key,day,w,h,data,updated_at) VALUES(?,?,?,?,?,?)
                   ON CONFLICT(hour_key) DO UPDATE SET w=excluded.w,h=excluded.h,data=excluded.data,
                   updated_at=excluded.updated_at""",
                (hour_key, hour_key[:10], grid.shape[1], grid.shape[0], grid.tobytes(), time.time()))

    def add_report(self, day: str, path: str) -> None:
        self._exec("INSERT INTO reports(day,path,created_at) VALUES(?,?,?) "
                   "ON CONFLICT(day) DO UPDATE SET path=excluded.path, created_at=excluded.created_at",
                   (day, path, time.time()))

    def prune(self, retention_days: int) -> None:
        cutoff = timeutil.shift_day(timeutil.today(), -retention_days)
        with self.lock:
            for t in ("visits", "events", "minutes", "zone_hours", "heatmaps"):
                self.conn.execute(f"DELETE FROM {t} WHERE day < ?", (cutoff,))

    # --------------------------------------------------------------- reads
    def day_counts(self, day: str) -> dict[str, int]:
        r = self.one("SELECT COALESCE(SUM(entries),0) e, COALESCE(SUM(exits),0) x, "
                     "COALESCE(SUM(staff_entries),0) s FROM minutes WHERE day=?", (day,))
        return {"entries": int(r["e"]), "exits": int(r["x"]), "staff_entries": int(r["s"])}

    def heatmap(self, start_day: str, end_day: str) -> np.ndarray | None:
        rows = self.query("SELECT w,h,data FROM heatmaps WHERE day BETWEEN ? AND ?", (start_day, end_day))
        total = None
        for r in rows:
            g = np.frombuffer(r["data"], dtype=np.float32).reshape(r["h"], r["w"])
            if total is None:
                total = g.copy()
            elif total.shape == g.shape:
                total += g
        return total

    def days_with_data(self) -> list[str]:
        return [r["day"] for r in self.query("SELECT DISTINCT day FROM minutes ORDER BY day")]

    # ---------------------------------------------------------------- sync
    def rows_after(self, table: str, last_id: int, limit: int = 500) -> list[dict[str, Any]]:
        assert table in SYNC_TABLES_APPEND
        return self.query(f"SELECT * FROM {table} WHERE id > ? ORDER BY id LIMIT ?", (last_id, limit))

    def rows_updated_after(self, table: str, since: float, limit: int = 200) -> list[dict[str, Any]]:
        assert table in SYNC_TABLES_UPSERT
        return self.query(f"SELECT * FROM {table} WHERE updated_at > ? ORDER BY updated_at LIMIT ?", (since, limit))

    def close(self) -> None:
        with self.lock:
            self.conn.close()
