"""Synthetic pharmacy simulator.

Renders a top-down-ish pharmacy (door, shelves, counter) with animated
customers and white-coated staff, and emits noisy person detections like
the IMX500 would. Used for development without hardware, automated tests,
demo data and documentation screenshots.

The scene matches config/pharmacam.simulator.yaml.
"""
from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field
from typing import Iterator

import numpy as np

from .. import timeutil
from .base import Detection, Frame

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

DOOR_OUT = (0.18, 0.995)
DOOR_IN = (0.18, 0.84)
SERVICE_SPOTS = [(0.47, 0.43), (0.63, 0.43)]
QUEUE_HEAD = (0.55, 0.53)
QUEUE_STEP = 0.065
STAFF_HOME = [(0.47, 0.27), (0.63, 0.27), (0.70, 0.24)]
SHELVES = {
    "derma": [(0.08, 0.40), (0.10, 0.55), (0.09, 0.68)],
    "vitamin": [(0.88, 0.40), (0.90, 0.52), (0.87, 0.62)],
    "bebek": [(0.82, 0.80), (0.90, 0.83)],
    "otc": [(0.27, 0.38), (0.31, 0.42)],
}
CUSTOMER_COLORS = [(52, 101, 164), (204, 0, 0), (78, 154, 6), (117, 80, 123), (193, 125, 17),
                   (46, 52, 54), (143, 89, 2), (32, 74, 135), (92, 53, 102), (164, 0, 0), (60, 60, 60)]

# customers per minute by hour (typical neighbourhood pharmacy)
HOURLY_RATE = {8: 0.35, 9: 0.7, 10: 1.1, 11: 1.3, 12: 1.0, 13: 0.8, 14: 0.9, 15: 1.0, 16: 1.1,
               17: 1.4, 18: 1.5, 19: 1.2, 20: 0.7, 21: 0.4}


@dataclass
class Agent:
    id: int
    staff: bool
    pos: list[float]
    color: tuple[int, int, int]
    plan: list[tuple] = field(default_factory=list)  # ("go", x, y) | ("wait", seconds) | ("queue",) ...
    speed: float = 0.11
    wait_until: float = 0.0
    state: str = "walk"
    queue_since: float | None = None
    patience: float = 1e9
    served_by: int | None = None
    done: bool = False
    spot: int | None = None


