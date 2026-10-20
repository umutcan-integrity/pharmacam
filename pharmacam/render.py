"""Drawing helpers for the live view and zone editor snapshots."""
from __future__ import annotations

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

ZONE_COLORS = {  # RGB — same palette as the panel and the film
    "entrance": (255, 190, 64),
    "queue": (255, 190, 64),
    "service": (255, 150, 112),
    "staff": (190, 152, 255),
    "shelf": (255, 92, 138),
    "ignore": (142, 142, 147),
}


def blur_boxes(img: np.ndarray, boxes: list[list[float]]) -> np.ndarray:
    h, w = img.shape[:2]
    out = img.copy()
    for b in boxes:
        x1, y1, x2, y2 = int(b[0] * w), int(b[1] * h), int(b[2] * w), int(b[3] * h)
        x1, y1, x2, y2 = max(0, x1), max(0, y1), min(w, x2), min(h, y2)
        if x2 - x1 > 2 and y2 - y1 > 2:
            roi = out[y1:y2, x1:x2]
            k = max(9, ((x2 - x1) // 3) | 1)
            out[y1:y2, x1:x2] = cv2.GaussianBlur(roi, (k, k), 0)
    return out


def draw_zones(img: np.ndarray, zones, alpha: float = 0.18) -> np.ndarray:
    h, w = img.shape[:2]
    overlay = img.copy()
    for z in zones:
        color = ZONE_COLORS.get(z.type, (200, 200, 200))
        if z.type == "entrance":
            (ax, ay), (bx, by) = z.line
            cv2.line(img, (int(ax * w), int(ay * h)), (int(bx * w), int(by * h)), color, 3, cv2.LINE_AA)
            cv2.putText(img, z.name, (int(ax * w) + 4, int(ay * h) - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1,
                        cv2.LINE_AA)
            continue
        pts = np.array([[int(x * w), int(y * h)] for x, y in z.polygon], np.int32)
        cv2.fillPoly(overlay, [pts], color)
        cv2.polylines(img, [pts], True, color, 2, cv2.LINE_AA)
    img = cv2.addWeighted(overlay, alpha, img, 1 - alpha, 0)
    for z in zones:
        if z.type == "entrance" or not z.polygon:
            continue
        x = int(min(p[0] for p in z.polygon) * w) + 4
        y = int(min(p[1] for p in z.polygon) * h) + 14
        cv2.putText(img, z.name, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 2, cv2.LINE_AA)
        cv2.putText(img, z.name, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.42, ZONE_COLORS.get(z.type), 1, cv2.LINE_AA)
    return img


def annotate(img: np.ndarray, live: dict, zones, blur: bool = True) -> np.ndarray:
    tracks = live.get("tracks", [])
    out = blur_boxes(img, [t["box"] for t in tracks]) if blur else img.copy()
    out = draw_zones(out, zones)
    h, w = out.shape[:2]
    for t in tracks:
        x1, y1, x2, y2 = (int(t["box"][0] * w), int(t["box"][1] * h), int(t["box"][2] * w), int(t["box"][3] * h))
        color = (29, 29, 31) if t["staff"] else (255, 159, 10)  # staff graphite, customer amber
        cv2.rectangle(out, (x1, y1), (x2, y2), color, 2)
        label = f"Personel #{t['id']}" if t["staff"] else f"#{t['id']} {int(t['seen_s'])}sn"
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.42, 1)
        cv2.rectangle(out, (x1, max(0, y1 - th - 6)), (x1 + tw + 6, y1), color, -1)
        cv2.putText(out, label, (x1 + 3, y1 - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1, cv2.LINE_AA)
    bar = f"Iceride: {live.get('occupancy', 0)}  Sira: {live.get('queue_length', 0)}  Personel: {live.get('staff_on_duty', 0)}"
    cv2.rectangle(out, (0, 0), (w, 24), (22, 22, 24), -1)
    cv2.putText(out, bar, (8, 17), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
    return out
