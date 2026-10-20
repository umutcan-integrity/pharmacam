"""Device runtime: camera loop, watchdog, background jobs and the web server."""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

import numpy as np

from . import timeutil
from .analytics.engine import AnalyticsEngine
from .camera import open_source
from .config import Config
from .storage.db import Store

log = logging.getLogger("pharmacam")

BACKGROUND_EVERY_S = 600


class Runtime:
    """Everything the API needs to know about the running device."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        timeutil.set_timezone(cfg["device"]["timezone"])
        self.store = Store(cfg.resolve(cfg["storage"]["path"]))
        self.engine = AnalyticsEngine(cfg, self.store)
        self.data_dir = cfg.resolve(cfg["storage"]["path"]).parent
        self.background_path = self.data_dir / "background.jpg"
        self.started_at = time.time()
        self.camera_error: str | None = None
        self.camera_opened_at: float | None = None
        self.last_frame_at: float = 0.0
        self.frames = 0
        self.sync_status: dict = {"enabled": bool(cfg["sync"]["enabled"])}
        self._stop = threading.Event()
        self._source = None
        self._empty_since: float | None = None
        self._last_bg = 0.0

    # ----------------------------------------------------------- camera loop
    def camera_loop(self) -> None:
        retry = 5.0
        while not self._stop.is_set():
            try:
                self._source = open_source(self.cfg)
                self.camera_opened_at = time.time()
                self.camera_error = None
                retry = 5.0
                for frame in self._source.frames():
                    if self._stop.is_set():
                        break
                    live = self.engine.process(frame)
                    self.frames += 1
                    self.last_frame_at = time.time()
                    self._maybe_save_background(frame.image, live.visible_customers + live.staff_on_duty, frame.ts)
            except Exception as e:  # camera missing, cable fault, libcamera error...
                self.camera_error = str(e) or e.__class__.__name__
                log.error("Kamera hatası: %s — %ds sonra yeniden denenecek", self.camera_error, retry)
            finally:
                self.engine.live.camera_ok = False
                if self._source is not None:
                    self._source.close()
                    self._source = None
            if self._stop.wait(retry):
                break
            retry = min(retry * 2, 60.0)

    def _maybe_save_background(self, image: np.ndarray | None, people: int, ts: float) -> None:
        """Keep a people-free picture of the store for heatmaps and the zone editor."""
        if image is None:
            return
        if people > 0:
            self._empty_since = None
            if not self.background_path.exists() and ts - self._last_bg > 60:
                # no empty frame yet: store one with people blurred so the editor has a picture
                from .render import blur_boxes
                self._write_jpeg(blur_boxes(image, [t["box"] for t in self.engine.live.tracks]), self.background_path)
                self._last_bg = ts
            return
        self._empty_since = self._empty_since or ts
        if ts - self._empty_since >= 5 and ts - self._last_bg >= BACKGROUND_EVERY_S:
            self._write_jpeg(image, self.background_path)
            self._last_bg = ts

    @staticmethod
    def _write_jpeg(image: np.ndarray, path: Path) -> None:
        from .analytics.heatmap import encode_jpeg

        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(encode_jpeg(image, 85))
        os.replace(tmp, path)

    def background(self) -> np.ndarray | None:
        import cv2

        if self.background_path.exists():
            img = cv2.imread(str(self.background_path))
            if img is not None:
                return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        return None

    # -------------------------------------------------------------- watchdog
    def watchdog_loop(self) -> None:
        limit = float(self.cfg["camera"]["watchdog_seconds"])
        under_systemd = bool(os.environ.get("INVOCATION_ID"))
        while not self._stop.wait(2.0):
            if self.camera_opened_at is None or self.camera_error:
                continue
            stale = time.time() - max(self.last_frame_at, self.camera_opened_at)
            if stale > limit + 60:  # first frames can take a while (network firmware upload)
                self.engine.live.camera_ok = False
                self.camera_error = f"{int(stale)} sn boyunca kameradan kare gelmedi"
                log.critical("%s", self.camera_error)
                if under_systemd and self.last_frame_at:
                    # libcamera can hang inside a blocking call; a clean process restart is
                    # the only reliable recovery. systemd brings us back in a few seconds.
                    self.engine.flush()
                    os._exit(3)

    # ---------------------------------------------------------- daily jobs
    def jobs_loop(self) -> None:
        from .reports.daily import generate_report

        rcfg = self.cfg["reports"]
        report_dir = self.cfg.resolve(rcfg["dir"])
        # catch up on missing reports (device was off at closing time)
        time.sleep(5)
        for day in self.store.days_with_data()[-7:]:
            if day < timeutil.today() and not self.store.one("SELECT 1 FROM reports WHERE day=?", (day,)):
                self._safe_report(generate_report, day, report_dir)
        last_prune = 0.0
        while not self._stop.wait(30.0):
            now = time.time()
            today = timeutil.today()
            hour = timeutil.local(now).hour
            if hour >= int(rcfg["closing_hour"]) and not self.store.one("SELECT 1 FROM reports WHERE day=?", (today,)):
                self.engine.flush()
                self._safe_report(generate_report, today, report_dir)
            if now - last_prune > 86400:
                self.store.prune(int(self.cfg["storage"]["retention_days"]))
                last_prune = now

    def _safe_report(self, fn, day: str, report_dir: Path) -> None:
        try:
            path = fn(self.store, self.cfg, day, report_dir, background=self.background())
            log.info("Günlük rapor oluşturuldu: %s", path)
        except Exception:
            log.exception("Rapor oluşturulamadı: %s", day)

    # ---------------------------------------------------------------- status
    def health(self) -> dict:
        live = self.engine.live
        return {
            "device_id": self.cfg["device"]["id"],
            "pharmacy": self.cfg["device"]["pharmacy_name"],
            "uptime_s": round(time.time() - self.started_at),
            "camera_ok": bool(live.camera_ok and time.time() - self.last_frame_at < 5),
            "camera_error": self.camera_error,
            "camera_source": self.cfg["camera"]["source"],
            "model": self.model_info(),
            "fps": live.fps,
            "video_fps": live.video_fps,
            "frames": self.frames,
            "last_frame_age_s": round(time.time() - self.last_frame_at, 1) if self.last_frame_at else None,
            "sync": self.sync_status,
            "system": system_stats(),
        }

    def model_info(self) -> dict | None:
        if self.cfg["camera"]["source"] != "imx500":
            return None
        from .camera import models

        return models.describe(self.cfg, getattr(self._source, "model", None))

    def start_background(self) -> None:
        threading.Thread(target=self.camera_loop, name="camera", daemon=True).start()
        threading.Thread(target=self.watchdog_loop, name="watchdog", daemon=True).start()
        threading.Thread(target=self.jobs_loop, name="jobs", daemon=True).start()
        if self.cfg["sync"]["enabled"]:
            from .sync.client import SyncClient

            threading.Thread(target=SyncClient(self).loop, name="sync", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        self.engine.flush()


def system_stats() -> dict:
    out: dict = {}
    try:
        out["cpu_temp_c"] = round(int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000, 1)
    except Exception:
        pass
    try:
        out["load"] = round(os.getloadavg()[0], 2)
    except Exception:
        pass
    try:
        st = os.statvfs("/")
        out["disk_free_gb"] = round(st.f_bavail * st.f_frsize / 1e9, 1)
    except Exception:
        pass
    return out


def run(cfg: Config) -> None:
    import uvicorn

    from .api.app import create_app

    rt = Runtime(cfg)
    rt.start_background()
    app = create_app(rt)
    s = cfg["server"]
    log.info("PharmaCam paneli: http://%s:%s", s["host"], s["port"])
    try:
        uvicorn.run(app, host=s["host"], port=int(s["port"]), log_level="warning")
    finally:
        rt.stop()
