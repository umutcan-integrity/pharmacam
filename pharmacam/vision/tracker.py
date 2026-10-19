"""ByteTrack-lite multi-person tracker.

Two-stage association (high-confidence detections first, then low-confidence
ones against the remaining tracks), constant-velocity prediction and
time-based track expiry. Pure numpy/python: a pharmacy scene rarely has more
than ~20 people, so greedy IoU matching is plenty fast on a Pi 5.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from ..camera.base import Detection
from .geometry import iou

_ids = itertools.count(1)


@dataclass
class Track:
    id: int
    box: tuple[float, float, float, float]
    score: float
    first_ts: float
    last_ts: float
    hits: int = 1
    velocity: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    confirmed: bool = False
    lost: bool = False
    meta: dict = field(default_factory=dict)  # analytics state lives here
    first_foot: tuple[float, float] | None = None

    def __post_init__(self) -> None:
        if self.first_foot is None:
            self.first_foot = self.foot

    @property
    def foot(self) -> tuple[float, float]:
        x1, _, x2, y2 = self.box
        return ((x1 + x2) / 2, y2)

    @property
    def age(self) -> float:
        return self.last_ts - self.first_ts

    def predict(self, ts: float) -> tuple[float, float, float, float]:
        dt = max(0.0, ts - self.last_ts)
        dt = min(dt, 1.0)  # don't extrapolate wildly while lost
        return tuple(b + v * dt for b, v in zip(self.box, self.velocity))  # type: ignore[return-value]

    def update(self, det: Detection, ts: float) -> None:
        dt = ts - self.last_ts
        if dt > 1e-3:
            inst = tuple((n - o) / dt for n, o in zip(det.box, self.box))
            a = 0.4  # velocity smoothing
            self.velocity = tuple(a * i + (1 - a) * v for i, v in zip(inst, self.velocity))  # type: ignore[assignment]
        # light box smoothing keeps foot points stable for zone tests
        self.box = tuple(0.7 * n + 0.3 * o for n, o in zip(det.box, self.box))  # type: ignore[assignment]
        self.score = det.score
        self.last_ts = ts
        self.hits += 1
        self.lost = False


class Tracker:
    def __init__(self, high_threshold=0.55, low_threshold=0.3, match_iou=0.25, min_hits=3, max_lost_seconds=2.0):
        self.high = high_threshold
        self.low = low_threshold
        self.match_iou = match_iou
        self.min_hits = min_hits
        self.max_lost = max_lost_seconds
        self.tracks: list[Track] = []

    @classmethod
    def from_config(cls, cfg: dict) -> "Tracker":
        return cls(**cfg)

    @staticmethod
    def _greedy(tracks: list[Track], dets: list[Detection], ts: float, thr: float):
        pairs = []
        for ti, t in enumerate(tracks):
            pb = t.predict(ts)
            for di, d in enumerate(dets):
                v = iou(pb, d.box)
                if v >= thr:
                    pairs.append((v, ti, di))
        pairs.sort(reverse=True)
        used_t, used_d, matches = set(), set(), []
        for _, ti, di in pairs:
            if ti in used_t or di in used_d:
                continue
            used_t.add(ti)
            used_d.add(di)
            matches.append((ti, di))
        return matches, [i for i in range(len(tracks)) if i not in used_t], [i for i in range(len(dets)) if i not in used_d]

    def update(self, detections: list[Detection], ts: float) -> tuple[list[Track], list[Track]]:
        """Feed one frame. Returns (active confirmed tracks, tracks that just ended)."""
        high = [d for d in detections if d.score >= self.high]
        low = [d for d in detections if self.low <= d.score < self.high]

        # Stage 1: all tracks vs high-confidence detections
        m1, rest_t, rest_d = self._greedy(self.tracks, high, ts, self.match_iou)
        for ti, di in m1:
            self.tracks[ti].update(high[di], ts)

        # Stage 2: remaining tracks vs low-confidence detections (occlusion recovery)
        remaining = [self.tracks[i] for i in rest_t]
        m2, rest_t2, _ = self._greedy(remaining, low, ts, self.match_iou)
        for ti, di in m2:
            remaining[ti].update(low[di], ts)
        for i in rest_t2:
            remaining[i].lost = True

        # New tracks only from unmatched high-confidence detections
        for di in rest_d:
            d = high[di]
            self.tracks.append(Track(id=next(_ids), box=d.box, score=d.score, first_ts=ts, last_ts=ts))

        for t in self.tracks:
            if not t.confirmed and t.hits >= self.min_hits:
                t.confirmed = True

        ended, alive = [], []
        for t in self.tracks:
            expired = ts - t.last_ts > self.max_lost
            # tentative tracks die fast: one or two spurious boxes are not people
            if not t.confirmed and ts - t.last_ts > 0.5:
                continue
            if expired:
                if t.confirmed:
                    ended.append(t)
            else:
                alive.append(t)
        self.tracks = alive
        active = [t for t in alive if t.confirmed and not t.lost]
        return active, ended

    def flush(self) -> list[Track]:
        """End all tracks (shutdown / day rollover)."""
        ended = [t for t in self.tracks if t.confirmed]
        self.tracks = []
        return ended
