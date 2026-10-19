"""Read-side analytics shared by the API, the daily report and the hub sync."""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

import numpy as np

from .. import timeutil
from ..storage.db import Store


def _r(v: float | None, n: int = 1) -> float | None:
    return None if v is None else round(float(v), n)


def summary(store: Store, day: str) -> dict[str, Any]:
    """KPIs for one day."""
    m = store.one(
        """SELECT COALESCE(SUM(entries),0) entries, COALESCE(SUM(exits),0) exits,
                  COALESCE(SUM(staff_entries),0) staff_entries,
                  COALESCE(MAX(occupancy_max),0) peak_occupancy,
                  COALESCE(MAX(queue_max),0) peak_queue,
                  AVG(CASE WHEN samples>0 THEN staff_avg END) staff_avg,
                  COUNT(*) minutes
           FROM minutes WHERE day=?""", (day,))
    v = store.one(
        """SELECT COUNT(*) visits,
                  AVG(duration_s) avg_visit_s,
                  SUM(served) served,
                  SUM(abandoned) abandoned,
                  AVG(CASE WHEN served=1 THEN queue_wait_s END) avg_wait_s,
                  MAX(queue_wait_s) max_wait_s,
                  AVG(CASE WHEN served=1 THEN service_s END) avg_service_s
           FROM visits WHERE day=? AND is_staff=0""", (day,))
    staff_people = store.one("SELECT COUNT(*) n FROM visits WHERE day=? AND is_staff=1", (day,))["n"]
    hourly = store.query("SELECT hour, SUM(entries) e FROM minutes WHERE day=? GROUP BY hour ORDER BY e DESC LIMIT 1",
                         (day,))
    busiest = hourly[0]["hour"] if hourly and hourly[0]["e"] else None
    waits = [r["queue_wait_s"] for r in store.query(
        "SELECT queue_wait_s FROM visits WHERE day=? AND is_staff=0 AND served=1 AND queue_wait_s IS NOT NULL",
        (day,))]
    p90 = float(np.percentile(waits, 90)) if waits else None
    top_zone = store.one(
        "SELECT zone, SUM(visits) n FROM zone_hours WHERE day=? GROUP BY zone ORDER BY n DESC LIMIT 1", (day,))
    entries = int(m["entries"])
    served = int(v["served"] or 0)
    return {
        "day": day,
        "weekday": timeutil.WEEKDAYS_TR[timeutil.local(timeutil.day_bounds(day)[0]).weekday()],
        "entries": entries,
        "exits": int(m["exits"]),
        "staff_entries": int(m["staff_entries"]),
        "visits": int(v["visits"] or 0),
        "peak_occupancy": int(m["peak_occupancy"]),
        "peak_queue": int(m["peak_queue"]),
        "busiest_hour": busiest,
        "avg_visit_s": _r(v["avg_visit_s"]),
        "served": served,
        "abandoned": int(v["abandoned"] or 0),
        "service_rate": _r(100.0 * served / entries, 1) if entries else None,
        "avg_wait_s": _r(v["avg_wait_s"]),
        "p90_wait_s": _r(p90),
        "max_wait_s": _r(v["max_wait_s"]),
        "avg_service_s": _r(v["avg_service_s"]),
        "staff_people": int(staff_people),
        "staff_avg": _r(m["staff_avg"], 2),
        "top_zone": top_zone["zone"] if top_zone and top_zone["n"] else None,
        "active_minutes": int(m["minutes"]),
    }


def comparable_entries(store: Store, day: str, ref_day: str, now_ts: float | None = None) -> tuple[int, int]:
    """(entries on day, entries on ref_day) — for an unfinished day both are cut at the
    same local time of day, so the morning is not compared with a full day."""
    full = store.day_counts(day)["entries"], store.day_counts(ref_day)["entries"]
    if day != timeutil.today():
        return full
    import time as _t

    now_ts = now_ts or _t.time()
    elapsed = now_ts - timeutil.day_bounds(day)[0]
    cut = timeutil.day_bounds(ref_day)[0] + elapsed
    r = store.one("SELECT COALESCE(SUM(entries),0) e FROM minutes WHERE day=? AND ts < ?", (ref_day, cut))
    return full[0], int(r["e"])


def hourly(store: Store, day: str) -> list[dict[str, Any]]:
    rows = {r["hour"]: r for r in store.query(
        """SELECT hour, SUM(entries) entries, SUM(exits) exits, AVG(occupancy_avg) occupancy_avg,
                  MAX(occupancy_max) occupancy_max, AVG(queue_avg) queue_avg, MAX(queue_max) queue_max,
                  AVG(staff_avg) staff_avg, SUM(served) served, SUM(wait_sum) wait_sum
           FROM minutes WHERE day=? GROUP BY hour""", (day,))}
    out = []
    for h in range(24):
        r = rows.get(h)
        out.append({
            "hour": h,
            "entries": int(r["entries"]) if r else 0,
            "exits": int(r["exits"]) if r else 0,
            "occupancy_avg": _r(r["occupancy_avg"], 2) if r else 0,
            "occupancy_max": int(r["occupancy_max"]) if r else 0,
            "queue_avg": _r(r["queue_avg"], 2) if r else 0,
            "queue_max": int(r["queue_max"]) if r else 0,
            "staff_avg": _r(r["staff_avg"], 2) if r else 0,
            "avg_wait_s": _r(r["wait_sum"] / r["served"]) if r and r["served"] else None,
        })
    return out


def minutes(store: Store, day: str) -> list[dict[str, Any]]:
    return store.query(
        """SELECT ts, entries, exits, occupancy_avg, occupancy_max, queue_avg, queue_max, staff_avg
           FROM minutes WHERE day=? ORDER BY ts""", (day,))


