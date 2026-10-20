"""Clothing-colour signature: recognise a person again after a break in tracking.

Two small colour histograms — upper body and legs — taken from the person's box and averaged
over time. Hue×saturation captures the colour of the clothes, a separate brightness histogram
tells black from grey from white (whose hue means nothing). It tells two nearby people apart
by what they wear; it is not a face, not biometric, and is never stored.
"""
from __future__ import annotations

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover - cv2 is optional on the dev box
    cv2 = None

H_BINS, S_BINS, V_BINS = 12, 4, 8
PARTS = ((0.16, 0.50), (0.55, 0.92))  # upper body, legs — fractions of the box height
MIN_PX = 6


def signature(image: np.ndarray, box: tuple[float, float, float, float]) -> np.ndarray | None:
    """Concatenated, per-part normalised histograms; None if the box is too small to judge."""
    if cv2 is None or image is None:
        return None
    h, w = image.shape[:2]
    x1, y1, x2, y2 = box
    bw, bh = x2 - x1, y2 - y1
    cx1, cx2 = int((x1 + 0.22 * bw) * w), int((x2 - 0.22 * bw) * w)  # skip arms and background
    if cx2 - cx1 < MIN_PX:
        return None
    parts = []
    for a, b in PARTS:
        cy1, cy2 = int((y1 + a * bh) * h), int((y1 + b * bh) * h)
        crop = image[max(0, cy1):min(h, cy2), max(0, cx1):min(w, cx2)]
        if crop.shape[0] < MIN_PX or crop.shape[1] < MIN_PX:
            return None
        hsv = cv2.cvtColor(np.ascontiguousarray(crop), cv2.COLOR_RGB2HSV)
        hs = cv2.calcHist([hsv], [0, 1], None, [H_BINS, S_BINS], [0, 180, 0, 256]).ravel()
        v = cv2.calcHist([hsv], [2], None, [V_BINS], [0, 256]).ravel()
        parts.append(np.concatenate([hs / (hs.sum() + 1e-9), v / (v.sum() + 1e-9)]))
    return np.concatenate(parts).astype(np.float32)


def blend(old: np.ndarray | None, new: np.ndarray, n: int) -> np.ndarray:
    """Running mean for the first samples, then a slow moving average."""
    if old is None:
        return new
    a = max(0.1, 1.0 / (n + 1))
    return (1 - a) * old + a * new


def distance(a: np.ndarray, b: np.ndarray) -> float:
    """0 = same clothes, 1 = nothing in common (mean Bhattacharyya distance of the four histograms)."""
    # each of the 4 sub-histograms sums to 1, so the Bhattacharyya coefficients add up to at most 4
    return float(1.0 - np.sqrt(np.clip(a, 0, None) * np.clip(b, 0, None)).sum() / 4.0)
