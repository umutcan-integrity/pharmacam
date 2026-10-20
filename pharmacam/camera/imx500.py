"""Raspberry Pi AI Camera (Sony IMX500) source.

The neural network runs *on the sensor*; every frame arrives with its
detection tensor in the request metadata, so the Pi CPU only parses boxes.
"""
from __future__ import annotations

import logging
import time
from typing import Iterator

import numpy as np

from .base import Detection, Frame

log = logging.getLogger(__name__)


class IMX500Source:
    def __init__(self, cfg):
        from libcamera import Transform
        from picamera2 import Picamera2
        from picamera2.devices import IMX500
        from picamera2.devices.imx500 import NetworkIntrinsics

        c = cfg["camera"]
        self.threshold = float(c["score_threshold"])
        self.person_class = int(c["person_class"])
        # low-score boxes are kept for the tracker's second association stage
        self.keep_threshold = min(self.threshold, float(cfg["tracker"]["low_threshold"]))

        if not Picamera2.global_camera_info():
            raise RuntimeError("Kamera bulunamadı (kablo bağlantısını kontrol edin)")

        self.imx500 = IMX500(c["model"])
        intr = self.imx500.network_intrinsics
        if intr is None:
            intr = NetworkIntrinsics()
            intr.task = "object detection"
        if intr.task != "object detection":
            raise RuntimeError(f"Model bir nesne tespit modeli değil: {c['model']}")
        intr.update_with_defaults()
        self.intr = intr

        self.picam2 = Picamera2(self.imx500.camera_num)
        self.size = (int(c["width"]), int(c["height"]))
        config = self.picam2.create_preview_configuration(
            # "BGR888" in libcamera naming == [R, G, B] byte order in numpy
            main={"size": self.size, "format": "BGR888"},
            controls={"FrameRate": float(c["fps"])},
            buffer_count=6,
            transform=Transform(hflip=bool(c["hflip"]), vflip=bool(c["vflip"])),
        )
        log.info("IMX500 ağ yazılımı yükleniyor (ilk açılışta 1-2 dk sürebilir)...")
        self.picam2.start(config, show_preview=False)
        if intr.preserve_aspect_ratio:
            self.imx500.set_auto_aspect_ratio()
        self.input_w, self.input_h = self.imx500.get_input_size()
        log.info("IMX500 hazır: model=%s giriş=%sx%s akış=%sx%s", c["model"].rsplit("/", 1)[-1],
                 self.input_w, self.input_h, *self.size)

    def _parse(self, metadata: dict) -> list[Detection] | None:
        outputs = self.imx500.get_outputs(metadata, add_batch=True)
        if outputs is None:
            return None  # network output not ready for this frame
        boxes, scores, classes = outputs[0][0], outputs[1][0], outputs[2][0]
        if self.intr.bbox_normalization:
            boxes = boxes / self.input_h
        if self.intr.bbox_order == "xy":
            boxes = boxes[:, [1, 0, 3, 2]]
        W, H = self.size
        dets = []
        for box, score, cls in zip(boxes, scores, classes):
            if int(cls) != self.person_class or float(score) < self.keep_threshold:
                continue
            x, y, w, h = self.imx500.convert_inference_coords(tuple(box), metadata, self.picam2)
            x1, y1 = max(0.0, x / W), max(0.0, y / H)
            x2, y2 = min(1.0, (x + w) / W), min(1.0, (y + h) / H)
            if x2 - x1 < 0.01 or y2 - y1 < 0.02:
                continue
            dets.append(Detection((x1, y1, x2, y2), float(score)))
        return dets

    def frames(self) -> Iterator[Frame]:
        last: list[Detection] = []
        while True:
            request = self.picam2.capture_request()
            try:
                metadata = request.get_metadata()
                image = request.make_array("main")
            finally:
                request.release()
            dets = self._parse(metadata)
            fresh = dets is not None
            if fresh:
                last = dets
            yield Frame(ts=time.time(), image=np.ascontiguousarray(image[..., :3]), detections=last, fresh=fresh)

    def close(self) -> None:
        try:
            self.picam2.stop()
            self.picam2.close()
        except Exception:  # pragma: no cover
            log.exception("kamera kapatılamadı")
