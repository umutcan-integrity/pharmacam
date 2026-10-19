"""Staff (white lab coat) recognition.

Pharmacists and technicians in Turkey wear white coats. We sample the torso
region of each person box and measure the fraction of "white" pixels
(low saturation, high value in HSV). The per-track score is an exponential
moving average, so one bright frame does not flip a customer into staff.

A second, independent cue is the staff zone (behind the counter): a person
who spends most of their time there is staff regardless of clothing.
"""
from __future__ import annotations

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover - cv2 is optional on the dev box
    cv2 = None


def torso_crop(image: np.ndarray, box: tuple[float, float, float, float]) -> np.ndarray | None:
    h, w = image.shape[:2]
    x1, y1, x2, y2 = box
    bw, bh = x2 - x1, y2 - y1
    # torso: 22%..62% of height, central 60% of width (skips head, hands, legs)
    tx1 = int((x1 + 0.2 * bw) * w)
    tx2 = int((x2 - 0.2 * bw) * w)
    ty1 = int((y1 + 0.22 * bh) * h)
    ty2 = int((y1 + 0.62 * bh) * h)
    tx1, ty1 = max(0, tx1), max(0, ty1)
    tx2, ty2 = min(w, tx2), min(h, ty2)
    if tx2 - tx1 < 6 or ty2 - ty1 < 6:
        return None
    return image[ty1:ty2, tx1:tx2]


def white_ratio(crop: np.ndarray, saturation_max: int = 55, value_min: int = 165) -> float:
    """Fraction of near-white pixels in an RGB crop."""
    if cv2 is not None:
        hsv = cv2.cvtColor(crop, cv2.COLOR_RGB2HSV)
        s, v = hsv[..., 1], hsv[..., 2]
    else:
        c = crop.astype(np.float32)
        mx, mn = c.max(axis=2), c.min(axis=2)
        v = mx
        s = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1) * 255, 0)
    # adapt to dim pharmacies: "white" is relative to the crop's brightest pixels
    v_thr = min(value_min, float(np.percentile(v, 95)) * 0.85) if v.size else value_min
    mask = (s <= saturation_max) & (v >= max(v_thr, 110))
    return float(mask.mean()) if mask.size else 0.0


class StaffClassifier:
    def __init__(self, enabled=True, white_ratio_threshold=0.32, min_samples=5,
                 saturation_max=55, value_min=165, staff_zone_ratio=0.6):
        self.enabled = enabled
        self.threshold = white_ratio_threshold
        self.min_samples = min_samples
        self.s_max = saturation_max
        self.v_min = value_min
        self.zone_ratio = staff_zone_ratio

    @classmethod
    def from_config(cls, cfg: dict) -> "StaffClassifier":
        return cls(**cfg)

    def observe(self, track, image: np.ndarray | None) -> None:
        """Update the track's coat score from the current frame."""
        if not self.enabled or image is None:
            return
        crop = torso_crop(image, track.box)
        if crop is None:
            return
        r = white_ratio(crop, self.s_max, self.v_min)
        m = track.meta
        n = m.get("coat_n", 0)
        prev = m.get("coat_score", r)
        alpha = max(0.08, 1.0 / (n + 1))  # running mean first, then EMA
        m["coat_score"] = (1 - alpha) * prev + alpha * r
        m["coat_n"] = n + 1

    def is_staff(self, track) -> bool:
        m = track.meta
        if not self.enabled:
            return False
        coat = m.get("coat_n", 0) >= self.min_samples and m.get("coat_score", 0.0) >= self.threshold
        seen = m.get("seen_s", 0.0)
        zone = seen > 5.0 and m.get("staff_zone_s", 0.0) / max(seen, 1e-6) >= self.zone_ratio
        return coat or zone
