"""Age and gender estimation of customers, on the device, in the background.

The camera loop never waits for it: now and then it hands over the latest picture of a person
(`submit`); a worker thread analyses one person at a time, whoever has been looked at least, and
the results are picked up on a later frame (`results`). The engine averages them over the visit.

    person ──► face found, ≥ 32 px? ──yes──► face and figure together: age + gender (weight up to 1)
                      │
                      no ──► figure alone (weight by size; seen from behind counts least)

Measured on hand-labelled store footage (720p, ceiling corner, 1 925 pictures of 65 people):
face + figure is right about gender 93 % of the time once the face is 32 px or more, the
figure alone 88 % from the front and 70 % from behind (it then takes women in dark coats for
men). A visit's estimate is a weighted vote and stays "unknown" when the evidence is thin.

Nothing is stored: pictures live in memory only until they are analysed. Backends are
pluggable (`demographics.model`); `auto` takes the most accurate one whose model files
are on the device.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

log = logging.getLogger(__name__)

MIN_PERSON_PX = 72  # shorter people are too small to judge
MARGIN = 0.08  # crop margin around the person box (keeps the head whole for face finding)
MAX_WAIT_S = 3.0  # a picture not analysed by then is dropped: a newer one will come
BACK_LOOKS = 3  # after this many analyses from behind, wait for the person to face the camera


@dataclass
class Sample:
    track_id: int
    crop: np.ndarray  # RGB: the person with a margin
    box: tuple[int, int, int, int]  # the person's own box inside the crop, pixels
    others: list[tuple[int, int, int, int]] = field(default_factory=list)  # other people in the crop
    t: float = 0.0  # time.monotonic() when taken
    bgr: np.ndarray | None = None  # the crop in OpenCV's channel order, made once by the worker


class Backend(Protocol):
    name: str
    min_person_px: int

    def find_face(self, s: Sample):
        """Cheap: the best face in the picture or None."""

    def estimate(self, s: Sample, face) -> dict | None:
        """{"female": 0..1, "age": years | None, "w": gender weight, "aw": age weight, "src": str} or None."""


class DemographicsAnalyzer:
    def __init__(self, backend: Backend, max_rate: float = 4.0):
        self.backend = backend
        self.period = 1.0 / max(0.2, float(max_rate))
        self.min_px = int(getattr(backend, "min_person_px", MIN_PERSON_PX))
        self.out: deque = deque(maxlen=200)
        self.stats = {"submitted": 0, "analysed": 0, "faces": 0, "bodies": 0, "backs": 0, "skipped": 0,
                      "stale": 0, "ms": 0.0}
        self._pending: dict[int, Sample] = {}  # latest picture of each person
        self._looks: dict[int, int] = {}
        self._backs: dict[int, int] = {}
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, name="demographics", daemon=True)
        self.thread.start()
        log.info("Yaş/cinsiyet analizi: %s", backend.name)

    # ------------------------------------------------------------ camera side
    def submit(self, track_id: int, image: np.ndarray, box, ts: float, others=()) -> bool:
        """Keep the latest picture of this person for the worker; False when too small to judge."""
        h, w = image.shape[:2]
        x1, y1, x2, y2 = box
        bw, bh = x2 - x1, y2 - y1
        if bh * h < self.min_px or bw * w < self.min_px // 5:
            return False
        px1, px2 = int(max(0.0, x1 - MARGIN * bw) * w), int(min(1.0, x2 + MARGIN * bw) * w)
        py1, py2 = int(max(0.0, y1 - MARGIN * bh) * h), int(min(1.0, y2 + MARGIN * bh) * h)
        cw, ch = px2 - px1, py2 - py1

        def local(b):
            return (min(cw, max(0, int(b[0] * w) - px1)), min(ch, max(0, int(b[1] * h) - py1)),
                    min(cw, max(0, int(b[2] * w) - px1)), min(ch, max(0, int(b[3] * h) - py1)))

        oth = [o for o in map(local, others) if o[2] > o[0] and o[3] > o[1]]
        s = Sample(track_id, np.ascontiguousarray(image[py1:py2, px1:px2]), local(box), oth, time.monotonic())
        with self._lock:
            self._pending[track_id] = s
        self.stats["submitted"] += 1
        self._wake.set()
        return True

    def results(self) -> list[tuple[int, dict]]:
        items = []
        while self.out:
            items.append(self.out.popleft())
        return items

    def drop(self, track_id: int) -> None:
        """The person has left: forget their pending picture and turn count."""
        with self._lock:
            self._pending.pop(track_id, None)
            self._looks.pop(track_id, None)
            self._backs.pop(track_id, None)

    # ------------------------------------------------------------ worker side
    def _next(self) -> Sample | None:
        now = time.monotonic()
        with self._lock:
            for k in [k for k, s in self._pending.items() if now - s.t > MAX_WAIT_S]:
                del self._pending[k]
                self.stats["stale"] += 1
            if not self._pending:
                return None
            def turn(k):  # fair turns: whoever has been analysed least, the larger figure first
                b = self._pending[k].box
                return self._looks.get(k, 0), -(b[3] - b[1])

            tid = min(self._pending, key=turn)
            self._looks[tid] = self._looks.get(tid, 0) + 1
            return self._pending.pop(tid)

    def _run(self) -> None:
        while not self._stop.is_set():
            s = self._next()
            if s is None:
                self._wake.wait(0.5)
                self._wake.clear()
                continue
            t0 = time.perf_counter()
            obs = None
            try:
                face = self.backend.find_face(s)
                if face is None and self._backs.get(s.track_id, 0) >= BACK_LOOKS:
                    self.stats["skipped"] += 1  # enough of their back: save the CPU for a face
                    continue
                obs = self.backend.estimate(s, face)
            except Exception:  # a model error must never take the counting down
                log.exception("yaş/cinsiyet analizi hatası")
            dt = time.perf_counter() - t0
            self.stats["analysed"] += 1
            self.stats["ms"] = 0.9 * self.stats["ms"] + 0.1 * dt * 1000 if self.stats["ms"] else dt * 1000
            if obs:
                src = obs.get("src")
                self.stats["faces" if src == "face" else "backs" if src == "back" else "bodies"] += 1
                if src == "back":
                    with self._lock:
                        self._backs[s.track_id] = self._backs.get(s.track_id, 0) + 1
                self.out.append((s.track_id, obs))
            time.sleep(max(0.0, self.period - dt))

    def close(self) -> None:
        self._stop.set()
        self._wake.set()


BACKENDS: dict[str, type] = {}  # name → backend class with .available(cfg) and .from_config(cfg)
AUTO_ORDER = ("mivolo", "insightface")  # most accurate first


def register(cls):
    BACKENDS[cls.name] = cls
    return cls


def create_analyzer(cfg) -> DemographicsAnalyzer:
    d = cfg["demographics"]
    want = str(d.get("model", "auto"))
    order = [n for n in AUTO_ORDER if n in BACKENDS] if want == "auto" else [want]
    for name in order:
        cls = BACKENDS.get(name)
        if cls is None:
            raise RuntimeError(f"Bilinmeyen yaş/cinsiyet modeli: {name}")
        ok, why = cls.available(cfg)
        if ok:
            return DemographicsAnalyzer(cls.from_config(cfg), max_rate=float(d.get("max_rate", 4.0)))
        log.info("Yaş/cinsiyet modeli %s kullanılamıyor: %s", name, why)
    raise RuntimeError("Yaş/cinsiyet modeli dosyaları yok (models/ klasörü, bkz. docs)")


# ------------------------------------------------------------------ backends
def _model_dir(cfg):
    from ..camera.models import model_dir

    return model_dir(cfg)


def _softmax2(a: np.ndarray) -> np.ndarray:
    a = np.asarray(a, dtype=np.float64)
    if a.min() >= 0 and abs(a.sum() - 1.0) < 1e-3:
        return a
    e = np.exp(a - a.max())
    return e / e.sum()


def _ort_session(path, threads: int):
    import onnxruntime as ort

    so = ort.SessionOptions()
    so.intra_op_num_threads = max(1, int(threads))  # the camera loop keeps the other cores
    so.inter_op_num_threads = 1
    return ort.InferenceSession(str(path), sess_options=so, providers=["CPUExecutionProvider"])


def _check_runtime() -> str:
    try:
        import cv2
        import onnxruntime  # noqa: F401
    except ImportError as e:
        return str(e)
    if not hasattr(cv2, "FaceDetectorYN"):
        return "OpenCV 4.5.4+ gerekli"
    return ""


class FaceFinder:
    """OpenCV YuNet: faces in the upper part of a person picture (with 5 landmarks)."""

    FILE = "face_detection_yunet_2023mar.onnx"
    MIN_FACE_PX = 18

    def __init__(self, path: str):
        import cv2

        self.cv2 = cv2
        self.det = cv2.FaceDetectorYN.create(path, "", (160, 160), score_threshold=0.7, nms_threshold=0.3, top_k=5)

    def find(self, person_bgr: np.ndarray):
        """Best face as (x, y, w, h, score, frontal 0..1) in picture pixels, or None."""
        cv2 = self.cv2
        h, w = person_bgr.shape[:2]
        top = person_bgr[: max(8, int(h * 0.5))]  # heads are in the upper half of a standing person
        scale = 1.0
        if top.shape[1] < 192:  # small pictures: enlarge so faces of ~20 px are found
            scale = 192.0 / top.shape[1]
            top = cv2.resize(top, None, fx=scale, fy=scale, interpolation=cv2.INTER_LINEAR)
        self.det.setInputSize((top.shape[1], top.shape[0]))
        _, faces = self.det.detect(top)
        if faces is None or len(faces) == 0:
            return None
        f = max(faces, key=lambda r: r[2] * r[3] * r[14])
        x, y, fw, fh = (float(v) / scale for v in f[:4])
        if min(fw, fh) < self.MIN_FACE_PX:
            return None
        # how frontal: the nose between the eyes (1 = straight on, ~0 = profile)
        (rx, _), (lx, _), (nx, _) = f[4:6], f[6:8], f[8:10]
        span = abs(lx - rx) or 1.0
        frontal = float(max(0.0, 1.0 - abs((nx - (rx + lx) / 2) / span) * 2))
        return x, y, fw, fh, float(f[14]), frontal


def _bgr(s: Sample) -> np.ndarray:
    if s.bgr is None:
        import cv2

        s.bgr = cv2.cvtColor(s.crop, cv2.COLOR_RGB2BGR)
    return s.bgr


@register
class MivoloAgeGender:
    """Face and figure together → age and gender: MiVOLO v2 (a VOLO vision transformer, 384×384,
    ONNX Runtime; huggingface.co/iitolstykh/mivolo_v2, Apache-2.0 per its model card), with OpenCV
    YuNet finding the face. ~1.2 s per person on a Pi 5 with two cores (int8); people are taken in turns.
    Export the model with `python -m pharmacam.tools.export_mivolo` on a development machine."""

    name = "mivolo"
    FILES = ("mivolo_v2_384_int8.onnx", "mivolo_v2_384.onnx")  # either one; int8 is ~1.5x faster
    min_person_px = 130  # below this the figure says too little
    FACE_PX = 32  # smaller faces mislead more than they help: the figure alone is then better
    FACE_SCALE = 1.4  # face crop: the detected box with some hair and chin around it
    BACK_SHIFT = 0.6  # log-odds toward "woman" when seen from behind (the model's measured bias)
    SIZE = 384
    MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
    STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

    @classmethod
    def _file(cls, cfg):
        d = _model_dir(cfg)
        for f in cls.FILES:
            if (d / f).exists():
                return d / f
        return None

    @classmethod
    def available(cls, cfg) -> tuple[bool, str]:
        d = _model_dir(cfg)
        if not (d / FaceFinder.FILE).exists():
            return False, "eksik dosya: " + FaceFinder.FILE
        if cls._file(cfg) is None:
            return False, "eksik dosya: " + " / ".join(cls.FILES)
        why = _check_runtime()
        return (not why), why

    @classmethod
    def from_config(cls, cfg):
        return cls(_model_dir(cfg), cls._file(cfg), int(cfg["demographics"].get("threads", 2)))

    def __init__(self, d, path, threads: int = 2):
        import cv2

        self.cv2 = cv2
        self.faces = FaceFinder(str(d / FaceFinder.FILE))
        self.sess = _ort_session(path, threads)
        self.inp = self.sess.get_inputs()[0].name
        meta = self.sess.get_modelmeta().custom_metadata_map
        self.age_span = float(meta.get("max_age", 122)) - float(meta.get("min_age", 0))
        self.age_mid = float(meta.get("avg_age", 61))
        self.black = self._prep(None)
        log.info("MiVOLO v2 yüklendi: %s", path.name)

    def _prep(self, bgr):
        """Letterbox to 384×384 on black, RGB, ImageNet normalisation, CHW — as the model was trained."""
        cv2, S = self.cv2, self.SIZE
        if bgr is None or bgr.size == 0:
            img = np.zeros((S, S, 3), np.uint8)
        else:
            h, w = bgr.shape[:2]
            r = min(S / h, S / w)
            nw, nh = max(1, int(round(w * r))), max(1, int(round(h * r)))
            img = cv2.resize(bgr, (nw, nh), interpolation=cv2.INTER_LINEAR)
            dw, dh = (S - nw) / 2, (S - nh) / 2
            img = cv2.copyMakeBorder(img, int(round(dh - 0.1)), int(round(dh + 0.1)), int(round(dw - 0.1)),
                                     int(round(dw + 0.1)), cv2.BORDER_CONSTANT, value=(0, 0, 0))
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        return ((rgb - self.MEAN) / self.STD).transpose(2, 0, 1)

    def _body(self, s: Sample):
        """The person's own box, other overlapping people blacked out (as MiVOLO was trained)."""
        x1, y1, x2, y2 = s.box
        body = _bgr(s)[y1:y2, x1:x2].copy()
        ch, cw = body.shape[:2]
        if ch < 8 or cw < 8:
            return None
        for ox1, oy1, ox2, oy2 in s.others:
            a, b, c, d = max(ox1 - x1, 0), max(oy1 - y1, 0), min(ox2 - x1, cw), min(oy2 - y1, ch)
            if c <= a or d <= b:
                continue
            a, b = (0 if a / cw < 0.3 else a), (0 if b / ch < 0.3 else b)  # snap to the edges like MiVOLO
            c, d = (cw if (cw - c) / cw < 0.3 else c), (ch if (ch - d) / ch < 0.3 else d)
            body[b:d, a:c] = 0
        if np.count_nonzero(body) < 0.4 * body.size:
            return None  # mostly someone else
        return body

    def _run(self, face_bgr, body_bgr) -> tuple[float, float]:
        x = np.concatenate([self._prep(face_bgr) if face_bgr is not None else self.black,
                            self._prep(body_bgr) if body_bgr is not None else self.black])[None]
        head = self.sess.run(None, {self.inp: x})[0][0]
        p = _softmax2(head[:2])  # [male, female]
        return float(p[1]), float(np.clip(head[2] * self.age_span + self.age_mid, 1, 95))

    def find_face(self, s: Sample):
        return self.faces.find(_bgr(s))

    def estimate(self, s: Sample, face) -> dict | None:
        body = self._body(s)
        if body is None:
            return None
        h = s.box[3] - s.box[1]
        fpx = min(face[2], face[3]) if face is not None else 0.0
        if fpx >= self.FACE_PX:
            x, y, fw, fh = face[:4]
            cx, cy, hw, hh = x + fw / 2, y + fh / 2, fw * self.FACE_SCALE / 2, fh * self.FACE_SCALE / 2
            bgr = _bgr(s)
            fc = bgr[max(0, int(cy - hh)):int(cy + hh), max(0, int(cx - hw)):int(cx + hw)]
            p, age = self._run(fc, body)
            w = 0.6 + 0.4 * float(np.clip((fpx - self.FACE_PX) / 20, 0, 1))
            return {"female": p, "age": age, "w": round(w, 3), "aw": round(w, 3), "src": "face"}
        size = float(np.clip((h - self.min_person_px) / 170, 0, 1))  # 130 px → 0, 300 px+ → 1
        if size <= 0:
            return None
        p, age = self._run(None, body)
        if face is not None:  # facing us, face too small for the face branch
            return {"female": p, "age": age, "w": round(0.8 * size, 3), "aw": round(0.3 * size, 3), "src": "body"}
        z = np.log(max(p, 1e-4) / max(1 - p, 1e-4)) + self.BACK_SHIFT
        return {"female": float(1 / (1 + np.exp(-z))), "age": age, "w": round(0.35 * size, 3),
                "aw": round(0.15 * size, 3), "src": "back"}


