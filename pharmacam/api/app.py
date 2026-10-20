"""Device REST API and dashboard.

Everything the dashboard shows comes from these endpoints, so third parties
(eczane yazılımları, BI araçları) can use the same API. Interactive docs are
served at /docs.
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import secrets
import time
from pathlib import Path
from typing import Any

from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles

from .. import __version__, timeutil
from ..analytics import forecast as fc
from ..analytics import heatmap as hm
from ..analytics import queries as q

STATIC = Path(__file__).parent / "static"
DAY_RE = r"^\d{4}-\d{2}-\d{2}$"
EDITABLE_SETTINGS = {
    "staff": {"enabled", "white_ratio_threshold", "staff_zone_ratio"},
    "analytics": {"occupancy_mode", "min_zone_dwell_seconds", "min_queue_seconds"},
    "server": {"live_view", "blur_people"},
    "reports": {"opening_hour", "closing_hour"},
    "device": {"pharmacy_name"},
}


def create_app(rt) -> FastAPI:
    cfg, store, engine = rt.cfg, rt.store, rt.engine
    app = FastAPI(title="PharmaCam API", version=__version__,
                  description="Eczane içi yapay zekâ kamera analitiği — cihaz API'si")
    security = HTTPBasic(auto_error=False)

    def auth(creds: HTTPBasicCredentials | None = Depends(security)) -> None:
        s = cfg["server"]
        if not s.get("password"):
            return
        ok = creds is not None and secrets.compare_digest(creds.username, str(s["username"])) \
            and secrets.compare_digest(creds.password, str(s["password"]))
        if not ok:
            raise HTTPException(401, "Yetkisiz", headers={"WWW-Authenticate": 'Basic realm="PharmaCam"'})

    def day_or_today(day: str | None) -> str:
        return day or timeutil.today()

    def range_or_default(start: str | None, end: str | None, days: int = 7) -> tuple[str, str]:
        end = end or timeutil.today()
        start = start or timeutil.shift_day(end, -(days - 1))
        if start > end:
            raise HTTPException(400, "start, end'den sonra olamaz")
        return start, end

    zone_names = lambda: {z.id: z.name for z in cfg.zones}  # noqa: E731

    # ------------------------------------------------------------- status
    @app.get("/api/health", tags=["Durum"])
    def health():
        return rt.health()

    @app.get("/api/info", tags=["Durum"], dependencies=[Depends(auth)])
    def info():
        return {
            "version": __version__,
            "device": cfg["device"],
            "zones": [z.to_dict() for z in cfg.zones],
            "days": store.days_with_data(),
            "today": timeutil.today(),
            "live_view": bool(cfg["server"]["live_view"]),
            "blur_people": bool(cfg["server"]["blur_people"]),
            "opening_hour": cfg["reports"]["opening_hour"],
            "closing_hour": cfg["reports"]["closing_hour"],
        }

    # --------------------------------------------------------------- live
    @app.get("/api/live", tags=["Canlı"], dependencies=[Depends(auth)])
    def live():
        snap = engine.snapshot()
        snap["camera_ok"] = rt.health()["camera_ok"]
        snap["zone_names"] = zone_names()
        return snap

    def _live_jpeg(raw: bool = False, max_w: int | None = None, quality: int = 78) -> bytes | None:
        import cv2

        from ..render import annotate, blur_boxes

        img = engine.last_frame
        if img is None:
            return None
        snap = engine.snapshot()
        if raw:  # no drawings (the dashboard draws its own overlay); people blurred if configured
            out = blur_boxes(img, [t["box"] for t in snap["tracks"]]) if cfg["server"]["blur_people"] else img
        else:
            out = annotate(img, snap, cfg.zones, blur=bool(cfg["server"]["blur_people"]))
        h, w = out.shape[:2]
        if max_w and w > max_w:
            out = cv2.resize(out, (max_w, round(h * max_w / w)), interpolation=cv2.INTER_AREA)
        return hm.encode_jpeg(out, quality)

    @app.get("/api/live.jpg", tags=["Canlı"], dependencies=[Depends(auth)])
    def live_jpg(raw: bool = False):
        """Current frame (never stored). `raw=true`: no drawings — the panel overlays zones and people itself."""
        if not cfg["server"]["live_view"]:
            raise HTTPException(403, "Canlı görüntü kapalı")
        data = _live_jpeg(raw)
        if data is None:
            raise HTTPException(503, "Henüz görüntü yok")
        return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/api/live.mjpg", tags=["Canlı"], dependencies=[Depends(auth)])
    async def live_mjpg(request: Request, raw: bool = False, fps: float = Query(15, gt=0, le=30)):
        """Continuous MJPEG stream (never stored): every new camera frame, at most `fps` per second.
        `raw=true`: no drawings, 960 px wide — what the panel shows under its own overlay."""
        if not cfg["server"]["live_view"]:
            raise HTTPException(403, "Canlı görüntü kapalı")

        async def gen():
            last_ts, sent_at, gap = None, 0.0, 0.9 / fps
            while not await request.is_disconnected():
                ts = engine.last_frame_ts
                # check often (cheap) so no camera frame is missed, send at most `fps` per second,
                # and only frames the camera actually produced — never duplicates
                if ts != last_ts and time.monotonic() - sent_at >= gap:
                    last_ts, sent_at = ts, time.monotonic()
                    data = await asyncio.to_thread(_live_jpeg, raw, 960 if raw else None, 72 if raw else 78)
                    if data:
                        yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                               + str(len(data)).encode() + b"\r\n\r\n" + data + b"\r\n")
                await asyncio.sleep(0.008)

        return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @app.get("/api/live/events", tags=["Canlı"], dependencies=[Depends(auth)])
    async def live_events(request: Request):
        """Server-Sent Events: the full live snapshot (people included) on every processed frame,
        up to 15 per second, plus a heartbeat each second while the camera is idle."""

        async def gen():
            last_ts, last_sent = None, 0.0
            while not await request.is_disconnected():
                ts, now = engine.last_frame_ts, time.monotonic()
                if ts != last_ts or now - last_sent >= 1.0:
                    last_ts, last_sent = ts, now
                    yield f"data: {json.dumps(live(), default=float, separators=(',', ':'))}\n\n"
                await asyncio.sleep(1 / 15)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @app.get("/api/stream", tags=["Canlı"], dependencies=[Depends(auth)])
    async def stream(request: Request):
        """Server-Sent Events: live metrics once per second."""

        async def gen():
            while not await request.is_disconnected():
                snap = engine.snapshot()
                snap.pop("tracks", None)
                yield f"data: {json.dumps(snap)}\n\n"
                await asyncio.sleep(1.0)

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.get("/api/snapshot.jpg", tags=["Bölgeler"], dependencies=[Depends(auth)])
    def snapshot(zones: bool = False):
        from ..render import blur_boxes, draw_zones

        img = rt.background()
        if img is None and engine.last_frame is not None:
            img = blur_boxes(engine.last_frame, [t["box"] for t in engine.live.tracks])
        if img is None:
            raise HTTPException(404, "Görüntü yok")
        if zones:
            img = draw_zones(img.copy(), cfg.zones)
        return Response(hm.encode_jpeg(img, 85), media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    # ----------------------------------------------------------- analytics
    @app.get("/api/summary", tags=["Analiz"], dependencies=[Depends(auth)])
    def summary(day: str | None = Query(None, pattern=DAY_RE)):
        d = day_or_today(day)
        s = q.summary(store, d)
        s["top_zone_name"] = zone_names().get(s["top_zone"], s["top_zone"])
        _, s["entries_same_day_last_week"] = q.comparable_entries(store, d, timeutil.shift_day(d, -7))
        return s

    @app.get("/api/hourly", tags=["Analiz"], dependencies=[Depends(auth)])
    def hourly(day: str | None = Query(None, pattern=DAY_RE)):
        return q.hourly(store, day_or_today(day))

    @app.get("/api/minutes", tags=["Analiz"], dependencies=[Depends(auth)])
    def minutes(day: str | None = Query(None, pattern=DAY_RE)):
        return q.minutes(store, day_or_today(day))

    @app.get("/api/daily", tags=["Analiz"], dependencies=[Depends(auth)])
    def daily(start: str | None = Query(None, pattern=DAY_RE), end: str | None = Query(None, pattern=DAY_RE)):
        return q.daily(store, *range_or_default(start, end, 30))

    @app.get("/api/zones/stats", tags=["Raflar"], dependencies=[Depends(auth)])
    def zones_stats(start: str | None = Query(None, pattern=DAY_RE), end: str | None = Query(None, pattern=DAY_RE)):
        s, e = range_or_default(start, end, 7)
        rows = q.zone_stats(store, s, e, zone_names())
        if e == timeutil.today():  # include the current, not yet flushed hour
            cur = engine.current_zone_hour()
            by = {r["zone"]: r for r in rows}
            for zid, st in cur.items():
                r = by.get(zid)
                if r is None:
                    continue
                r["stops"] += st["visits"]
                r["passes"] += st["passes"]
        types = {z.id: z.type for z in cfg.zones}
        return [r for r in rows if types.get(r["zone"], "shelf") == "shelf"]

    @app.get("/api/zones/hourly", tags=["Raflar"], dependencies=[Depends(auth)])
    def zones_hourly(day: str | None = Query(None, pattern=DAY_RE)):
        return q.zone_hourly(store, day_or_today(day))

    @app.get("/api/queue", tags=["Sıra"], dependencies=[Depends(auth)])
    def queue(day: str | None = Query(None, pattern=DAY_RE)):
        return q.queue_analysis(store, day_or_today(day))

    @app.get("/api/staff", tags=["Personel"], dependencies=[Depends(auth)])
    def staff(day: str | None = Query(None, pattern=DAY_RE)):
        return q.staff_summary(store, day_or_today(day))

    @app.get("/api/visits", tags=["Ziyaretler"], dependencies=[Depends(auth)])
    def visits(day: str | None = Query(None, pattern=DAY_RE), limit: int = Query(200, le=5000),
               include_staff: bool = True):
        return q.visits(store, day_or_today(day), limit, include_staff)

    @app.get("/api/export/visits.csv", tags=["Ziyaretler"], dependencies=[Depends(auth)])
    def export_visits(start: str | None = Query(None, pattern=DAY_RE), end: str | None = Query(None, pattern=DAY_RE)):
        s, e = range_or_default(start, end, 30)
        rows = store.query("SELECT * FROM visits WHERE day BETWEEN ? AND ? ORDER BY started_at", (s, e))
        buf = io.StringIO()
        if rows:
            w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        return Response(buf.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="ziyaretler_{s}_{e}.csv"'})

    @app.get("/api/heatmap", tags=["Isı Haritası"], dependencies=[Depends(auth)])
    def heatmap(start: str | None = Query(None, pattern=DAY_RE), end: str | None = Query(None, pattern=DAY_RE),
                format: str = Query("png", pattern="^(png|json)$")):
        s, e = range_or_default(start, end, 1)
        grid = store.heatmap(s, e)
        if e == timeutil.today():
            cur = engine.current_heatmap()
            grid = cur if grid is None else (grid + cur if grid.shape == cur.shape else grid)
        if format == "json":
            return {"start": s, "end": e, "grid": None if grid is None else hm.normalize(grid).round(3).tolist()}
        img = hm.render(grid, rt.background())
        return Response(hm.encode_png(img), media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.get("/api/insights", tags=["Analiz"], dependencies=[Depends(auth)])
    def insights(day: str | None = Query(None, pattern=DAY_RE)):
        from ..analytics.insights import day_insights

        return day_insights(store, cfg, day_or_today(day))

    @app.get("/api/forecast", tags=["Tahmin"], dependencies=[Depends(auth)])
    def forecast(days: int = Query(7, ge=1, le=28)):
        f = fc.forecast(store, days)
        s = q.summary(store, timeutil.shift_day(timeutil.today(), -1))
        svc = s["avg_service_s"] or 90.0
        for d in f["days"]:
            for h in d["hours"]:
                h["staff_needed"] = fc.staffing_hint(h["expected"], svc) if h["expected"] >= 0.5 else 0
        return f

    # ------------------------------------------------------------- reports
    @app.get("/api/reports", tags=["Raporlar"], dependencies=[Depends(auth)])
    def reports():
        return store.query("SELECT day, created_at FROM reports ORDER BY day DESC")

    @app.get("/api/reports/{day}.pdf", tags=["Raporlar"], dependencies=[Depends(auth)])
    def report_pdf(day: str):
        r = store.one("SELECT path FROM reports WHERE day=?", (day,))
        if not r or not Path(r["path"]).exists():
            raise HTTPException(404, "Rapor yok")
        return FileResponse(r["path"], media_type="application/pdf", filename=f"PharmaCam_{day}.pdf")

    @app.post("/api/reports/{day}", tags=["Raporlar"], dependencies=[Depends(auth)])
    def make_report(day: str):
        from ..reports.daily import generate_report

        if not timeutil_day_ok(day):
            raise HTTPException(400, "Geçersiz tarih")
        if day == timeutil.today():
            engine.flush()
        path = generate_report(store, cfg, day, cfg.resolve(cfg["reports"]["dir"]), background=rt.background())
        return {"day": day, "path": str(path)}

    # -------------------------------------------------------------- config
    @app.get("/api/config/zones", tags=["Bölgeler"], dependencies=[Depends(auth)])
    def get_zones():
        return [z.to_dict() for z in cfg.zones]

    @app.put("/api/config/zones", tags=["Bölgeler"], dependencies=[Depends(auth)])
    def put_zones(zones: list[dict[str, Any]] = Body(...)):
        try:
            cfg.set_zones(zones)
        except (ValueError, KeyError, TypeError) as e:
            raise HTTPException(400, str(e))
        cfg.save()
        engine.reload_zones()
        return [z.to_dict() for z in cfg.zones]

    @app.get("/api/config/settings", tags=["Ayarlar"], dependencies=[Depends(auth)])
    def get_settings():
        return {sec: {k: cfg[sec][k] for k in keys} for sec, keys in EDITABLE_SETTINGS.items()}

    @app.patch("/api/config/settings", tags=["Ayarlar"], dependencies=[Depends(auth)])
    def patch_settings(body: dict[str, dict[str, Any]] = Body(...)):
        for sec, vals in body.items():
            allowed = EDITABLE_SETTINGS.get(sec)
            if allowed is None or not set(vals) <= allowed:
                raise HTTPException(400, f"Düzenlenemez ayar: {sec}")
            for k, v in vals.items():
                old = cfg[sec][k]
                if isinstance(old, bool):
                    v = bool(v)
                elif isinstance(old, (int, float)) and not isinstance(v, (int, float)):
                    raise HTTPException(400, f"{sec}.{k} sayı olmalı")
                cfg[sec][k] = v
        # live-tunable parameters
        engine.staff.enabled = cfg["staff"]["enabled"]
        engine.staff.threshold = float(cfg["staff"]["white_ratio_threshold"])
        engine.staff.zone_ratio = float(cfg["staff"]["staff_zone_ratio"])
        engine.occ_mode = cfg["analytics"]["occupancy_mode"]
        engine.min_dwell = float(cfg["analytics"]["min_zone_dwell_seconds"])
        engine.min_queue = float(cfg["analytics"]["min_queue_seconds"])
        cfg.save()
        return get_settings()

    # ---------------------------------------------------------- dashboard
    @app.get("/", include_in_schema=False, dependencies=[Depends(auth)])
    def index():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.exception_handler(404)
    async def not_found(request: Request, exc):
        detail = getattr(exc, "detail", "Bulunamadı")
        return JSONResponse({"detail": detail}, status_code=404)

    app.state.started = time.time()
    return app


def timeutil_day_ok(day: str) -> bool:
    import re

    return bool(re.match(DAY_RE, day))
