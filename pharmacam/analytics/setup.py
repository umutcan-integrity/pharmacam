"""Installation assistant: is the camera mounted and set up well enough to count accurately?

The engine feeds this a little of every frame; the panel's "Kurulum" tab asks for:

* live checks — camera and model running, light (too dark, back light from the door glass),
  focus, how sure the detector is about people, people cut by the top of the picture,
  zones drawn — each with what to do about it;
* a door line: a preset for the chosen mounting, or one learnt from where people actually
  appear and disappear in the picture;
* a count audit — the installer taps "in"/"out" for every real person at the door and the
  system's own counts are matched to the taps one by one (results are stored).
"""
from __future__ import annotations

import warnings
from collections import deque
from typing import Any

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

LIGHT_EVERY_S = 2.0
TILES = (8, 6)  # focus is judged tile by tile: each part of the shop against its own best
AUDIT_MATCH_S = 6.0  # a system count this close to a tap is the same person


def _relative(tiles: np.ndarray, ref: np.ndarray) -> float | None:
    """Median over textured, visible tiles of current / reference sharpness, in %, at most 100."""
    ok = ~np.isnan(tiles) & (ref > 20)  # bare walls and floor say nothing about focus
    return float(np.median(np.minimum(1.0, tiles[ok] / ref[ok]))) * 100 if ok.any() else None