def daily(store: Store, start: str, end: str) -> list[dict[str, Any]]:
    m = {r["day"]: r for r in store.query(
        """SELECT day, SUM(entries) entries, SUM(exits) exits, MAX(occupancy_max) peak_occupancy,
                  MAX(queue_max) peak_queue, SUM(served) served, SUM(wait_sum) wait_sum
           FROM minutes WHERE day BETWEEN ? AND ? GROUP BY day""", (start, end))}
    v = {r["day"]: r for r in store.query(
        """SELECT day, AVG(duration_s) avg_visit_s, SUM(abandoned) abandoned
           FROM visits WHERE day BETWEEN ? AND ? AND is_staff=0 GROUP BY day""", (start, end))}
    out = []
    for d in timeutil.day_range(start, end):
        r, w = m.get(d), v.get(d)
        out.append({
            "day": d,
            "weekday": timeutil.local(timeutil.day_bounds(d)[0]).weekday(),
            "entries": int(r["entries"]) if r else 0,
            "exits": int(r["exits"]) if r else 0,
            "peak_occupancy": int(r["peak_occupancy"]) if r else 0,
            "peak_queue": int(r["peak_queue"]) if r else 0,
            "served": int(r["served"]) if r else 0,
            "avg_wait_s": _r(r["wait_sum"] / r["served"]) if r and r["served"] else None,
            "avg_visit_s": _r(w["avg_visit_s"]) if w else None,
            "abandoned": int(w["abandoned"] or 0) if w else 0,
        })
    return out


def zone_stats(store: Store, start: str, end: str, zone_names: dict[str, str] | None = None) -> list[dict[str, Any]]:
    rows = store.query(
        """SELECT zone, SUM(visits) visits, SUM(passes) passes, SUM(dwell_total) dwell_total,
                  MAX(dwell_max) dwell_max
           FROM zone_hours WHERE day BETWEEN ? AND ? GROUP BY zone ORDER BY visits DESC""", (start, end))
    names = zone_names or {}
    out = []
    for r in rows:
        visits, passes = int(r["visits"]), int(r["passes"])
        out.append({
            "zone": r["zone"],
            "name": names.get(r["zone"], r["zone"]),
            "stops": visits,
            "passes": passes,
            "stop_rate": _r(100.0 * visits / (visits + passes), 1) if visits + passes else None,
            "avg_dwell_s": _r(r["dwell_total"] / visits) if visits else None,
            "max_dwell_s": _r(r["dwell_max"]),
            "total_dwell_min": _r(r["dwell_total"] / 60.0),
        })
    return out


def zone_hourly(store: Store, day: str) -> dict[str, list[int]]:
    out: dict[str, list[int]] = defaultdict(lambda: [0] * 24)
    for r in store.query("SELECT hour_key, zone, visits FROM zone_hours WHERE day=?", (day,)):
        out[r["zone"]][int(r["hour_key"][11:13])] += int(r["visits"])
    return dict(out)


def queue_analysis(store: Store, day: str) -> dict[str, Any]:
    rows = store.query(
        """SELECT started_at, queue_wait_s, service_s, served, abandoned FROM visits
           WHERE day=? AND is_staff=0 AND queue_wait_s IS NOT NULL ORDER BY started_at""", (day,))
    waits = [r["queue_wait_s"] for r in rows if r["served"]]
    bins = [0, 30, 60, 120, 180, 300, 600, 10**9]
    labels = ["<30 sn", "30-60 sn", "1-2 dk", "2-3 dk", "3-5 dk", "5-10 dk", ">10 dk"]
    hist = [0] * (len(bins) - 1)
    for w in waits:
        for i in range(len(bins) - 1):
            if bins[i] <= w < bins[i + 1]:
                hist[i] += 1
                break
    services = [r["service_s"] for r in rows if r["served"] and r["service_s"] is not None]
    return {
        "day": day,
        "served": len(waits),
        "abandoned": sum(r["abandoned"] for r in rows),
        "avg_wait_s": _r(np.mean(waits)) if waits else None,
        "median_wait_s": _r(np.median(waits)) if waits else None,
        "p90_wait_s": _r(np.percentile(waits, 90)) if waits else None,
        "max_wait_s": _r(max(waits)) if waits else None,
        "avg_service_s": _r(np.mean(services)) if services else None,
        "histogram": [{"label": l, "count": c} for l, c in zip(labels, hist)],
        "by_hour": [{"hour": h["hour"], "avg_wait_s": h["avg_wait_s"], "queue_max": h["queue_max"]}
                    for h in hourly(store, day)],
    }


def visits(store: Store, day: str, limit: int = 500, include_staff: bool = True) -> list[dict[str, Any]]:
    sql = "SELECT * FROM visits WHERE day=?" + ("" if include_staff else " AND is_staff=0")
    rows = store.query(sql + " ORDER BY started_at DESC LIMIT ?", (day, limit))
    for r in rows:
        r["zones"] = json.loads(r["zones"] or "{}")
    return rows


def staff_summary(store: Store, day: str) -> dict[str, Any]:
    h = hourly(store, day)
    return {
        "day": day,
        "by_hour": [{"hour": x["hour"], "staff_avg": x["staff_avg"], "occupancy_avg": x["occupancy_avg"]} for x in h],
        "customers_per_staff": [
            {"hour": x["hour"], "ratio": _r(x["occupancy_avg"] / x["staff_avg"], 2) if x["staff_avg"] else None}
            for x in h],
    }
