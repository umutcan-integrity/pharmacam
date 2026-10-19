"""Plain-language insights for the pharmacist (Turkish)."""
from __future__ import annotations

from typing import Any

from .. import timeutil
from . import forecast as fc
from . import queries as q


def fmt_dur(s: float | None) -> str:
    if s is None:
        return "—"
    s = float(s)
    if s < 60:
        return f"{s:.0f} sn"
    return f"{int(s // 60)} dk {int(s % 60):02d} sn"


def day_insights(store, cfg, day: str) -> list[dict[str, Any]]:
    names = {z.id: z.name for z in cfg.zones}
    s = q.summary(store, day)
    out: list[dict[str, Any]] = []
    if not s["entries"] and not s["visits"]:
        return [{"level": "info", "text": "Bu gün için veri yok."}]

    cur, ref = q.comparable_entries(store, day, timeutil.shift_day(day, -7))
    if ref >= 10:
        ch = 100.0 * (cur - ref) / ref
        lvl = "good" if ch >= 0 else "warn"
        ref_txt = f"geçen {s['weekday']} aynı saate" if day == timeutil.today() else f"geçen {s['weekday']} gününe"
        out.append({"level": lvl, "text": f"Müşteri girişi {ref_txt} göre %{ch:+.0f} ({ref} → {cur})."})

    hours = q.hourly(store, day)
    busy = max(hours, key=lambda h: h["entries"])
    if busy["entries"]:
        txt = f"En yoğun saat {busy['hour']:02d}:00–{busy['hour'] + 1:02d}:00 ({busy['entries']} giriş"
        if busy["avg_wait_s"]:
            txt += f", ort. bekleme {fmt_dur(busy['avg_wait_s'])}"
        out.append({"level": "info", "text": txt + ")."})

    if s["avg_wait_s"] is not None:
        if s["avg_wait_s"] > 180:
            out.append({"level": "bad", "text": f"Ortalama sıra bekleme {fmt_dur(s['avg_wait_s'])}: hedef 3 dk'nın üstünde. "
                                                "Yoğun saatlerde tezgâha ek personel önerilir."})
        elif s["p90_wait_s"] and s["p90_wait_s"] > 240:
            out.append({"level": "warn", "text": f"Müşterilerin %10'u {fmt_dur(s['p90_wait_s'])}'den uzun bekledi."})
        else:
            out.append({"level": "good", "text": f"Sıra bekleme süreleri iyi (ort. {fmt_dur(s['avg_wait_s'])})."})

    if s["abandoned"]:
        out.append({"level": "warn", "text": f"{s['abandoned']} müşteri sıradan hizmet almadan ayrıldı."})

    understaffed = [h for h in hours if h["staff_avg"] and h["occupancy_avg"] / max(h["staff_avg"], 0.1) > 4
                    and h["queue_max"] >= 3]
    if understaffed:
        hrs = ", ".join(f"{h['hour']:02d}:00" for h in understaffed[:4])
        out.append({"level": "warn", "text": f"Personel başına düşen müşteri yüksek: {hrs}."})

    zones = q.zone_stats(store, day, day, names)
    shelves = [z for z in zones if any(c.id == z["zone"] and c.type == "shelf" for c in cfg.zones)]
    if shelves:
        top = shelves[0]
        out.append({"level": "info", "text": f"En çok ilgi gören raf: {top['name']} ({top['stops']} duruş, "
                                             f"ort. {fmt_dur(top['avg_dwell_s'])})."})
        low = [z for z in shelves if z["stop_rate"] is not None and z["stop_rate"] < 40 and z["passes"] >= 10]
        for z in low[:2]:
            out.append({"level": "warn", "text": f"{z['name']} önünden geçenlerin yalnızca %{z['stop_rate']:.0f}'i durdu — "
                                                 "teşhir/yerleşim gözden geçirilebilir."})

    f = fc.forecast(store, 1, start=timeutil.shift_day(day, 1))
    if f["days"]:
        d = f["days"][0]
        if d["expected_total"]:
            out.append({"level": "info", "text": f"Yarın ({d['weekday']}) tahmini {d['expected_total']} müşteri "
                                                 f"(aralık {d['low_total']}–{d['high_total']}), "
                                                 f"en yoğun saat {d['peak_hour']:02d}:00."})
    return out