@register
class InsightFaceAgeGender:
    """Face → age and gender: OpenCV YuNet finds the face, InsightFace's genderage network (ONNX Runtime)
    estimates. Light (a few ms per face) but weak on small CCTV faces (it calls most women men there).
    Licence: InsightFace pretrained models are for non-commercial research — fallback only."""

    name = "insightface"
    FILES = (FaceFinder.FILE, "genderage.onnx")
    min_person_px = MIN_PERSON_PX

    @classmethod
    def available(cls, cfg) -> tuple[bool, str]:
        d = _model_dir(cfg)
        missing = [f for f in cls.FILES if not (d / f).exists()]
        if missing:
            return False, "eksik dosya: " + ", ".join(missing)
        why = _check_runtime()
        return (not why), why

    @classmethod
    def from_config(cls, cfg):
        return cls(_model_dir(cfg), int(cfg["demographics"].get("threads", 2)))

    def __init__(self, d, threads: int = 1):
        import cv2

        self.cv2 = cv2
        self.faces = FaceFinder(str(d / FaceFinder.FILE))
        self.sess = _ort_session(d / "genderage.onnx", 1)
        self.inp = self.sess.get_inputs()[0].name

    def find_face(self, s: Sample):
        return self.faces.find(_bgr(s))

    def estimate(self, s: Sample, face) -> dict | None:
        if face is None:
            return None
        cv2, bgr = self.cv2, _bgr(s)
        x, y, fw, fh, score, frontal = face
        cx, cy = x + fw / 2, y + fh / 2
        k = 96.0 / (max(fw, fh) * 1.5)  # the face and some context, like InsightFace aligns it
        M = np.float32([[k, 0, 48 - cx * k], [0, k, 48 - cy * k]])
        aimg = cv2.warpAffine(bgr, M, (96, 96), borderValue=0)
        blob = cv2.dnn.blobFromImage(aimg, 1.0, (96, 96), (0, 0, 0), swapRB=True)  # normalisation is in the model
        pred = self.sess.run(None, {self.inp: blob})[0][0]
        p = _softmax2(pred[:2])
        size_w = float(np.clip((min(fw, fh) - 14) / 36, 0.2, 1.0))  # bigger faces are more reliable
        w = size_w * (0.4 + 0.6 * frontal) * min(1.0, score + 0.1)
        return {"female": float(p[0]), "age": float(np.clip(pred[2] * 100, 1, 99)), "w": round(w, 3), "src": "face"}