class PharmacySim:
    def __init__(self, seed: int = 42, arrival_scale: float = 1.0, n_staff: int = 2, hour_override: int | None = None):
        self.rng = random.Random(seed)
        self.arrival_scale = arrival_scale
        self.hour_override = hour_override
        self.agents: list[Agent] = []
        self.queue: list[Agent] = []
        self.spots: list[Agent | None] = [None] * len(SERVICE_SPOTS)
        self._next_id = 1
        for i in range(n_staff):
            self._spawn_staff(i)

    def _new_id(self) -> int:
        self._next_id += 1
        return self._next_id

    def _spawn_staff(self, i: int) -> None:
        x, y = STAFF_HOME[i % len(STAFF_HOME)]
        self.agents.append(Agent(self._new_id(), True, [x, y], (245, 245, 245), speed=0.08))

    def _spawn_customer(self) -> None:
        r = self.rng
        a = Agent(self._new_id(), False, [DOOR_OUT[0] + r.uniform(-0.06, 0.06), DOOR_OUT[1]],
                  r.choice(CUSTOMER_COLORS), speed=r.uniform(0.09, 0.15))
        plan: list[tuple] = [("go", DOOR_IN[0] + r.uniform(-0.04, 0.04), DOOR_IN[1])]
        shelves = list(SHELVES)
        r.shuffle(shelves)
        n_stop = r.choices([0, 1, 2, 3], weights=[0.35, 0.4, 0.2, 0.05])[0]
        for s in shelves[:n_stop]:
            x, y = r.choice(SHELVES[s])
            plan += [("go", x + r.uniform(-0.02, 0.02), y + r.uniform(-0.02, 0.02)), ("wait", r.uniform(4, 40))]
        if r.random() < 0.25 and n_stop < 3:  # walk past a shelf without stopping
            x, y = r.choice(SHELVES[shelves[-1]])
            plan.append(("go", x, y))
        if r.random() < 0.88:
            plan.append(("queue",))
            a.patience = r.uniform(70, 400) if r.random() < 0.15 else 1e9
        plan += [("go", DOOR_IN[0], DOOR_IN[1] + 0.03), ("go", DOOR_OUT[0], DOOR_OUT[1] + 0.02), ("leave",)]
        a.plan = plan
        self.agents.append(a)

    # ------------------------------------------------------------ dynamics
    def step(self, ts: float, dt: float) -> None:
        hour = self.hour_override if self.hour_override is not None else timeutil.local(ts).hour
        rate = HOURLY_RATE.get(hour, 0.0) * self.arrival_scale / 60.0
        if self.rng.random() < rate * dt:
            self._spawn_customer()

        # free service spots pull the queue head
        for i, occ in enumerate(self.spots):
            if occ is None and self.queue:
                a = self.queue.pop(0)
                self.spots[i] = a
                a.spot = i
                a.state = "to_service"

        for a in self.agents:
            if a.staff:
                self._step_staff(a, ts, dt)
            else:
                self._step_customer(a, ts, dt)
        self.agents = [a for a in self.agents if not a.done]

    def _move(self, a: Agent, target: tuple[float, float], dt: float) -> bool:
        dx, dy = target[0] - a.pos[0], target[1] - a.pos[1]
        dist = math.hypot(dx, dy)
        # perspective: people far from the camera (small y) move fewer pixels
        speed = a.speed * (0.55 + 0.6 * a.pos[1])
        if dist < 0.01:
            return True
        step = min(dist, speed * dt)
        a.pos[0] += dx / dist * step + self.rng.gauss(0, 0.0008)
        a.pos[1] += dy / dist * step + self.rng.gauss(0, 0.0008)
        return False

    def _step_customer(self, a: Agent, ts: float, dt: float) -> None:
        if a.state == "queued":
            idx = self.queue.index(a)
            tgt = (QUEUE_HEAD[0] + 0.01 * math.sin(idx), QUEUE_HEAD[1] + idx * QUEUE_STEP)
            self._move(a, tgt, dt)
            if ts - (a.queue_since or ts) > a.patience:  # gives up
                self.queue.remove(a)
                a.state = "walk"
                a.plan = a.plan[1:]
            return
        if a.state == "to_service":
            if self._move(a, SERVICE_SPOTS[a.spot], dt):
                a.state = "service"
                a.wait_until = ts + self.rng.uniform(25, 110)
            return
        if a.state == "service":
            if ts >= a.wait_until:
                self.spots[a.spot] = None
                a.spot = None
                a.state = "walk"
                a.plan = a.plan[1:]
            return
        if a.state == "wait":
            a.pos[0] += self.rng.gauss(0, 0.0006)
            if ts >= a.wait_until:
                a.state = "walk"
                a.plan = a.plan[1:]
            return
        if not a.plan:
            a.done = True
            return
        step = a.plan[0]
        if step[0] == "go":
            if self._move(a, (step[1], step[2]), dt):
                a.plan = a.plan[1:]
        elif step[0] == "wait":
            a.state = "wait"
            a.wait_until = ts + step[1]
        elif step[0] == "queue":
            a.state = "queued"
            a.queue_since = ts
            self.queue.append(a)
        elif step[0] == "leave":
            a.done = True

    def _step_staff(self, a: Agent, ts: float, dt: float) -> None:
        if a.state == "wait":
            if ts >= a.wait_until:
                a.state = "walk"
            return
        if not a.plan:
            r = self.rng
            if r.random() < 0.04:  # restock a shelf now and then
                s = r.choice(list(SHELVES))
                x, y = r.choice(SHELVES[s])
                home = STAFF_HOME[a.id % len(STAFF_HOME)]
                a.plan = [("go", 0.36, 0.30), ("go", x, y), ("wait", r.uniform(10, 30)), ("go", 0.36, 0.30),
                          ("go", *home)]
            else:
                x = r.uniform(0.40, 0.70)
                a.plan = [("go", x, r.uniform(0.22, 0.30)), ("wait", r.uniform(3, 20))]
        step = a.plan[0]
        if step[0] == "go":
            if self._move(a, (step[1], step[2]), dt):
                a.plan = a.plan[1:]
        elif step[0] == "wait":
            a.state = "wait"
            a.wait_until = ts + step[1]
            a.plan = a.plan[1:]

    # ------------------------------------------------------------- outputs
    @staticmethod
    def box_of(a: Agent) -> tuple[float, float, float, float]:
        x, y = a.pos
        h = 0.17 + 0.25 * y
        w = h * 0.36
        return (x - w / 2, y - h, x + w / 2, y)

    def detections(self) -> list[Detection]:
        r = self.rng
        dets = []
        for a in self.agents:
            if r.random() < 0.03:
                continue  # missed detection
            x1, y1, x2, y2 = self.box_of(a)
            n = 0.004
            box = (x1 + r.gauss(0, n), y1 + r.gauss(0, n), x2 + r.gauss(0, n), min(1.0, y2 + r.gauss(0, n)))
            box = tuple(min(1.0, max(0.0, v)) for v in box)
            if box[3] - box[1] < 0.03:
                continue
            dets.append(Detection(box, r.uniform(0.62, 0.95)))
        if r.random() < 0.01:
            x, y = r.uniform(0.1, 0.9), r.uniform(0.3, 0.9)
            dets.append(Detection((x - 0.03, y - 0.1, x + 0.03, y), r.uniform(0.32, 0.45)))
        return dets


