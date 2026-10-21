"""Multi-person tracker for a fixed camera: ByteTrack + OC-SORT ideas, plain Python.

- Every track carries a constant-velocity Kalman filter on (cx, cy, w, h). The noise scales
  with the box height, as in ByteTrack, and the filter is time based (seconds, not frames),
  so it copes with any frame rate and with dropped frames. The four coordinates are
  independent, which lets each be a tiny 2-state filter — no matrix library needed.
- Three association stages, each solved optimally (Hungarian):
  1. high-confidence detections ↔ all live tracks, by IoU with the predicted box;
  2. low-confidence detections ↔ tracks still unmatched (partly hidden people);
  3. recovery: leftover detections ↔ lost tracks by where they were *last seen* (OC-SORT's
     observation-centric recovery) — while someone is hidden the prediction drifts, but a
     person who stood still reappears where they were.
- People who stand still are remembered longer (`max_lost_static_seconds`: a queue, a
  consultation at the counter) than people walking (`max_lost_seconds`).
- A box much shorter than the person last seen in full, with the same top, is someone whose
  legs are hidden behind a person or a stand nearer the camera: the full height is kept, so
  the foot point (zones, door) does not jump. A box cut by the image edge is not that — people
  leave the picture there — and a lost track predicted outside the picture ends at once.
- New tracks only from unmatched high-confidence detections; tentative ones die quickly.
"""
from __future__ import annotations

import itertools
import math
from collections import deque
from dataclasses import dataclass, field

from ..camera.base import Detection
from .geometry import iou

_ids = itertools.count(1)

# Kalman noise, relative to the box height (ByteTrack's values, per 1/30 s step)
STD_POS = 1 / 20
STD_VEL = 1 / 160 * 30  # per second
LOST_VEL_DECAY_S = 0.8  # a hidden person is assumed to slow down rather than keep walking
STILL_SPEED = 0.15  # foot speed below this many box heights per second = standing
UNMATCHED = 1e6
EDGE = 0.985  # a box reaching this far is cut by the image border


class _Kalman1D:
    """Position + velocity filter for one box coordinate."""

    __slots__ = ("p", "v", "pp", "pv", "vv")

    def __init__(self, z: float, h: float):
        self.p, self.v = z, 0.0
        self.pp, self.pv, self.vv = (2 * STD_POS * h) ** 2, 0.0, (10 * STD_VEL * h) ** 2

    def predict(self, dt: float, h: float, decay: float) -> None:
        k = dt * 30
        self.p += self.v * dt
        self.pp += dt * (2 * self.pv + dt * self.vv) + (STD_POS * h) ** 2 * k
        self.pv += dt * self.vv
        self.vv += (STD_VEL * h) ** 2 * k
        if decay < 1.0:
            self.v *= decay

    def update(self, z: float, r: float) -> None:
        s = self.pp + r
        k0, k1 = self.pp / s, self.pv / s
        y = z - self.p
        self.p += k0 * y
        self.v += k1 * y
        self.vv -= k1 * self.pv
        self.pv *= 1 - k0
        self.pp *= 1 - k0


class KalmanBox:
    def __init__(self, box):
        cx, cy, w, h = _cxcywh(box)
        self.f = [_Kalman1D(cx, h), _Kalman1D(cy, h), _Kalman1D(w, h), _Kalman1D(h, h)]

    @property
    def box(self) -> tuple[float, float, float, float]:
        cx, cy, w, h = (f.p for f in self.f)
        w, h = max(w, 0.004), max(h, 0.008)
        return (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)

    @property
    def velocity(self) -> tuple[float, float, float, float]:
        return tuple(f.v for f in self.f)  # type: ignore[return-value]

    def predict(self, dt: float, lost: bool) -> None:
        if dt <= 0:
            return
        h = max(self.f[3].p, 0.01)
        decay = math.exp(-dt / LOST_VEL_DECAY_S) if lost else 1.0
        for f in self.f:
            f.predict(dt, h, decay)

    def update(self, box, noise_scale: float = 1.0) -> None:
        z = _cxcywh(box)
        r = (STD_POS * max(self.f[3].p, 0.01)) ** 2 * noise_scale
        for f, v in zip(self.f, z):
            f.update(v, r)


def _cxcywh(b):
    return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2, b[2] - b[0], b[3] - b[1])


