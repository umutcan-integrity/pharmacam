"""Device -> hub synchronisation.

Store-and-forward: the device database *is* the queue. Each tick sends rows
the hub has not acknowledged yet (by rowid for append-only tables, by
updated_at for accumulating ones) plus a live heartbeat. If the internet is
down nothing is lost; the next successful tick catches up in batches.

Protocol: POST {hub}/api/v1/ingest, gzip JSON, `Authorization: Bearer <token>`.
The hub answers 200 {"ok": true, "accepted": {...}} only after committing.
"""
from __future__ import annotations

import base64
import gzip
import json
import logging
import time
import urllib.error
import urllib.request

from .. import __version__
from ..storage.db import SYNC_TABLES_APPEND, SYNC_TABLES_UPSERT

log = logging.getLogger(__name__)

BATCH = 500
MAX_BATCHES_PER_TICK = 10
VISIT_COLS = ("uid", "day", "hour", "started_at", "ended_at", "duration_s", "is_staff", "entered", "exited",
              "queue_wait_s", "service_s", "served", "abandoned", "zones")


def build_payload(rt, include_rows: bool = True) -> tuple[dict, dict]:
    """Return (payload, cursor updates to persist after a successful send)."""
    store, cfg = rt.store, rt.cfg
    live = rt.engine.snapshot()
    live.pop("tracks", None)
    payload: dict = {
        "schema": 1,
        "sent_at": time.time(),
        "device": {
            "id": cfg["device"]["id"],
            "pharmacy_name": cfg["device"]["pharmacy_name"],
            "timezone": cfg["device"]["timezone"],
            "version": __version__,
            "zones": [z.to_dict() for z in cfg.zones],
        },
        "live": live,
        "health": rt.health(),
    }
    cursors: dict[str, str] = {}
    if not include_rows:
        return payload, cursors
    for table in SYNC_TABLES_APPEND:
        if table == "events":
            continue  # raw events stay on the device; visits/minutes carry the aggregates
        last = int(store.get_meta(f"sync_{table}", "0"))
        rows = store.rows_after(table, last, BATCH)
        if rows:
            cursors[f"sync_{table}"] = str(rows[-1]["id"])
            if table == "visits":
                rows = [{k: r[k] for k in VISIT_COLS} for r in rows]
            else:
                for r in rows:
                    r.pop("id", None)
        payload[table] = rows
    for table in SYNC_TABLES_UPSERT:
        since = float(store.get_meta(f"sync_{table}", "0"))
        rows = store.rows_updated_after(table, since, BATCH if table == "zone_hours" else 48)
        if rows:
            cursors[f"sync_{table}"] = repr(rows[-1]["updated_at"])
        if table == "heatmaps":
            for r in rows:
                r["data"] = base64.b64encode(r["data"]).decode()
        payload[table] = rows
    return payload, cursors


class SyncClient:
    def __init__(self, rt):
        self.rt = rt
        s = rt.cfg["sync"]
        self.url = s["hub_url"].rstrip("/") + "/api/v1/ingest"
        self.token = s["device_token"]
        self.interval = float(s["interval_seconds"])
        self.status = rt.sync_status
        self.status.update(enabled=True, hub=s["hub_url"], last_ok=None, error=None, pending=False)

    def post(self, payload: dict) -> dict:
        body = gzip.compress(json.dumps(payload, separators=(",", ":")).encode())
        req = urllib.request.Request(self.url, data=body, method="POST", headers={
            "Content-Type": "application/json",
            "Content-Encoding": "gzip",
            "Authorization": f"Bearer {self.token}",
            "User-Agent": f"pharmacam/{__version__}",
        })
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read() or b"{}")

    def tick(self) -> bool:
        """One sync round. Returns True if more rows are waiting."""
        for _ in range(MAX_BATCHES_PER_TICK):
            payload, cursors = build_payload(self.rt)
            self.post(payload)
            for k, v in cursors.items():
                self.rt.store.set_meta(k, v)
            more = any(len(payload.get(t, [])) >= BATCH for t in ("visits", "minutes"))
            if not more:
                return False
        return True

    def loop(self) -> None:
        backoff = self.interval
        while not self.rt._stop.wait(backoff):
            try:
                pending = self.tick()
                self.status.update(last_ok=time.time(), error=None, pending=pending)
                backoff = 2.0 if pending else self.interval
            except urllib.error.HTTPError as e:
                self.status["error"] = f"HTTP {e.code}"
                log.warning("Senkronizasyon reddedildi: HTTP %s", e.code)
                backoff = min(backoff * 2, 600)
            except Exception as e:  # offline, DNS, timeout...
                self.status["error"] = str(e)[:120]
                log.info("Senkronizasyon bekliyor (bağlantı yok?): %s", e)
                backoff = min(backoff * 2, 600)
