"""Age and gender estimation of customers, on the device, in the background.

The camera loop never waits for it: it hands over a crop of a person now and then
(`submit`), a worker thread runs the models at a capped rate, and the results are
picked up on a later frame (`results`). The engine averages them over the visit.

    person crop ──► face found and large enough? ──yes──► face model: age + gender (weight ~1)
                                   │
                                   no ──► whole-figure model: gender only (weight ~0.35)

Nothing is stored: crops live in memory only until they are analysed. Backends are
pluggable (`camera.demographics.model`); `auto` takes the most accurate one whose
model files are on the device (`python -m pharmacam models --fetch` downloads them).
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque
from typing import Protocol

import numpy as np

log = logging.getLogger(__name__)

MIN_PERSON_PX = 72  # shorter person crops are too small to judge
MARGIN = 0.08  # crop margin around the person box


class Backend(Protocol):
    name: str

    def analyze(self, person: np.ndarray) -> dict | None:
        """RGB crop of one person → {"female": 0..1, "age": years | None, "w": weight, "src": str} or None."""


class DemographicsAnalyzer:
    def __init__(self, backend: Backend, max_rate: float = 4.0, queue_size: int = 6):
        self.backend = backend
        self.period = 1.0 / max(0.2, float(max_rate))
        self.q: queue.Queue = queue.Queue(maxsize=queue_size)
        self.out: deque = deque(maxlen=200)
        self.stats = {"submitted": 0, "analysed": 0, "faces": 0, "bodies": 0, "skipped": 0, "ms": 0.0}
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, name="demographics", daemon=True)
        self.thread.start()
        log.info("Yaş/cinsiyet analizi: %s (en fazla %.1f analiz/sn)", backend.name, 1 / self.period)

    # ------------------------------------------------------------ camera side
    def submit(self, track_id: int, image: np.ndarray, box, ts: float) -> bool:
        """Queue a crop of this person; False when it is too small or the worker is busy."""
        h, w = image.shape[:2]
        x1, y1, x2, y2 = box
        bw, bh = x2 - x1, y2 - y1
        px1, px2 = int(max(0.0, x1 - MARGIN * bw) * w), int(min(1.0, x2 + MARGIN * bw) * w)
        py1, py2 = int(max(0.0, y1 - MARGIN * bh) * h), int(min(1.0, y2 + MARGIN * bh) * h)
        if py2 - py1 < MIN_PERSON_PX or px2 - px1 < MIN_PERSON_PX // 4:
            return False
        try:
            self.q.put_nowait((track_id, ts, np.ascontiguousarray(image[py1:py2, px1:px2])))
        except queue.Full:
            self.stats["skipped"] += 1
            return False
        self.stats["submitted"] += 1
        return True

    def results(self) -> list[tuple[int, dict]]:
        items = []
        while self.out:
            items.append(self.out.popleft())
        return items

    # ------------------------------------------------------------ worker side
    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                track_id, ts, crop = self.q.get(timeout=0.5)
            except queue.Empty:
                continue
            t0 = time.perf_counter()
            try:
                obs = self.backend.analyze(crop)
            except Exception:  # a model error must never take the counting down
                log.exception("yaş/cinsiyet analizi hatası")
                obs = None
            dt = time.perf_counter() - t0
            self.stats["analysed"] += 1
            self.stats["ms"] = 0.9 * self.stats["ms"] + 0.1 * dt * 1000 if self.stats["ms"] else dt * 1000
            if obs:
                self.stats["faces" if obs.get("src") == "face" else "bodies"] += 1
                self.out.append((track_id, obs))
            time.sleep(max(0.0, self.period - dt))

    def close(self) -> None:
        self._stop.set()


BACKENDS: dict[str, type] = {}  # name → backend class with .available(cfg) and .from_config(cfg)


def register(cls):
    BACKENDS[cls.name] = cls
    return cls


def create_analyzer(cfg) -> DemographicsAnalyzer:
    d = cfg["demographics"]
    want = str(d.get("model", "auto"))
    order = list(BACKENDS) if want == "auto" else [want]
    for name in order:
        cls = BACKENDS.get(name)
        if cls is None:
            raise RuntimeError(f"Bilinmeyen yaş/cinsiyet modeli: {name}")
        ok, why = cls.available(cfg)
        if ok:
            return DemographicsAnalyzer(cls.from_config(cfg), max_rate=float(d.get("max_rate", 4.0)))
        log.info("Yaş/cinsiyet modeli %s kullanılamıyor: %s", name, why)
    raise RuntimeError("Yaş/cinsiyet modeli dosyaları yok: python -m pharmacam models --fetch")
