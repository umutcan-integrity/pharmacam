from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Protocol

import numpy as np


@dataclass
class Detection:
    """A person detection in normalized image coordinates (x1, y1, x2, y2 in 0..1)."""

    box: tuple[float, float, float, float]
    score: float
    # optional per-person estimates that come with the detection (the simulator's ground truth
    # with noise, or a camera that estimates them): {"female": 0..1, "age": years, "w": weight}
    attrs: dict | None = None

    @property
    def foot(self) -> tuple[float, float]:
        x1, _, x2, y2 = self.box
        return ((x1 + x2) / 2, y2)


@dataclass
class Frame:
    ts: float  # unix seconds
    image: np.ndarray | None  # RGB uint8 HxWx3, may be None when not needed
    detections: list[Detection]
    # False: the detector produced nothing new for this frame (the IMX500 runs its network on
    # every other frame at 30 fps) — the picture is still current, the detections are not
    fresh: bool = True
    # True on the first frame of a new scene (a recording that loops): everyone seen so far is gone
    cut: bool = False
    # with `cut`: the scene replays the same people, so today's counts start over (video demo)
    new_day: bool = False


class FrameSource(Protocol):
    def frames(self) -> Iterator[Frame]: ...

    def close(self) -> None: ...