_BG_CACHE: dict[tuple[int, int], np.ndarray] = {}


def render_background(w: int, h: int) -> np.ndarray:
    if (w, h) in _BG_CACHE:
        return _BG_CACHE[(w, h)].copy()
    img = np.zeros((h, w, 3), np.uint8)
    for y in range(h):  # floor gradient
        c = int(170 + 50 * y / h)
        img[y, :] = (c, c - 6, c - 18)
    P = lambda x, y: (int(x * w), int(y * h))  # noqa: E731
    cv2.rectangle(img, P(0, 0), P(1, 0.17), (225, 230, 228), -1)  # back wall
    cv2.rectangle(img, P(0.34, 0.12), P(0.76, 0.34), (196, 205, 210), -1)  # back counter cabinets
    for i in range(7):
        x0 = 0.35 + i * 0.058
        cv2.rectangle(img, P(x0, 0.13), P(x0 + 0.05, 0.2), (90 + i * 15, 140, 200 - i * 12), -1)
    cv2.rectangle(img, P(0.36, 0.33), P(0.74, 0.38), (120, 86, 60), -1)  # counter
    cv2.rectangle(img, P(0.36, 0.33), P(0.74, 0.345), (160, 120, 90), -1)
    cv2.putText(img, "ECZANE", P(0.47, 0.095), cv2.FONT_HERSHEY_SIMPLEX, w / 900, (0, 140, 70), 2, cv2.LINE_AA)
    cv2.rectangle(img, P(0.44, 0.03), P(0.46, 0.12), (0, 170, 80), -1)  # green cross
    cv2.rectangle(img, P(0.42, 0.065), P(0.48, 0.085), (0, 170, 80), -1)

    def shelf(x1, y1, x2, y2, hue):
        cv2.rectangle(img, P(x1, y1), P(x2, y2), (150, 150, 155), -1)
        rows = 6
        for r_ in range(rows):
            yy = y1 + (y2 - y1) * r_ / rows
            for k in range(5):
                xx = x1 + (x2 - x1) * (0.08 + 0.18 * k)
                col = tuple(int(v) for v in np.clip(np.array(hue) + (k * 17 - 30), 0, 255))
                cv2.rectangle(img, P(xx, yy + 0.008), P(xx + (x2 - x1) * 0.13, yy + (y2 - y1) / rows - 0.006), col, -1)

    shelf(0.0, 0.28, 0.05, 0.78, (200, 120, 160))   # derma (left wall)
    shelf(0.95, 0.28, 1.0, 0.72, (230, 170, 60))    # vitamins (right wall)
    shelf(0.95, 0.74, 1.0, 0.92, (120, 180, 230))   # baby
    shelf(0.2, 0.2, 0.33, 0.27, (90, 200, 120))     # OTC
    cv2.rectangle(img, P(0.04, 0.955), P(0.32, 1.0), (95, 95, 95), -1)  # door mat
    cv2.line(img, P(0.04, 0.955), P(0.32, 0.955), (60, 60, 60), 2)
    _BG_CACHE[(w, h)] = img
    return img.copy()


