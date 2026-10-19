"""Heatmap rendering: dwell-time grid -> colored overlay on the store background."""
from __future__ import annotations

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None


RAMP_LOW = (255, 214, 102)  # pale amber
RAMP_HIGH = (160, 20, 20)  # deep red


def normalize(grid: np.ndarray) -> np.ndarray:
    g = grid.astype(np.float32)
    if cv2 is not None:
        g = cv2.GaussianBlur(g, (0, 0), sigmaX=1.6)
    hi = float(np.percentile(g[g > 0], 98)) if (g > 0).any() else 0.0
    if hi <= 0:
        return np.zeros_like(g)
    # square root keeps quieter areas (shelves) visible next to the busy counter
    return np.sqrt(np.clip(g / hi, 0, 1))


def render(grid: np.ndarray | None, background: np.ndarray | None, size=(960, 720), alpha=0.6) -> np.ndarray:
    """Return an RGB image with the heatmap blended over the background."""
    w, h = size
    if background is None:
        bg = np.full((h, w, 3), 40, np.uint8)
    else:
        bg = cv2.resize(background, (w, h)) if cv2 is not None else background
    if grid is None or not np.any(grid):
        return bg
    n = normalize(grid)
    up = cv2.resize(n, (w, h), interpolation=cv2.INTER_CUBIC)
    up = np.clip(up, 0, 1)
    # single-hue sequential ramp (light -> dark orange-red); intensity also drives opacity
    lo, hi = np.array(RAMP_LOW, np.float32), np.array(RAMP_HIGH, np.float32)
    color = lo + (hi - lo) * up[..., None]
    a = (np.clip(up * 1.6, 0, 1) * alpha)[..., None]
    out = bg.astype(np.float32) * (1 - a) + color.astype(np.float32) * a
    return out.astype(np.uint8)


def encode_png(rgb: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    return buf.tobytes() if ok else b""


def encode_jpeg(rgb: np.ndarray, quality: int = 80) -> bytes:
    ok, buf = cv2.imencode(".jpg", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buf.tobytes() if ok else b""