@dataclass
class Track:
    id: int
    box: tuple[float, float, float, float]
    score: float
    first_ts: float
    last_ts: float
    hits: int = 1
    confirmed: bool = False
    lost: bool = False
    meta: dict = field(default_factory=dict)  # analytics state lives here
    first_foot: tuple[float, float] | None = None
    obs_box: tuple[float, float, float, float] | None = None  # last detection matched to it
    kf: KalmanBox | None = field(default=None, repr=False)
    pred_ts: float | None = field(default=None, repr=False)  # time the filter was last advanced to
    full_h: float | None = field(default=None, repr=False)  # height when last seen whole
    attrs: dict | None = field(default=None, repr=False)  # estimates that came with the last detection
    motion: deque = field(default_factory=lambda: deque(maxlen=24), repr=False)  # (ts, foot) samples

    def __post_init__(self) -> None:
        if self.first_foot is None:
            self.first_foot = self.foot
        if self.obs_box is None:
            self.obs_box = self.box

    @property
    def foot(self) -> tuple[float, float]:
        x1, _, x2, y2 = self.box
        return ((x1 + x2) / 2, y2)

    @property
    def age(self) -> float:
        return self.last_ts - self.first_ts

    @property
    def velocity(self) -> tuple[float, float, float, float]:
        return self.kf.velocity if self.kf else (0.0, 0.0, 0.0, 0.0)

    @property
    def height(self) -> float:
        return max(self.box[3] - self.box[1], 1e-3)

    def speed(self) -> float:
        """Recent foot speed in box heights per second (0 if not enough history)."""
        if len(self.motion) < 2:
            return 0.0
        (t0, (x0, y0)), (t1, (x1, y1)) = self.motion[0], self.motion[-1]
        if t1 - t0 < 0.3:
            return 0.0
        return math.hypot(x1 - x0, y1 - y0) / (t1 - t0) / self.height

    def still(self) -> bool:
        return self.speed() < STILL_SPEED

    def predict(self, ts: float) -> tuple[float, float, float, float]:
        if self.kf is None:
            self.kf = KalmanBox(self.box)
        if self.pred_ts is not None:
            self.kf.predict(ts - self.pred_ts, self.lost)
        self.pred_ts = ts
        self.box = self.kf.box
        return self.box

    def update(self, det: Detection, ts: float, low: bool = False) -> None:
        if self.kf is None:
            self.kf = KalmanBox(self.box)
        z = det.box
        dh = z[3] - z[1]
        fh = self.full_h
        if fh and dh < 0.75 * fh and abs(z[1] - self.box[1]) < 0.15 * fh and z[3] < EDGE:
            # legs hidden behind something nearer the camera: same top, keep the full height
            z = (z[0], z[1], z[2], min(z[1] + fh, EDGE))
        else:
            self.full_h = dh if fh is None else 0.8 * fh + 0.2 * dh
        self.kf.update(z, noise_scale=4.0 if low else 1.0)
        self.box = self.kf.box
        self.obs_box = det.box
        self.attrs = getattr(det, "attrs", None)
        self.score = det.score
        self.last_ts = ts
        self.hits += 1
        self.lost = False
        self.motion.append((ts, self.foot))


def _hungarian(cost: list[list[float]]) -> list[tuple[int, int]]:
    """Optimal assignment (minimum total cost) for a small n×m matrix; returns (row, col) pairs."""
    n = len(cost)
    m = len(cost[0]) if n else 0
    if n == 0 or m == 0:
        return []
    transposed = n > m
    if transposed:
        cost = [list(col) for col in zip(*cost)]
        n, m = m, n
    inf = float("inf")
    u, v, p, way = [0.0] * (n + 1), [0.0] * (m + 1), [0] * (m + 1), [0] * (m + 1)
    for i in range(1, n + 1):
        p[0], j0 = i, 0
        minv, used = [inf] * (m + 1), [False] * (m + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], inf, 0
            row = cost[i0 - 1]
            for j in range(1, m + 1):
                if not used[j]:
                    cur = row[j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j], way[j] = cur, j0
                    if minv[j] < delta:
                        delta, j1 = minv[j], j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    pairs = [(p[j] - 1, j - 1) for j in range(1, m + 1) if p[j] != 0]
    return [(c, r) for r, c in pairs] if transposed else pairs


def _assign(tracks, dets, score_fn, min_score):
    """Match tracks to detections maximising score_fn (0..1); pairs below min_score are not allowed."""
    if not tracks or not dets:
        return [], list(range(len(tracks))), list(range(len(dets)))
    cost = [[UNMATCHED if (s := score_fn(t, d)) < min_score else 1.0 - s for d in dets] for t in tracks]
    matches = [(ti, di) for ti, di in _hungarian(cost) if cost[ti][di] < UNMATCHED]
    mt, md = {ti for ti, _ in matches}, {di for _, di in matches}
    return matches, [i for i in range(len(tracks)) if i not in mt], [i for i in range(len(dets)) if i not in md]


