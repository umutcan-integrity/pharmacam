"""On-sensor detection models for the IMX500 and how to read their outputs.

`camera.model` is a preset name below or a path to any .rpk detection network.
Accuracy is COCO mAP after INT8 quantisation on the IMX500 (raspberrypi/imx500-models).
`fps` is the camera rate at which the network keeps up with every frame on a Pi 5 —
measured on our unit: YOLO11n runs ~33 ms on the sensor, so at 15 fps every frame is
analysed, while at 16-30 fps only every 2nd-4th frame is.
"""
from __future__ import annotations

import hashlib
import logging
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SYSTEM_DIR = Path("/usr/share/imx500-models")


@dataclass(frozen=True)
class ModelSpec:
    key: str
    title: str
    file: str  # file name; looked up in <root>/models, then in SYSTEM_DIR
    fps: int
    coco_map: float
    licence: str
    bbox_order: str = "yx"  # order of the box tensor: "yx" (y0,x0,y1,x1) or "xy"
    bbox_normalization: bool = False  # True: boxes are in input pixels and must be divided
    url: str | None = None
    sha256: str | None = None


MODELS: dict[str, ModelSpec] = {
    m.key: m
    for m in (
        ModelSpec("yolo11n", "YOLO11n 640×640", "imx500_network_yolo11n_pp.rpk", fps=15, coco_map=0.374,
                  licence="AGPL-3.0", bbox_order="xy", bbox_normalization=True,
                  url="https://github.com/raspberrypi/imx500-models/raw/main/imx500_network_yolo11n_pp.rpk",
                  sha256="c8e53dd9208debff3cd72044600095624952d6fb4e67910e2e8098251e0307fa"),
        ModelSpec("nanodet", "NanoDet-Plus 416×416", "imx500_network_nanodet_plus_416x416_pp.rpk", fps=30,
                  coco_map=0.320, licence="Apache-2.0"),
        ModelSpec("ssd", "SSD MobileNetV2 FPNLite 320×320", "imx500_network_ssd_mobilenetv2_fpnlite_320x320_pp.rpk",
                  fps=30, coco_map=0.218, licence="MIT"),
    )
}


@dataclass(frozen=True)
class ResolvedModel:
    path: Path
    title: str
    fps: float
    bbox_order: str | None  # None: whatever the .rpk declares
    bbox_normalization: bool | None
    spec: ModelSpec | None


def model_dir(cfg) -> Path:
    return Path(os.environ.get("PHARMACAM_MODELS") or cfg.resolve("models"))


def _locate(cfg, spec: ModelSpec) -> Path:
    local = model_dir(cfg) / spec.file
    system = SYSTEM_DIR / spec.file
    return system if not local.exists() and system.exists() else local


def resolve(cfg) -> ResolvedModel:
    """What to load, at which frame rate and how to decode its boxes (explicit config wins)."""
    c = cfg["camera"]
    name = str(c["model"])
    spec = MODELS.get(name)
    if spec:
        path, title = _locate(cfg, spec), spec.title
        order, norm, fps = spec.bbox_order, spec.bbox_normalization, spec.fps
    else:  # a path to some other .rpk
        path, title = Path(name), Path(name).name
        order, norm, fps = None, None, 30
    if c.get("bbox_order") in ("xy", "yx"):
        order = c["bbox_order"]
    if c.get("bbox_normalization") is not None:
        norm = bool(c["bbox_normalization"])
    if str(c.get("fps", "auto")).lower() != "auto":
        fps = float(c["fps"])
    return ResolvedModel(path=path, title=title, fps=float(fps), bbox_order=order, bbox_normalization=norm, spec=spec)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(url: str, path: Path, sha256: str | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    log.info("Model indiriliyor: %s ← %s", path.name, url)
    with urllib.request.urlopen(url, timeout=120) as r, open(tmp, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
    if sha256 and _sha256(tmp) != sha256:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"İndirilen model doğrulanamadı (sha256): {url}")
    tmp.replace(path)


def ensure(cfg) -> ResolvedModel:
    """Download the configured preset if it is not on the device yet (checksum verified)."""
    m = resolve(cfg)
    spec = m.spec
    if m.path.exists():
        if spec and spec.sha256 and m.path.parent != SYSTEM_DIR and _sha256(m.path) != spec.sha256:
            raise RuntimeError(f"Model dosyası bozuk (sha256 uyuşmuyor): {m.path}")
        return m
    if not spec or not spec.url:
        raise RuntimeError(f"Model dosyası yok: {m.path}")
    _download(spec.url, m.path, spec.sha256)
    return m


@dataclass(frozen=True)
class FileSpec:
    """A CPU model used next to the camera (age / gender estimation)."""

    file: str
    title: str
    licence: str
    url: str | None = None  # None: made on a development machine and copied to the device
    sha256: str | None = None
    how: str = ""


DEMOGRAPHICS_FILES = (
    FileSpec("face_detection_yunet_2023mar.onnx", "YuNet yüz bulucu (OpenCV Zoo)", "MIT",
             url="https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/"
                 "face_detection_yunet_2023mar.onnx",
             sha256="8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"),
    FileSpec("mivolo_v2_384_int8.onnx", "MiVOLO v2 yaş/cinsiyet, yüz + beden (int8)", "Apache-2.0 (model kartı)",
             how="geliştirme bilgisayarında `python -m pharmacam.tools.export_mivolo models/mivolo_v2_384.onnx "
                 "--int8`, sonra cihazın models/ klasörüne kopyalayın"),
)


def ensure_demographics(cfg) -> list[tuple[FileSpec, Path, str]]:
    """Fetch the downloadable age/gender files; (spec, path, state) for each — state: ok | missing | error text."""
    out = []
    for spec in DEMOGRAPHICS_FILES:
        path = model_dir(cfg) / spec.file
        try:
            if not path.exists() and spec.url:
                _download(spec.url, path, spec.sha256)
            out.append((spec, path, "ok" if path.exists() else "missing"))
        except Exception as e:  # offline: counting works without it
            out.append((spec, path, str(e)))
    return out


def fallback() -> ResolvedModel:
    """The model that ships with the camera packages — used when the configured one is missing."""
    s = MODELS["ssd"]
    return ResolvedModel(path=SYSTEM_DIR / s.file, title=s.title, fps=float(s.fps), bbox_order=s.bbox_order,
                         bbox_normalization=s.bbox_normalization, spec=s)


def describe(cfg, loaded: ResolvedModel | None = None) -> dict[str, Any]:
    m = loaded or resolve(cfg)
    s = m.spec
    return {"key": s.key if s else None, "title": m.title, "fps": m.fps, "coco_map": s.coco_map if s else None,
            "licence": s.licence if s else None, "path": str(m.path), "present": m.path.exists(),
            "fallback": bool(loaded and loaded.path != resolve(cfg).path)}
