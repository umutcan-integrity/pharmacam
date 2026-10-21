"""Configuration loading.

All geometry (zones, door line) is stored in *normalized* image coordinates
(0..1), so the same config works for any stream resolution.
"""
from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ZONE_TYPES = ("shelf", "queue", "service", "staff", "entrance", "ignore")

DEFAULTS: dict[str, Any] = {
    "device": {
        "id": "pharmacam-001",
        "pharmacy_name": "Demo Eczanesi",
        "timezone": "Europe/Istanbul",
    },
    "camera": {
        "source": "imx500",  # imx500 | simulator
        # preset (yolo11n | nanodet | ssd, see camera/models.py) or a path to a .rpk network
        "model": "yolo11n",
        "width": 1280,
        "height": 960,
        "fps": "auto",  # auto: the highest rate at which the model analyses every frame
        "score_threshold": 0.45,
        "person_class": 0,
        # box format of a custom .rpk (presets know their own): auto | yx | xy, and
        # bbox_normalization true when its boxes come in input pixels
        "bbox_order": "auto",
        "bbox_normalization": None,
        "hflip": False,
        "vflip": False,
        "watchdog_seconds": 15,
    },
    "tracker": {
        "high_threshold": 0.55,
        "low_threshold": 0.3,
        "match_iou": 0.25,
        "min_hits": 3,
        "max_lost_seconds": 2.0,  # walking people
        "max_lost_static_seconds": 6.0,  # people standing still (queue, counter) are remembered longer
        "recovery_score": 0.3,  # how close a reappearing person must be to where a lost track was seen
    },
    "staff": {
        "enabled": True,
        "white_ratio_threshold": 0.32,
        "min_samples": 5,
        "saturation_max": 55,
        "value_min": 165,
        "staff_zone_ratio": 0.6,
    },
    "analytics": {
        "occupancy_mode": "hybrid",  # visible | counter | hybrid
        "min_zone_dwell_seconds": 3.0,
        "min_queue_seconds": 3.0,
        "door_hysteresis": 0.02,
        "heatmap_grid": [64, 48],
        "min_visit_seconds": 2.0,
        "empty_reset_seconds": 90,
    },
    "demographics": {
        # age / gender estimation, on the device, from the picture of each customer (never stored);
        # only per-visit estimates and daily totals are kept. Mention it in the privacy notice.
        "enabled": False,
        "show_live": False,  # show the estimate on the live view's person tag
        "model": "auto",  # mivolo (face + figure, most accurate) | insightface (face only) — see vision/demographics.py
        "max_rate": 4.0,  # analyses per second at most (CPU budget)
        "interval_s": 0.7,  # hand over a new picture of the same person at most this often
        "threads": 2,  # CPU cores the model may use (a Pi 5 has 4; the camera loop needs the rest)
    },
    "storage": {
        "path": "data/pharmacam.db",
        "retention_days": 400,
    },
    "server": {
        "host": "0.0.0.0",
        "port": 8080,
        "username": "admin",
        "password": "",  # empty = no auth (only for development)
        "live_view": True,
        "blur_people": False,  # live view only; frames are never stored either way
    },
    "reports": {
        "dir": "data/reports",
        "opening_hour": 8,
        "closing_hour": 22,
    },
    "sync": {
        "enabled": False,
        "hub_url": "",
        "device_token": "",
        "interval_seconds": 60,
    },
    "zones": [],
}


@dataclass
class Zone:
    id: str
    type: str
    name: str
    polygon: list[tuple[float, float]] = field(default_factory=list)
    line: list[tuple[float, float]] = field(default_factory=list)
    inside_point: tuple[float, float] | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Zone":
        ztype = d.get("type", "shelf")
        if ztype not in ZONE_TYPES:
            raise ValueError(f"Bilinmeyen bölge tipi: {ztype} (geçerli: {', '.join(ZONE_TYPES)})")
        z = cls(
            id=str(d["id"]),
            type=ztype,
            name=str(d.get("name", d["id"])),
            polygon=[tuple(map(float, p)) for p in d.get("polygon", [])],
            line=[tuple(map(float, p)) for p in d.get("line", [])],
            inside_point=tuple(map(float, d["inside_point"])) if d.get("inside_point") else None,
        )
        if ztype == "entrance":
            if len(z.line) != 2 or z.inside_point is None:
                raise ValueError(f"'{z.id}' giriş bölgesi için 2 noktalı 'line' ve 'inside_point' gerekli")
        elif len(z.polygon) < 3:
            raise ValueError(f"'{z.id}' bölgesi için en az 3 noktalı 'polygon' gerekli")
        return z

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"id": self.id, "type": self.type, "name": self.name}
        if self.type == "entrance":
            d["line"] = [list(p) for p in self.line]
            d["inside_point"] = list(self.inside_point) if self.inside_point else None
        else:
            d["polygon"] = [list(p) for p in self.polygon]
        return d


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    """Thin wrapper over the merged YAML dict with typed zone access."""

    def __init__(self, data: dict[str, Any], path: Path | None = None):
        self.path = path
        self.data = _merge(DEFAULTS, data)
        self.zones = [Zone.from_dict(z) for z in self.data.get("zones", [])]
        self.base_dir = path.parent.parent if path and path.parent.name == "config" else Path.cwd()

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def section(self, key: str) -> dict[str, Any]:
        return self.data[key]

    def resolve(self, p: str) -> Path:
        """Resolve a data path relative to the project root."""
        pp = Path(p)
        return pp if pp.is_absolute() else (self.base_dir / pp)

    def zones_of(self, ztype: str) -> list[Zone]:
        return [z for z in self.zones if z.type == ztype]

    def set_zones(self, zones: list[dict[str, Any]]) -> None:
        parsed = [Zone.from_dict(z) for z in zones]
        ids = [z.id for z in parsed]
        if len(ids) != len(set(ids)):
            raise ValueError("Bölge kimlikleri benzersiz olmalı")
        self.zones = parsed
        self.data["zones"] = [z.to_dict() for z in parsed]

    def save(self) -> None:
        if not self.path:
            return
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            yaml.safe_dump(self.data, f, allow_unicode=True, sort_keys=False)
        os.replace(tmp, self.path)

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        if path is None:
            path = os.environ.get("PHARMACAM_CONFIG", "config/pharmacam.yaml")
        p = Path(path).resolve()
        if not p.exists():
            return cls({}, p)
        with open(p, encoding="utf-8") as f:
            return cls(yaml.safe_load(f) or {}, p)
