"""Analytics engine: turns tracked people into pharmacy metrics.

Per frame it updates, for every confirmed track:
  * staff classification (white coat + staff-zone time)
  * door line crossings  -> entry / exit events
  * zone membership      -> shelf dwell, queue wait, service time
  * heatmap accumulation (customers only)
and keeps live state (occupancy, queue length, staff on duty) for the API.

When a track ends, an anonymous *visit* record is written. Minute metrics,
hourly zone statistics and hourly heatmaps are flushed on time boundaries.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .. import timeutil
from ..camera.base import Frame
from ..config import Config, Zone
from ..vision.geometry import point_in_polygon, projection_t, signed_side
from ..vision.staff import StaffClassifier
from ..vision.tracker import Track, Tracker

log = logging.getLogger(__name__)

ZONE_EXIT_GRACE = 0.8  # seconds a foot point may leave a zone without ending the stay
ENTRY_DECISION_DELAY = 4.0  # seconds to wait for staff classification before counting a crossing
DOOR_MARGIN = 0.07  # normalized distance inside the door line treated as "at the door"


@dataclass
class ZoneHourStats:
    visits: int = 0  # stays longer than min dwell ("durdu")
    passes: int = 0  # touched the zone but did not stop ("geçti")
    dwell_total: float = 0.0
    dwell_max: float = 0.0


@dataclass
class MinuteAcc:
    start: float
    entries: int = 0
    exits: int = 0
    staff_entries: int = 0
    samples: int = 0
    occ_sum: float = 0.0
    occ_max: int = 0
    vis_sum: float = 0.0
    queue_sum: float = 0.0
    queue_max: int = 0
    staff_sum: float = 0.0
    served: int = 0
    wait_sum: float = 0.0


@dataclass
class LiveState:
    ts: float = 0.0
    occupancy: int = 0
    visible_customers: int = 0
    staff_on_duty: int = 0
    queue_length: int = 0
    queue_waits: list[float] = field(default_factory=list)
    entries_today: int = 0
    exits_today: int = 0
    staff_entries_today: int = 0
    tracks: list[dict[str, Any]] = field(default_factory=list)
    fps: float = 0.0
    camera_ok: bool = False
    zone_counts: dict[str, int] = field(default_factory=dict)


class AnalyticsEngine:
    def __init__(self, cfg: Config, store=None):
        self.cfg = cfg
        self.store = store
        a = cfg["analytics"]
        self.min_dwell = float(a["min_zone_dwell_seconds"])
        self.min_queue = float(a["min_queue_seconds"])
        self.hyst = float(a["door_hysteresis"])
        self.min_visit = float(a["min_visit_seconds"])
        self.empty_reset = float(a["empty_reset_seconds"])
        self.occ_mode = a["occupancy_mode"]
        self.grid_w, self.grid_h = a["heatmap_grid"]

        self.tracker = Tracker.from_config(cfg["tracker"])
        self.staff = StaffClassifier.from_config(cfg["staff"])
        self.lock = threading.RLock()
        self.live = LiveState()
        self._fps_times: deque[float] = deque(maxlen=60)
        self._last_ts: float | None = None
        self._day: str | None = None
        self._counter = 0
        self._empty_since: float | None = None
        self._pending_crossings: list[tuple[float, Track, str, str]] = []
        self._minute: MinuteAcc | None = None
        self._hour_key: str | None = None
        self._zone_hour: dict[str, ZoneHourStats] = defaultdict(ZoneHourStats)
        self._heat_hour = np.zeros((self.grid_h, self.grid_w), dtype=np.float32)
        self.last_frame: np.ndarray | None = None
        self.last_frame_ts: float = 0.0
        self._restore_today()

    # ------------------------------------------------------------------ setup
    def reload_zones(self) -> None:
        """Called after zones are edited from the dashboard."""
        with self.lock:
            for t in self.tracker.tracks:
                t.meta.pop("zones", None)
                t.meta.pop("doors", None)

    def _restore_today(self) -> None:
        """After a restart, continue today's counters from the database."""
        if not self.store:
            return
        day = timeutil.today()
        s = self.store.day_counts(day)
        self._day = day
        self.live.entries_today = s["entries"]
        self.live.exits_today = s["exits"]
        self.live.staff_entries_today = s["staff_entries"]
        self._counter = max(0, s["entries"] - s["exits"])

    # --------------------------------------------------------------- per frame
    def process(self, frame: Frame) -> LiveState:
        with self.lock:
            ts = frame.ts
            dt = 0.0 if self._last_ts is None else min(max(ts - self._last_ts, 0.0), 1.0)
            self._last_ts = ts
            self._fps_times.append(ts)
            self._roll_time(ts)

            active, ended = self.tracker.update(frame.detections, ts)
            for t in active:
                self._update_track(t, frame, ts, dt)
            for t in ended:
                self._finish_track(t, ts)
            self._resolve_crossings(ts, force=False)
            self._update_live(active, ts, dt)
            if frame.image is not None:
                self.last_frame = frame.image
                self.last_frame_ts = ts
            return self.live

    def _update_track(self, t: Track, frame: Frame, ts: float, dt: float) -> None:
        m = t.meta
        m["seen_s"] = m.get("seen_s", 0.0) + dt
        self.staff.observe(t, frame.image)
        foot = t.foot
        if any(point_in_polygon(foot, z.polygon) for z in self.cfg.zones_of("staff")):
            m["staff_zone_s"] = m.get("staff_zone_s", 0.0) + dt
        is_staff = self.staff.is_staff(t)
        m["is_staff"] = is_staff

        for door in self.cfg.zones_of("entrance"):
            self._door_step(t, door, ts)

        zones = m.setdefault("zones", {})
        for z in self.cfg.zones:
            if z.type in ("entrance", "staff"):
                continue
            inside = point_in_polygon(foot, z.polygon)
            st = zones.get(z.id)
            if inside:
                if st is None or st.get("closed"):
                    st = zones[z.id] = {"enter": ts, "last_in": ts, "closed": False,
                                        "total": st["total"] if st else 0.0, "stays": st["stays"] if st else 0}
                st["last_in"] = ts
                self._zone_live_hook(t, z, st, ts, is_staff)
            elif st is not None and not st["closed"] and ts - st["last_in"] > ZONE_EXIT_GRACE:
                self._close_zone_stay(t, z, st, is_staff)

        if not is_staff:
            gx = min(self.grid_w - 1, max(0, int(foot[0] * self.grid_w)))
            gy = min(self.grid_h - 1, max(0, int(foot[1] * self.grid_h)))
            self._heat_hour[gy, gx] += dt

    # ------------------------------------------------------------------- doors
    @staticmethod
    def _door_metrics(p: tuple[float, float], door: Zone) -> tuple[float, float]:
        """(signed distance with inside positive, position along the door segment)."""
        a, b = door.line
        sign = 1.0 if signed_side(door.inside_point, a, b) >= 0 else -1.0
        return signed_side(p, a, b) * sign, projection_t(p, a, b)

    def _door_step(self, t: Track, door: Zone, ts: float) -> None:
        d, along = self._door_metrics(t.foot, door)
        doors = t.meta.setdefault("doors", {})
        prev = doors.get(door.id)
        if prev is None:
            # Tracks are confirmed a few frames after birth; judge the side by
            # where the person was first seen.
            d0, along0 = self._door_metrics(t.first_foot, door)
            if d0 < -self.hyst:
                prev = doors[door.id] = "out"
            elif d0 < DOOR_MARGIN and -0.25 <= along0 <= 1.25:
                # Born right at the door: either a fast walker coming in, or a
                # leaving customer whose track restarted. Decide by direction.
                prev = doors[door.id] = "door"
        if prev == "door":
            if d > DOOR_MARGIN:
                doors[door.id] = "in"
                self._pending_crossings.append((ts, t, "entry", door.id))
            elif d < -self.hyst:
                doors[door.id] = "out"  # fragment of someone leaving; their exit was already counted
            return
        if d > self.hyst:
            state = "in"
        elif d < -self.hyst:
            state = "out"
        else:
            return  # inside the hysteresis band: keep previous state
        if prev is None:
            doors[door.id] = state
            return
        if prev != state and -0.25 <= along <= 1.25:
            kind = "entry" if state == "in" else "exit"
            self._pending_crossings.append((ts, t, kind, door.id))
        doors[door.id] = state

    def _door_lost_exit(self, t: Track) -> None:
        """A customer lost right inside the door (stepped out between frames) has left."""
        for door in self.cfg.zones_of("entrance"):
            if t.meta.get("doors", {}).get(door.id) != "in":
                continue
            d, along = self._door_metrics(t.foot, door)
            if d < DOOR_MARGIN and -0.25 <= along <= 1.25:
                t.meta["doors"][door.id] = "out"
                self._emit_crossing(t.last_ts, t, "exit", door.id)

    def _resolve_crossings(self, ts: float, force: bool) -> None:
        keep = []
        for cts, t, kind, door_id in self._pending_crossings:
            mature = t.meta.get("coat_n", 0) >= self.staff.min_samples or ts - cts >= ENTRY_DECISION_DELAY
            if force or mature or not self.staff.enabled:
                self._emit_crossing(cts, t, kind, door_id)
            else:
                keep.append((cts, t, kind, door_id))
        self._pending_crossings = keep

    def _emit_crossing(self, ts: float, t: Track, kind: str, door_id: str) -> None:
        is_staff = self.staff.is_staff(t)
        t.meta["entered" if kind == "entry" else "exited"] = True
        if self._day != timeutil.day_str(ts):
            self._roll_day(ts)
        acc = self._minute_acc(ts)
        if is_staff:
            if kind == "entry":
                acc.staff_entries += 1
                self.live.staff_entries_today += 1
        else:
            if kind == "entry":
                acc.entries += 1
                self.live.entries_today += 1
                self._counter += 1
            else:
                acc.exits += 1
                self.live.exits_today += 1
                self._counter = max(0, self._counter - 1)
        if self.store:
            self.store.add_event(ts, kind, self._uid(t), door_id, None, {"staff": is_staff})

    # ------------------------------------------------------------------- zones
    def _zone_live_hook(self, t: Track, z: Zone, st: dict, ts: float, is_staff: bool) -> None:
        if is_staff:
            return
        m = t.meta
        if z.type == "queue" and "queue_start" not in m and ts - st["enter"] >= self.min_queue:
            m["queue_start"] = st["enter"]
            if self.store:
                self.store.add_event(ts, "queue_join", self._uid(t), z.id, None, None)
        if z.type == "service" and "service_start" not in m and ts - st["enter"] >= 1.0:
            m["service_start"] = st["enter"]
            wait = max(0.0, st["enter"] - m["queue_start"]) if "queue_start" in m else 0.0
            m["queue_wait"] = wait
            m["served"] = True
            acc = self._minute_acc(ts)
            acc.served += 1
            acc.wait_sum += wait
            if self.store:
                self.store.add_event(ts, "served", self._uid(t), z.id, wait, None)

    def _close_zone_stay(self, t: Track, z: Zone, st: dict, is_staff: bool) -> None:
        st["closed"] = True
        dur = max(0.0, st["last_in"] - st["enter"])
        st["total"] = st.get("total", 0.0) + dur
        if is_staff:
            return
        if z.type == "service":
            t.meta["service_s"] = t.meta.get("service_s", 0.0) + dur
        if z.type != "shelf":
            return
        zs = self._zone_hour[z.id]
        if dur >= self.min_dwell:
            st["stays"] = st.get("stays", 0) + 1
            zs.visits += 1
            zs.dwell_total += dur
            zs.dwell_max = max(zs.dwell_max, dur)
            if self.store:
                self.store.add_event(st["last_in"], "shelf_stop", self._uid(t), z.id, dur, None)
        else:
            zs.passes += 1

    # ------------------------------------------------------------- track end
    @staticmethod
    def _uid(t: Track) -> str:
        return f"{int(t.first_ts)}-{t.id}"

    def _finish_track(self, t: Track, ts: float) -> None:
        # flush crossings of this track before deciding anything else
        mine = [c for c in self._pending_crossings if c[1] is t]
        self._pending_crossings = [c for c in self._pending_crossings if c[1] is not t]
        for c in mine:
            self._emit_crossing(*c)
        if self.cfg.zones_of("entrance"):
            self._door_lost_exit(t)
        is_staff = self.staff.is_staff(t)
        zones = t.meta.get("zones", {})
        zmap = {z.id: z for z in self.cfg.zones}
        for zid, st in zones.items():
            if not st.get("closed") and zid in zmap:
                self._close_zone_stay(t, zmap[zid], st, is_staff)
        m = t.meta
        abandoned = False
        if not is_staff and "queue_start" in m and not m.get("served"):
            abandoned = True
            m["queue_wait"] = max(0.0, t.last_ts - m["queue_start"])
            if self.store:
                self.store.add_event(t.last_ts, "queue_abandon", self._uid(t), None, m["queue_wait"], None)
        if t.age < self.min_visit or not self.store:
            return
        self.store.add_visit({
            "uid": self._uid(t),
            "started_at": t.first_ts,
            "ended_at": t.last_ts,
            "duration_s": round(t.age, 2),
            "is_staff": int(is_staff),
            "coat_score": round(m.get("coat_score", 0.0), 3),
            "entered": int(bool(m.get("entered"))),
            "exited": int(bool(m.get("exited"))),
            "queue_wait_s": round(m["queue_wait"], 2) if "queue_wait" in m else None,
            "service_s": round(m.get("service_s", 0.0), 2) if m.get("served") else None,
            "served": int(bool(m.get("served"))),
            "abandoned": int(abandoned),
            "zones": json.dumps({zid: round(st.get("total", 0.0), 1) for zid, st in zones.items()
                                 if st.get("total", 0.0) >= 1.0}),
        })

    # ------------------------------------------------------------- live state
    def _update_live(self, active: list[Track], ts: float, dt: float) -> None:
        customers = [t for t in active if not t.meta.get("is_staff")]
        staff = [t for t in active if t.meta.get("is_staff")]
        visible = len(customers)
        if visible == 0:
            self._empty_since = self._empty_since or ts
            if ts - self._empty_since >= self.empty_reset:
                self._counter = 0  # drift correction: store is visibly empty
        else:
            self._empty_since = None

        if self.occ_mode == "visible":
            occ = visible
        elif self.occ_mode == "counter":
            occ = self._counter
        else:
            occ = max(visible, self._counter)

        queue_ids = {z.id for z in self.cfg.zones_of("queue")}
        waits, zone_counts = [], defaultdict(int)
        for t in customers:
            for zid, st in t.meta.get("zones", {}).items():
                if not st.get("closed"):
                    zone_counts[zid] += 1
            if "queue_start" in t.meta and not t.meta.get("served"):
                in_q = any(not t.meta["zones"].get(q, {"closed": True})["closed"] for q in queue_ids)
                if in_q:
                    waits.append(round(ts - t.meta["queue_start"], 1))

        lv = self.live
        lv.ts = ts
        lv.occupancy = occ
        lv.visible_customers = visible
        lv.staff_on_duty = len(staff)
        lv.queue_length = len(waits)
        lv.queue_waits = sorted(waits, reverse=True)
        lv.zone_counts = dict(zone_counts)
        lv.camera_ok = True
        if len(self._fps_times) > 1:
            span = self._fps_times[-1] - self._fps_times[0]
            lv.fps = round((len(self._fps_times) - 1) / span, 1) if span > 0 else 0.0
        lv.tracks = [{
            "id": t.id,
            "box": [round(v, 4) for v in t.box],
            "staff": bool(t.meta.get("is_staff")),
            "coat": round(t.meta.get("coat_score", 0.0), 2),
            "seen_s": round(t.meta.get("seen_s", 0.0), 1),
        } for t in active]

        acc = self._minute_acc(ts)
        acc.samples += 1
        acc.occ_sum += occ
        acc.occ_max = max(acc.occ_max, occ)
        acc.vis_sum += visible
        acc.queue_sum += len(waits)
        acc.queue_max = max(acc.queue_max, len(waits))
        acc.staff_sum += len(staff)

    # ------------------------------------------------------- time boundaries
    def _minute_acc(self, ts: float) -> MinuteAcc:
        ms = timeutil.minute_start(ts)
        if self._minute is None:
            self._minute = MinuteAcc(start=ms)
        return self._minute

    def _roll_time(self, ts: float) -> None:
        ms = timeutil.minute_start(ts)
        if self._minute is not None and ms > self._minute.start:
            self._flush_minute()
            self._minute = MinuteAcc(start=ms)
        hk = timeutil.hour_key(ts)
        if self._hour_key is None:
            self._hour_key = hk
        elif hk != self._hour_key:
            self._flush_hour()
            self._hour_key = hk
        if self._day is None:
            self._day = timeutil.day_str(ts)
        elif timeutil.day_str(ts) != self._day:
            self._roll_day(ts)

    def _roll_day(self, ts: float) -> None:
        self._day = timeutil.day_str(ts)
        self._counter = 0
        self.live.entries_today = 0
        self.live.exits_today = 0
        self.live.staff_entries_today = 0

    def _flush_minute(self) -> None:
        a = self._minute
        if a is None or not self.store:
            return
        n = max(a.samples, 1)
        self.store.add_minute({
            "ts": a.start,
            "entries": a.entries,
            "exits": a.exits,
            "staff_entries": a.staff_entries,
            "occupancy_avg": round(a.occ_sum / n, 3),
            "occupancy_max": a.occ_max,
            "visible_avg": round(a.vis_sum / n, 3),
            "queue_avg": round(a.queue_sum / n, 3),
            "queue_max": a.queue_max,
            "staff_avg": round(a.staff_sum / n, 3),
            "served": a.served,
            "wait_sum": round(a.wait_sum, 2),
            "samples": a.samples,
        })

    def _flush_hour(self) -> None:
        if not self.store or self._hour_key is None:
            return
        if self._zone_hour:
            self.store.add_zone_hour(self._hour_key, {k: vars(v) for k, v in self._zone_hour.items()})
        if self._heat_hour.any():
            self.store.add_heatmap(self._hour_key, self._heat_hour)
        self._zone_hour = defaultdict(ZoneHourStats)
        self._heat_hour = np.zeros((self.grid_h, self.grid_w), dtype=np.float32)

    def flush(self) -> None:
        """Persist everything (shutdown)."""
        with self.lock:
            ts = self._last_ts or time.time()
            for t in self.tracker.flush():
                self._finish_track(t, ts)
            self._resolve_crossings(ts, force=True)
            self._flush_minute()
            self._minute = None
            self._flush_hour()

    def current_heatmap(self) -> np.ndarray:
        with self.lock:
            return self._heat_hour.copy()

    def current_zone_hour(self) -> dict[str, dict]:
        with self.lock:
            return {k: vars(v).copy() for k, v in self._zone_hour.items()}

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            d = vars(self.live).copy()
            d["counter"] = self._counter
            d["day"] = self._day
            return d
