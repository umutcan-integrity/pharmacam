import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pharmacam.camera.base import Detection, Frame  # noqa: E402
from pharmacam.config import Config  # noqa: E402

TZ = ZoneInfo("Europe/Istanbul")


def ts_at(day: str, hour: int, minute: int = 0, second: float = 0) -> float:
    y, m, d = map(int, day.split("-"))
    return datetime(y, m, d, hour, minute, tzinfo=TZ).timestamp() + second


def sim_config(tmp_path: Path, **overrides) -> Config:
    data = yaml.safe_load((ROOT / "config" / "pharmacam.simulator.yaml").read_text())
    data["storage"]["path"] = str(tmp_path / "test.db")
    data["reports"]["dir"] = str(tmp_path / "reports")
    for k, v in overrides.items():
        data.setdefault(k, {}).update(v)
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(exist_ok=True)
    p = cfg_dir / "pharmacam.yaml"
    p.write_text(yaml.safe_dump(data, allow_unicode=True))
    return Config.load(p)


def person_box(x: float, y: float, h: float = 0.3) -> tuple:
    """Box with its foot point at (x, y)."""
    w = h * 0.36
    return (x - w / 2, y - h, x + w / 2, y)


class Scene:
    """Scripted scene: every person follows (t, x, y) keyframes, linearly interpolated."""

    def __init__(self, start: float, fps: float = 10.0, size=(320, 240)):
        self.start = start
        self.fps = fps
        self.people: list[dict] = []
        self.size = size

    def add(self, keyframes, coat=False):
        self.people.append({"kf": keyframes, "coat": coat})
        return self

    def _pos(self, kf, t):
        if t < kf[0][0] or t > kf[-1][0]:
            return None
        for (t0, x0, y0), (t1, x1, y1) in zip(kf, kf[1:]):
            if t0 <= t <= t1:
                a = 0 if t1 == t0 else (t - t0) / (t1 - t0)
                return x0 + (x1 - x0) * a, y0 + (y1 - y0) * a
        return kf[-1][1], kf[-1][2]

    def frames(self, duration: float):
        w, h = self.size
        n = int(duration * self.fps)
        for i in range(n + 1):
            t = i / self.fps
            img = np.full((h, w, 3), 90, np.uint8)
            dets = []
            for p in self.people:
                pos = self._pos(p["kf"], t)
                if pos is None:
                    continue
                box = person_box(*pos)
                dets.append(Detection(box, 0.9))
                x1, y1, x2, y2 = (int(box[0] * w), int(box[1] * h), int(box[2] * w), int(box[3] * h))
                torso = (245, 245, 245) if p["coat"] else (30, 60, 160)
                img[max(0, y1):max(0, y2), max(0, x1):max(0, x2)] = torso
            yield Frame(ts=self.start + t, image=img, detections=dets)


@pytest.fixture
def cfg(tmp_path):
    return sim_config(tmp_path)
