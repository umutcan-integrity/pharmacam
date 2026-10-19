"""Small geometry helpers working in normalized coordinates."""
from __future__ import annotations

from typing import Sequence

Point = tuple[float, float]


def point_in_polygon(p: Point, poly: Sequence[Point]) -> bool:
    """Ray casting test."""
    x, y = p
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y):
            x_cross = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < x_cross:
                inside = not inside
        j = i
    return inside


def signed_side(p: Point, a: Point, b: Point) -> float:
    """Signed distance of p to the infinite line a-b (sign = side)."""
    (ax, ay), (bx, by) = a, b
    dx, dy = bx - ax, by - ay
    length = (dx * dx + dy * dy) ** 0.5 or 1e-9
    return ((p[0] - ax) * dy - (p[1] - ay) * dx) / length


def projection_t(p: Point, a: Point, b: Point) -> float:
    """Position of p's projection along segment a-b (0 at a, 1 at b)."""
    (ax, ay), (bx, by) = a, b
    dx, dy = bx - ax, by - ay
    denom = dx * dx + dy * dy or 1e-9
    return ((p[0] - ax) * dx + (p[1] - ay) * dy) / denom


def iou(a: Sequence[float], b: Sequence[float]) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0
