"""A recorded clip as the camera: plays a video file in a loop through a person detector.

For demos and for tuning zones without being in the shop. The AI Camera runs its network on
the sensor, so a recording needs the same kind of model on the computer instead — YOLO11n,
the model the camera uses (needs `pip install ultralytics`; the Mac's GPU is used if present).

    camera:
      source: video
    video:
      path: data/videos/store.mp4
      loop: true
      fps: 15            # analysis rate (the clip is subsampled if it is faster)
      model: yolo11n.pt
      conf: 0.3          # keep low-confidence boxes for the tracker, like the camera does
"""
from __future__ import annotations

import logging
import time
from typing import Iterator

import numpy as np

from .base import Detection, Frame

log = logging.getLogger(__name__)


class VideoFileSource:
    def __init__(self, cfg):
        import cv2

        v = cfg.data.get("video", {})
        self.path = cfg.resolve(v.get("path", ""))
        if not self.path.exists():
            raise RuntimeError(f"Video bulunamadı: {self.path}")
        self.loop = bool(v.get("loop", True))
        self.fps = float(v.get("fps", 15))
        self.realtime = bool(v.get("realtime", True))
        self.conf = float(v.get("conf", 0.3))
        self.imgsz = int(v.get("imgsz", 640))
        self.cv2 = cv2
        try:
            from ultralytics import YOLO
        except ImportError as e:  # pragma: no cover - optional dependency
            raise RuntimeError("Video kaynağı için kişi tespit modeli gerekli: pip install ultralytics") from e
        name = v.get("model", "yolo11n.pt")
        local = cfg.resolve(name)
        self.model = YOLO(str(local) if local.exists() else name)  # else Ultralytics downloads it
        self.device = v.get("device") or self._best_device()
        log.info("Video kaynağı: %s (döngü=%s, %s kare/sn, model=%s, cihaz=%s)",
                 self.path.name, self.loop, self.fps, v.get("model", "yolo11n.pt"), self.device)

    @staticmethod
    def _best_device() -> str:
        try:
            import torch

            if torch.cuda.is_available():
                return "cuda"
            if torch.backends.mps.is_available():
                return "mps"
        except Exception:  # pragma: no cover
            pass
        return "cpu"

    def _detect(self, rgb: np.ndarray) -> list[Detection]:
        res = self.model.predict(rgb[..., ::-1], imgsz=self.imgsz, conf=self.conf, classes=[0], device=self.device,
                                 verbose=False)[0]
        if res.boxes is None or len(res.boxes) == 0:
            return []
        boxes = res.boxes.xyxyn.cpu().numpy()
        scores = res.boxes.conf.cpu().numpy()
        return [Detection(tuple(float(min(1.0, max(0.0, v))) for v in b), float(s)) for b, s in zip(boxes, scores)]

    def frames(self) -> Iterator[Frame]:
        cv2 = self.cv2
        cap = cv2.VideoCapture(str(self.path))
        src_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        step = max(1, round(src_fps / self.fps))  # analyse every n-th frame of a fast clip
        idx, loops = 0, 0
        clip_t0 = time.monotonic()
        try:
            while True:
                ok, bgr = cap.read()
                if not ok:
                    if not self.loop:
                        return
                    cap.release()
                    cap = cv2.VideoCapture(str(self.path))
                    idx, loops, clip_t0 = 0, loops + 1, time.monotonic()
                    continue
                idx += 1
                if (idx - 1) % step:
                    continue
                if self.realtime:  # play at the clip's own speed
                    delay = clip_t0 + idx / src_fps - time.monotonic()
                    if delay > 0:
                        time.sleep(delay)
                rgb = np.ascontiguousarray(bgr[..., ::-1])
                yield Frame(ts=time.time(), image=rgb, detections=self._detect(rgb))
        finally:
            cap.release()

    def close(self) -> None:
        pass