def _recovery_score(t: Track, d: Detection) -> float:
    """How well a detection continues a lost track, judged from where it was last seen."""
    ob = t.obs_box
    v = iou(ob, d.box)
    h = max(ob[3] - ob[1], 1e-3)
    dh = d.box[3] - d.box[1]
    if not 0.5 <= dh / h <= 1.6:
        return v
    # nearest of the foot points and the head points (a box cut at the bottom keeps its head)
    cx_o, cx_d = (ob[0] + ob[2]) / 2, (d.box[0] + d.box[2]) / 2
    dist = min(math.hypot(cx_o - cx_d, ob[3] - d.box[3]), math.hypot(cx_o - cx_d, ob[1] - d.box[1]))
    near = 1.0 - dist / (0.35 * h)
    return max(v, near)


class Tracker:
    def __init__(self, high_threshold=0.55, low_threshold=0.3, match_iou=0.25, min_hits=3, max_lost_seconds=2.0,
                 max_lost_static_seconds=6.0, recovery_score=0.3):
        self.high = high_threshold
        self.low = low_threshold
        self.match_iou = match_iou
        self.min_hits = min_hits
        self.max_lost = max_lost_seconds
        self.max_lost_static = max(max_lost_static_seconds, max_lost_seconds)
        self.recovery = recovery_score
        self.tracks: list[Track] = []

    @classmethod
    def from_config(cls, cfg: dict) -> "Tracker":
        return cls(**cfg)

    def update(self, detections: list[Detection], ts: float) -> tuple[list[Track], list[Track]]:
        """Feed one frame. Returns (active confirmed tracks, tracks that just ended)."""
        for t in self.tracks:
            t.predict(ts)
        high = [d for d in detections if d.score >= self.high]
        low = [d for d in detections if self.low <= d.score < self.high]
        by_iou = lambda t, d: iou(t.box, d.box)  # noqa: E731

        # 1: every live track ↔ confident detections
        m1, rest_t, rest_h = _assign(self.tracks, high, by_iou, self.match_iou)
        for ti, di in m1:
            self.tracks[ti].update(high[di], ts)

        # 2: still-unmatched confirmed tracks ↔ low-confidence (partly hidden) detections; a
        # tentative track must earn confirmation with confident boxes (keeps posters out)
        remaining = [self.tracks[i] for i in rest_t if self.tracks[i].confirmed]
        m2, rest_t2, rest_l = _assign(remaining, low, by_iou, max(self.match_iou, 0.3))
        for ti, di in m2:
            remaining[ti].update(low[di], ts, low=True)

        # 3: recovery of confirmed tracks from where they were last seen
        lost = [remaining[i] for i in rest_t2]
        left = [high[i] for i in rest_h] + [low[i] for i in rest_l]
        m3, _, rest_d = _assign(lost, left, _recovery_score, self.recovery)
        for ti, di in m3:
            lost[ti].update(left[di], ts, low=left[di].score < self.high)
        matched = {id(remaining[i]) for i, _ in m2} | {id(lost[i]) for i, _ in m3} | {id(self.tracks[i]) for i, _ in m1}
        for t in self.tracks:
            if id(t) not in matched:
                t.lost = True

        # new tracks only from confident detections nobody claimed
        for di in rest_d:
            d = left[di]
            if d.score >= self.high:
                t = Track(id=next(_ids), box=d.box, score=d.score, first_ts=ts, last_ts=ts,
                          kf=KalmanBox(d.box), pred_ts=ts)
                t.motion.append((ts, t.foot))
                self.tracks.append(t)

        for t in self.tracks:
            if not t.confirmed and t.hits >= self.min_hits:
                t.confirmed = True

        ended, alive = [], []
        for t in self.tracks:
            gone = ts - t.last_ts
            if not t.confirmed:
                if gone <= 0.5:  # one or two spurious boxes are not people
                    alive.append(t)
                continue
            x1, y1, x2, y2 = t.box
            if t.lost and (y1 >= EDGE or y2 <= 1 - EDGE or x1 >= EDGE or x2 <= 1 - EDGE):
                ended.append(t)  # walked out of the picture
                continue
            limit = self.max_lost_static if t.still() else self.max_lost
            (ended if gone > limit else alive).append(t)
        self.tracks = alive
        active = [t for t in alive if t.confirmed and not t.lost]
        return active, ended

    def forget(self, track: Track) -> None:
        """Drop a lost track whose person was found again as another track."""
        self.tracks = [t for t in self.tracks if t is not track]

    def flush(self) -> list[Track]:
        """End all tracks (shutdown / day rollover)."""
        ended = [t for t in self.tracks if t.confirmed]
        self.tracks = []
        return ended