def render_frame(sim: PharmacySim, w: int, h: int) -> np.ndarray:
    img = render_background(w, h)
    for a in sorted(sim.agents, key=lambda a: a.pos[1]):  # painter's order
        x1, y1, x2, y2 = PharmacySim.box_of(a)
        X1, Y1, X2, Y2 = int(x1 * w), int(y1 * h), int(x2 * w), int(y2 * h)
        bh, bw = Y2 - Y1, X2 - X1
        cx = (X1 + X2) // 2
        legs = (40, 40, 60) if not a.staff else (60, 60, 70)
        cv2.rectangle(img, (X1 + bw // 5, Y1 + int(bh * 0.6)), (X2 - bw // 5, Y2), legs, -1)
        torso = a.color
        coat_bottom = Y1 + int(bh * (0.78 if a.staff else 0.62))
        cv2.rectangle(img, (X1, Y1 + int(bh * 0.2)), (X2, coat_bottom), torso, -1)
        if a.staff:
            cv2.line(img, (cx, Y1 + int(bh * 0.22)), (cx, coat_bottom), (205, 205, 205), 1)
        cv2.circle(img, (cx, Y1 + int(bh * 0.11)), max(3, int(bh * 0.1)), (224, 184, 150), -1)
    return img


class SimulatorSource:
    def __init__(self, cfg):
        s = cfg.data.get("simulator", {})
        self.fps = float(s.get("fps", 10))
        self.speed = float(s.get("speed", 1.0))
        self.realtime = bool(s.get("realtime", True))
        self.w, self.h = int(s.get("width", 640)), int(s.get("height", 480))
        self.render = bool(s.get("render", True))
        self.start_ts = s.get("start_ts")
        self.duration = s.get("duration_s")
        self.sim = PharmacySim(seed=int(s.get("seed", 42)), arrival_scale=float(s.get("arrival_scale", 1.0)),
                               n_staff=int(s.get("staff", 2)), hour_override=s.get("hour_override"))

    def frames(self) -> Iterator[Frame]:
        ts = float(self.start_ts) if self.start_ts else time.time()
        t0 = ts
        dt = 1.0 / self.fps
        wall_next = time.monotonic()
        # warm up the scene so customers are already inside
        for k in range(int(120 / dt)):
            self.sim.step(ts - 120 + k * dt, dt)
        while self.duration is None or ts - t0 < self.duration:
            self.sim.step(ts, dt)
            img = render_frame(self.sim, self.w, self.h) if self.render else None
            yield Frame(ts=ts, image=img, detections=self.sim.detections())
            ts += dt
            if self.realtime:
                wall_next += dt / self.speed
                delay = wall_next - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                else:
                    wall_next = time.monotonic()
                if not self.start_ts:
                    ts = max(ts, time.time()) if self.speed == 1.0 else ts

    def close(self) -> None:
        pass
