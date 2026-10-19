"""Visitor forecasting ("kişi tahminleme").

Seasonal profile model: expected entries for (weekday, hour) is an
exponentially weighted mean of the same slot in previous weeks, scaled by a
recent-trend factor (last 14 days vs. the long-run average). Simple,
explainable to a pharmacist, and robust with only a few weeks of data.
Falls back to the all-days hourly profile when a weekday has no history.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

import numpy as np

from .. import timeutil
from ..storage.db import Store

DECAY = 0.75  # weight of each older week relative to the next newer one


def _history(store: Store, lookback_days: int, until: str) -> dict[str, list[int]]:
    start = timeutil.shift_day(until, -lookback_days)
    rows = store.query(
        "SELECT day, hour, SUM(entries) e FROM minutes WHERE day BETWEEN ? AND ? GROUP BY day, hour",
        (start, until))
    hist: dict[str, list[int]] = defaultdict(lambda: [0] * 24)
    for r in rows:
        hist[r["day"]][r["hour"]] = int(r["e"])
    return dict(hist)


def forecast(store: Store, days: int = 7, start: str | None = None, lookback_days: int = 84) -> dict[str, Any]:
    start = start or timeutil.today()
    last = timeutil.shift_day(start, -1)
    hist = _history(store, lookback_days, last)
    if not hist:
        return {"start": start, "days": [], "history_days": 0, "method": "seasonal-ewm"}

    by_wd: dict[int, list[tuple[str, list[int]]]] = defaultdict(list)
    for d, hours in hist.items():
        by_wd[date.fromisoformat(d).weekday()].append((d, hours))
    all_profile = np.mean([h for h in hist.values()], axis=0)

    totals = {d: sum(h) for d, h in hist.items()}
    recent = [totals[d] for d in sorted(totals)[-14:]]
    long_run = np.mean(list(totals.values())) or 1.0
    trend = float(np.clip((np.mean(recent) if recent else long_run) / long_run, 0.7, 1.3))

    out = []
    for i in range(days):
        d = timeutil.shift_day(start, i)
        wd = date.fromisoformat(d).weekday()
        samples = sorted(by_wd.get(wd, []), key=lambda x: x[0], reverse=True)
        if samples:
            arr = np.array([h for _, h in samples], dtype=float)
            w = np.array([DECAY ** k for k in range(len(samples))])
            mean = (arr * w[:, None]).sum(0) / w.sum()
            std = arr.std(0) if len(samples) > 1 else mean * 0.3
        elif len(hist) >= 14:
            # two weeks of history and never open on this weekday: closed (e.g. Sunday)
            mean = np.zeros(24)
            std = np.zeros(24)
        else:
            mean = all_profile.astype(float)
            std = mean * 0.4
        mean = mean * trend
        hours = [{
            "hour": h,
            "expected": round(float(mean[h]), 1),
            "low": round(max(0.0, float(mean[h] - 1.28 * std[h])), 1),
            "high": round(float(mean[h] + 1.28 * std[h]), 1),
        } for h in range(24)]
        total = float(mean.sum())
        peak = int(np.argmax(mean)) if total > 0 else None
        out.append({
            "day": d,
            "weekday": timeutil.WEEKDAYS_TR[wd],
            "expected_total": round(total),
            "low_total": round(max(0.0, total - 1.28 * float(np.sqrt((std ** 2).sum())))),
            "high_total": round(total + 1.28 * float(np.sqrt((std ** 2).sum()))),
            "peak_hour": peak,
            "samples": len(samples),
            "hours": hours,
        })
    return {"start": start, "days": out, "history_days": len(hist), "trend": round(trend, 3),
            "method": "seasonal-ewm"}


def staffing_hint(expected_per_hour: float, service_s: float = 90.0, target_util: float = 0.75) -> int:
    """Rough number of counter staff so utilisation stays under the target."""
    load = expected_per_hour * service_s / 3600.0
    return max(1, int(np.ceil(load / target_util)))