class SetupMonitor:
    def __init__(self):
        self.light: dict[str, float] | None = None
        self._light_ts = 0.0
        self.sharp_hist: deque[tuple[float, np.ndarray]] = deque(maxlen=300)  # (ts, tile sharpness) of the last 10 min
        self.conf: deque[float] = deque(maxlen=900)  # detector confidence of tracked people
        self.height_px: deque[float] = deque(maxlen=900)
        self.top_cut: deque[int] = deque(maxlen=900)
        self.people_seen = 0
        self.last_person_ts = 0.0
        self.ends: deque[tuple] = deque(maxlen=400)  # (first foot, last foot) of finished customers

    # ---------------------------------------------------------------- feed
    def observe(self, frame, active, ts: float) -> None:
        img = frame.image
        if img is not None and ts - self._light_ts >= LIGHT_EVERY_S and cv2 is not None:
            self._light_ts = ts
            h, w = img.shape[:2]
            small = cv2.resize(img, (320, max(1, round(h * 320 / w))), interpolation=cv2.INTER_AREA)
            gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
            # Sharpness of the shop itself, tile by tile: people bring edges of their own and cover
            # parts of the scene, so one number for the whole picture would call a quiet moment blurry.
            lap = cv2.Laplacian(gray, cv2.CV_64F)
            sh, sw = gray.shape
            busy = np.zeros(gray.shape, bool)
            for t in active:
                x1, y1, x2, y2 = t.box
                busy[max(0, int(y1 * sh) - 3):int(y2 * sh) + 3, max(0, int(x1 * sw) - 3):int(x2 * sw) + 3] = True
            tiles = np.full(TILES[0] * TILES[1], np.nan)
            for i in range(TILES[1]):
                for j in range(TILES[0]):
                    ys, xs = slice(i * sh // TILES[1], (i + 1) * sh // TILES[1]), slice(j * sw // TILES[0], (j + 1) * sw // TILES[0])
                    if not busy[ys, xs].any():
                        tiles[i * TILES[0] + j] = lap[ys, xs].var()
            self.sharp_hist.append((ts, tiles))
            while self.sharp_hist and ts - self.sharp_hist[0][0] > 600:
                self.sharp_hist.popleft()
            with warnings.catch_warnings():  # tiles nobody has seen free yet are all-NaN
                warnings.simplefilter("ignore", RuntimeWarning)
                # the check: each tile against its usual sharpness (80th percentile of 10 min), which a
                # passer-by does not raise — a lens knocked out of focus pulls every tile down
                pct = _relative(tiles, np.nanpercentile(np.vstack([v for _, v in self.sharp_hist]), 80, axis=0))
                # the bar watched while turning the lens: against the best of the last 2 min
                recent = np.vstack([v for t2, v in self.sharp_hist if ts - t2 <= 120])
                steady = [np.nanmedian(recent[k:k + 3], axis=0) for k in range(max(1, len(recent) - 2))]
                live = _relative(tiles, np.nanmax(np.vstack(steady), axis=0))
            sharp = float((lap[~busy] if (~busy).mean() > 0.3 else lap).var())  # absolute floor for real blur
            self.light = {
                "brightness": float(gray.mean()),
                "clipped": float((gray >= 250).mean()),
                "dark": float((gray <= 12).mean()),
                "sharpness": sharp,
                "focus_pct": round(pct, 0) if pct is not None else None,  # for the check
                "focus_live": round(live, 0) if live is not None else None,  # for the bar while turning the lens
                "ts": ts,
            }
        H = img.shape[0] if img is not None else 960
        for t in active:
            self.conf.append(float(t.score))
            x1, y1, x2, y2 = t.box
            self.height_px.append((y2 - y1) * H)
            self.top_cut.append(1 if y1 < 0.01 and y2 < 0.97 else 0)
        if active:
            self.last_person_ts = ts

    def track_ended(self, t) -> None:
        if t.age >= 3.0 and not t.meta.get("is_staff") and not t.meta.get("phantom"):
            self.people_seen += 1
            self.ends.append((tuple(t.first_foot), tuple(t.foot)))

    # -------------------------------------------------------------- checks
    def checks(self, cfg, health: dict) -> list[dict[str, Any]]:
        """[{key, title, status good|warn|bad|info, value, advice}]"""
        out = []

        def add(key, title, status, value, advice=""):
            out.append({"key": key, "title": title, "status": status, "value": value, "advice": advice})

        fps = health.get("fps") or 0
        # the rate the source should reach: the model's (15 for YOLO11n), else what the source is set to
        src = cfg["camera"]["source"]
        want = (health.get("model") or {}).get("fps") or \
            ((cfg.data.get("simulator") or {}).get("fps", 10) if src == "simulator" else 15)
        if src == "video":
            want = float(cfg["camera"].get("fps") if str(cfg["camera"].get("fps")) != "auto" else 15)
        if not health.get("camera_ok"):
            add("camera", "Kamera", "bad", "görüntü yok",
                "Kamera kablosunu kontrol edin (kilit kapalı mı, temaslar doğru yönde mi), sonra Pi'yi yeniden başlatın.")
        else:
            ok = fps >= 0.8 * float(want)
            add("camera", "Kamera ve analiz hızı", "good" if ok else "warn", f"{fps:.0f} kare/sn",
                "" if ok else "Analiz yavaş: Pi ısınıyor olabilir (soğutucu/fan) ya da başka bir yük var.")
        sysst = health.get("system") or {}
        if "undervoltage_now" in sysst:  # Raspberry Pi only
            if sysst["undervoltage_now"]:
                add("power", "Güç kaynağı", "bad", "voltaj şu an düşük",
                    "Adaptör yetersiz: resmi 27 W (5,1 V / 5 A) Raspberry Pi USB-C adaptörünü kullanın.")
            elif sysst.get("undervoltage_seen"):
                add("power", "Güç kaynağı", "warn", "açılıştan beri düşük voltaj görüldü",
                    "Yoğun anlarda voltaj düşmüş: resmi 27 W (5,1 V / 5 A) USB-C adaptör ve kısa, kaliteli kablo kullanın.")
            else:
                add("power", "Güç kaynağı", "good", "normal")
        m = health.get("model") or {}
        if m:
            add("model", "Yapay zekâ modeli", "warn" if m.get("fallback") else "good", m.get("title", "?"),
                "Seçili model yüklenemedi, yedek model çalışıyor: 'python -m pharmacam models --fetch' çalıştırın."
                if m.get("fallback") else "")
        L = self.light
        if L and health.get("camera_ok"):
            b, clip, dark = L["brightness"], L["clipped"], L["dark"]
            if clip > 0.08:
                add("light", "Işık", "bad" if clip > 0.18 else "warn", f"görüntünün %{clip * 100:.0f}'i patlamış",
                    "Kamera güçlü bir ışığa ya da güneş alan cama bakıyor: açıyı camdan uzaklaştırın ya da kamerayı "
                    "kapının üstüne, içeri bakacak şekilde takın.")
            elif b < 60 or dark > 0.35:
                add("light", "Işık", "warn", f"karanlık (parlaklık {b:.0f}/255)",
                    "Ortam loş: tavan aydınlatmasını açın; gece kullanımında doğruluk düşer.")
            else:
                add("light", "Işık", "good", f"parlaklık {b:.0f}/255")
            # sharpness depends on the scene (a wall of shelves is "sharper" than a bare floor), so it
            # is judged against the best value seen while the lens was turned, plus a floor for real blur
            s, pct = L["sharpness"], L.get("focus_pct") or 100
            st = "bad" if s < 25 else "warn" if pct < 70 or s < 60 else "good"
            add("focus", "Netlik (odak)", st, f"en iyinin %{pct:.0f}'i",
                "" if st == "good" else "Görüntü bulanık: lensi yavaşça çevirin, odak çubuğu en uzun olduğunda bırakın.")
        if len(self.conf) >= 60:
            c = float(np.median(self.conf))
            st = "good" if c >= 0.6 else "warn" if c >= 0.45 else "bad"
            add("confidence", "Kişi tespit güveni", st, f"%{c * 100:.0f}",
                "" if st == "good" else "Kişiler zor seçiliyor: kamera çok uzak, çok dik ya da ters ışıkta olabilir.")
            hp = float(np.median(self.height_px))
            st = "good" if hp >= 110 else "warn" if hp >= 70 else "bad"
            add("size", "Kişi boyu (görüntüde)", st, f"{hp:.0f} piksel",
                "" if st == "good" else "Kişiler görüntüde küçük: kamerayı alana yaklaştırın ya da daha az geniş bir "
                                        "açıyla takın.")
            cut = float(np.mean(self.top_cut))
            if cut > 0.15:
                add("tilt", "Eğim", "warn", f"kişilerin %{cut * 100:.0f}'inin başı kesik",
                    "Kamera fazla aşağı bakıyor: biraz yukarı kaldırın (tezgâhtaki kişilerin başı görünmeli).")
            else:
                add("tilt", "Eğim", "good", "başlar görünüyor")
        else:
            add("people", "Kişi tespiti", "info", "henüz yeterli kişi geçmedi",
                "Birkaç kişi görüş alanında dolaşınca tespit kalitesi ölçülecek.")
        if cfg["demographics"]["enabled"] and cfg["camera"]["source"] != "simulator":
            d = health.get("demographics")
            if not d:
                add("demographics", "Yaş/cinsiyet modeli", "warn", "çalışmıyor",
                    "Model dosyaları eksik: 'python -m pharmacam models --fetch' çıktısındaki adımları izleyin.")
            else:
                title = {"mivolo": "MiVOLO v2 (yüz + beden)", "insightface": "InsightFace (yalnız yüz)"}.get(
                    d["model"], d["model"])
                ms = d.get("ms_per_person") or 0
                done = d.get("analysed", 0)
                add("demographics", "Yaş/cinsiyet modeli", "good" if d["model"] == "mivolo" else "warn",
                    f"{title}" + (f" · kişi başı {ms / 1000:.1f} sn" if done else ""),
                    "" if d["model"] == "mivolo" else "Daha isabetli MiVOLO modeli cihazda yok; yalnız yüzden tahmin "
                                                      "uzak kamerada zayıftır.")
                if done >= 30:
                    seen = d.get("faces", 0) / max(1, done)
                    add("faces", "Yüz görünürlüğü", "good" if seen >= 0.15 else "warn", f"analizlerin %{seen * 100:.0f}'i",
                        "" if seen >= 0.15 else "Müşteriler kameraya çoğunlukla arkasını dönüyor: yaş tahmini zayıflar. "
                                                "Kamerayı yüzleri görecek şekilde (ör. tezgâhın arkasına) takmayı düşünün.")
        zt = {z.type for z in cfg.zones}
        missing = [n for t, n in (("entrance", "giriş çizgisi"), ("queue", "sıra alanı"), ("service", "tezgâh"),
                                  ("shelf", "raf")) if t not in zt]
        add("zones", "Bölgeler", "good" if not missing else "warn" if "entrance" not in missing else "bad",
            "tamam" if not missing else "eksik: " + ", ".join(missing),
            "" if not missing else "'Giriş çizgisi' adımından çizgiyi uygulayın, diğerlerini 'Bölgeler' sekmesinden çizin.")
        return out

    # -------------------------------------------------------- door line
    @staticmethod
    def preset_door(mount: str) -> dict[str, Any]:
        """A starting line for a mounting type (adjust it in the zone editor if needed)."""
        if mount == "above_door":  # looking in from above the door: people appear at the bottom edge
            return {"id": "kapi", "type": "entrance", "name": "Giriş Kapısı",
                    "line": [[0.12, 0.84], [0.88, 0.84]], "inside_point": [0.5, 0.45]}
        if mount == "facing_door":  # on the back wall, the door is in the picture: draw it on the doorway
            return {"id": "kapi", "type": "entrance", "name": "Giriş Kapısı",
                    "line": [[0.35, 0.62], [0.65, 0.62]], "inside_point": [0.5, 0.9]}
        return {"id": "kapi", "type": "entrance", "name": "Giriş Kapısı",  # corner: door near a side
                "line": [[0.05, 0.75], [0.35, 0.95]], "inside_point": [0.5, 0.5]}

    def learnt_door(self, min_samples: int = 10) -> dict[str, Any]:
        """Where do people come into and go out of the picture? Births/deaths near one image edge
        mark the door side; the line runs along that edge, a little inside, across where they cross."""
        pts = [p for a, b in self.ends for p in (a, b)]
        if len(self.ends) < min_samples:
            return {"zone": None, "samples": len(self.ends), "note": f"en az {min_samples} kişinin girip çıkması gerekli"}
        edges = {
            "bottom": [p for p in pts if p[1] > 0.9],
            "top": [p for p in pts if p[1] < 0.1],
            "left": [p for p in pts if p[0] < 0.08],
            "right": [p for p in pts if p[0] > 0.92],
        }
        side, hits = max(edges.items(), key=lambda kv: len(kv[1]))
        share = len(hits) / max(1, len(pts))
        if len(hits) < min_samples or share < 0.3:
            return {"zone": None, "samples": len(self.ends),
                    "note": "insanlar belirli bir kenardan girip çıkmıyor: kapı görüntünün içinde olabilir, çizgiyi elle çizin"}
        if side in ("bottom", "top"):
            xs = np.percentile([p[0] for p in hits], [5, 95])
            y = 0.84 if side == "bottom" else 0.16
            line = [[round(float(max(0.02, xs[0] - 0.05)), 3), y], [round(float(min(0.98, xs[1] + 0.05)), 3), y]]
            inside = [0.5, 0.45 if side == "bottom" else 0.6]
        else:
            ys = np.percentile([p[1] for p in hits], [5, 95])
            x = 0.12 if side == "left" else 0.88
            line = [[x, round(float(max(0.02, ys[0] - 0.05)), 3)], [x, round(float(min(0.98, ys[1] + 0.05)), 3)]]
            inside = [0.5, 0.5]
        side_tr = {"bottom": "alt", "top": "üst", "left": "sol", "right": "sağ"}[side]
        return {"zone": {"id": "kapi", "type": "entrance", "name": "Giriş Kapısı", "line": line, "inside_point": inside},
                "samples": len(self.ends),
                "note": f"girip çıkanların %{share * 100:.0f}'i görüntünün {side_tr} kenarından geçti"}


# -------------------------------------------------------------------- audit
def audit_result(taps: list[dict], sys_events: list[dict]) -> dict[str, Any]:
    """Match manual taps to the system's entry/exit events one by one (nearest within AUDIT_MATCH_S)."""
    res: dict[str, Any] = {}
    for kind, ev_type in (("in", "entry"), ("out", "exit")):
        manual = sorted(t["ts"] for t in taps if t["kind"] == kind)
        system = sorted(e["ts"] for e in sys_events if e["type"] == ev_type)
        pairs = sorted((abs(m - s), i, j) for i, m in enumerate(manual) for j, s in enumerate(system)
                       if abs(m - s) <= AUDIT_MATCH_S)
        used_m, used_s = set(), set()
        for _, i, j in pairs:
            if i in used_m or j in used_s:
                continue
            used_m.add(i)
            used_s.add(j)
        n_m, n_s, n_ok = len(manual), len(system), len(used_m)
        res[kind] = {
            "manual": n_m, "system": n_s, "matched": n_ok,
            "missed": n_m - n_ok,  # people the system did not count
            "extra": n_s - n_ok,  # counts with nobody behind them
            "count_accuracy": round(100 * (1 - abs(n_s - n_m) / n_m), 1) if n_m else None,
            "event_accuracy": round(100 * n_ok / max(n_m, n_s), 1) if max(n_m, n_s) else None,
        }
    tot_m = res["in"]["manual"] + res["out"]["manual"]
    tot_ok = res["in"]["matched"] + res["out"]["matched"]
    tot_max = max(tot_m, res["in"]["system"] + res["out"]["system"])
    res["overall"] = round(100 * tot_ok / tot_max, 1) if tot_max else None
    return res

