"""Daily PDF report (A4, 4 pages) built with matplotlib only, so it runs on the Pi
without a browser or LaTeX."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.backends.backend_pdf import PdfPages  # noqa: E402
from matplotlib.patches import FancyBboxPatch  # noqa: E402

from .. import __version__, timeutil  # noqa: E402
from ..analytics import forecast as fc  # noqa: E402
from ..analytics import heatmap as hm  # noqa: E402
from ..analytics import queries as q  # noqa: E402
from ..analytics.insights import day_insights, fmt_dur  # noqa: E402

BRAND = "#0f8a5f"  # chrome only, never a data series
BRAND_L = "#e3f3ec"
INK = "#1d2b36"
MUTED = "#6b7a86"
GRID = "#e6ebee"
S1 = "#2a78d6"  # series 1 (blue)
S2 = "#eb6834"  # series 2 (orange)
S1_BAND = "#cde2fb"
NEUTRAL = "#b8c4cc"
STATUS = {"good": "#0ca30c", "warn": "#fab219", "bad": "#d03b3b", "info": S1}
STATUS_ICON = {"good": "✓", "warn": "!", "bad": "✕", "info": "i"}
A4 = (8.27, 11.69)
AYLAR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
PAGES = 4

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": 9,
    "axes.edgecolor": "#c9d2d9",
    "axes.labelcolor": MUTED,
    "xtick.color": MUTED,
    "ytick.color": MUTED,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.spines.left": False,
    "axes.grid": True,
    "axes.grid.axis": "y",
    "grid.color": GRID,
    "grid.linewidth": 0.8,
    "axes.axisbelow": True,
    "axes.titleweight": "bold",
    "axes.titlesize": 10.5,
    "axes.titlecolor": INK,
    "axes.titlelocation": "left",
    "axes.titlepad": 10,
})


def _tr_date(day: str) -> str:
    d = datetime.strptime(day, "%Y-%m-%d")
    return f"{d.day} {AYLAR[d.month - 1]} {d.year} {timeutil.WEEKDAYS_TR[d.weekday()]}"


def _header(fig, title: str, subtitle: str, page: int) -> None:
    fig.patches.append(FancyBboxPatch((0, 0.945), 1, 0.055, boxstyle="square,pad=0", transform=fig.transFigure,
                                      facecolor=BRAND, edgecolor="none"))
    fig.text(0.05, 0.968, "✚ PharmaCam", color="white", fontsize=15, fontweight="bold", va="center")
    fig.text(0.95, 0.975, title, color="white", fontsize=10, ha="right", va="center", fontweight="bold")
    fig.text(0.95, 0.958, subtitle, color="#e6f5ee", fontsize=8.5, ha="right", va="center")
    fig.text(0.05, 0.018, f"PharmaCam v{__version__} · Görüntü kaydedilmez, kişiler anonim izlenir (KVKK uyumlu tasarım)",
             color=MUTED, fontsize=7)
    fig.text(0.95, 0.018, f"Sayfa {page}/{PAGES}", color=MUTED, fontsize=7, ha="right")


def _kpi(fig, x, y, w, h, label, value, sub=None, status=None):
    fig.patches.append(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.004,rounding_size=0.01",
                                      transform=fig.transFigure, facecolor="#f6f8f8", edgecolor="#e1e7e5"))
    fig.text(x + 0.015, y + h - 0.011, label, fontsize=7.8, color=MUTED, va="top")
    fig.text(x + 0.015, y + 0.025 if sub else y + 0.016, value, fontsize=16, color=INK, fontweight="bold",
             va="bottom")
    if status:
        fig.text(x + w - 0.012, y + h - 0.011, STATUS_ICON[status], fontsize=8, color="white", va="top", ha="right",
                 fontweight="bold", bbox=dict(boxstyle="circle,pad=0.25", fc=STATUS[status], ec="none"))
    if sub:
        fig.text(x + 0.015, y + 0.009, sub, fontsize=7, color=MUTED, va="bottom")


def _empty(ax, text="Veri yok"):
    ax.text(0.5, 0.5, text, ha="center", va="center", color=MUTED, transform=ax.transAxes)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.grid(False)


def _hour_axis(ax, xs, step=1):
    ax.set_xticks(xs[::step], [f"{x:02d}" for x in xs[::step]])
    ax.tick_params(axis="both", length=0)


def _bars(ax, xs, ys, color, label_max=True, fmt="{:.0f}"):
    ax.bar(xs, ys, color=color, width=0.72, edgecolor="white", linewidth=1.2)
    if label_max and ys and max(ys) > 0:
        i = int(np.argmax(ys))
        ax.annotate(fmt.format(ys[i]), (xs[i], ys[i]), ha="center", va="bottom", fontsize=8, color=INK,
                    xytext=(0, 2), textcoords="offset points", fontweight="bold")


def generate_report(store, cfg, day: str, out_dir: Path, background: np.ndarray | None = None) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"PharmaCam_{day}.pdf"
    names = {z.id: z.name for z in cfg.zones}
    shelf_ids = {z.id for z in cfg.zones if z.type == "shelf"}
    pharmacy = cfg["device"]["pharmacy_name"]
    s = q.summary(store, day)
    prev = q.summary(store, timeutil.shift_day(day, -7))
    hours = q.hourly(store, day)
    qa = q.queue_analysis(store, day)
    zones = [z for z in q.zone_stats(store, day, day, names) if z["zone"] in shelf_ids]
    insights = day_insights(store, cfg, day)
    tomorrow = fc.forecast(store, 1, start=timeutil.shift_day(day, 1))
    grid = store.heatmap(day, day)
    o, c = int(cfg["reports"]["opening_hour"]), int(cfg["reports"]["closing_hour"])
    hr = [h for h in hours if o <= h["hour"] < c] or hours
    xs = [h["hour"] for h in hr]
    sub = f"{pharmacy} · {_tr_date(day)}"

    with PdfPages(path) as pdf:
        # ------------------------------------------------------------ page 1
        fig = plt.figure(figsize=A4)
        _header(fig, "Günlük Mağaza Raporu", sub, 1)
        ch = 100.0 * (s["entries"] - prev["entries"]) / prev["entries"] if prev["entries"] else None
        wait_status = None
        if s["avg_wait_s"] is not None:
            wait_status = "bad" if s["avg_wait_s"] > 180 else ("warn" if s["avg_wait_s"] > 120 else "good")
        cards = [
            ("Giren müşteri", f"{s['entries']}",
             f"geçen {s['weekday']}: {prev['entries']}" + (f" ({ch:+.0f}%)" if ch is not None else ""), None),
            ("En yoğun saat", f"{s['busiest_hour']:02d}:00" if s["busiest_hour"] is not None else "—", None, None),
            ("Ort. ziyaret süresi", fmt_dur(s["avg_visit_s"]), None, None),
            ("En yüksek doluluk", f"{s['peak_occupancy']} kişi", None, None),
            ("Ort. sıra bekleme", fmt_dur(s["avg_wait_s"]), f"%90'ı: {fmt_dur(s['p90_wait_s'])} altında", wait_status),
            ("Hizmet alan", f"{s['served']}", f"girenlerin %{s['service_rate']:.0f}'i" if s["service_rate"] else None,
             None),
            ("Sıradan vazgeçen", f"{s['abandoned']}", None, "warn" if s["abandoned"] else None),
            ("Ort. hizmet süresi", fmt_dur(s["avg_service_s"]), f"en uzun sıra: {s['peak_queue']} kişi", None),
        ]
        cw, chh, gap = 0.2075, 0.075, 0.0125
        for i, (label, val, subt, st) in enumerate(cards):
            r, k = divmod(i, 4)
            _kpi(fig, 0.05 + k * (cw + gap), 0.845 - r * (chh + gap), cw, chh, label, val, subt, st)

        ax = fig.add_axes([0.08, 0.52, 0.86, 0.2])
        _bars(ax, xs, [h["entries"] for h in hr], S1)
        ax.set_title("Saatlik müşteri girişi")
        _hour_axis(ax, xs)

        ax = fig.add_axes([0.08, 0.27, 0.86, 0.18])
        occ = [h["occupancy_avg"] for h in hr]
        ax.fill_between(xs, occ, color=S1_BAND, step=None, alpha=0.6, lw=0)
        ax.plot(xs, occ, color=S1, lw=2, marker="o", ms=4, mec="white", mew=1)
        if occ and max(occ) > 0:
            i = int(np.argmax(occ))
            ax.annotate(f"{occ[i]:.1f}", (xs[i], occ[i]), ha="center", va="bottom", fontsize=8, color=INK,
                        xytext=(0, 5), textcoords="offset points", fontweight="bold")
        ax.set_title("Ortalama içerideki müşteri (kişi)")
        ax.set_ylim(bottom=0, top=max(occ + [1]) * 1.25)
        _hour_axis(ax, xs)

        fig.text(0.05, 0.215, "Öne çıkanlar", fontsize=11, fontweight="bold", color=INK)
        y = 0.19
        for ins in insights[:7]:
            lvl = ins["level"]
            fig.text(0.062, y, STATUS_ICON[lvl], fontsize=7, color="white", va="top", ha="center", fontweight="bold",
                     bbox=dict(boxstyle="circle,pad=0.22", fc=STATUS[lvl], ec="none"))
            fig.text(0.085, y, ins["text"], color=INK, fontsize=8.6, va="top")
            y -= 0.024
        pdf.savefig(fig)
        plt.close(fig)

        # ------------------------------------------------------------ page 2
        fig = plt.figure(figsize=A4)
        _header(fig, "Sıra ve Personel Analizi", sub, 2)
        ax = fig.add_axes([0.08, 0.68, 0.86, 0.22])
        waits = [(h["avg_wait_s"] or 0) / 60 for h in hr]
        _bars(ax, xs, waits, S1, fmt="{:.1f} dk")
        ax.axhline(3, color=STATUS["bad"], ls="--", lw=1)
        ax.annotate("hedef: 3 dk", (1, 3), xycoords=("axes fraction", "data"), ha="right", va="bottom",
                    fontsize=7.5, color=STATUS["bad"], xytext=(0, 2), textcoords="offset points")
        ax.set_ylim(0, max(waits + [3.4]) * 1.15)
        ax.set_title("Saatlik ortalama sıra bekleme (dakika)")
        _hour_axis(ax, xs)

        ax = fig.add_axes([0.08, 0.40, 0.86, 0.18])
        _bars(ax, xs, [h["queue_max"] for h in hr], S1)
        ax.set_title("Saatlik en uzun sıra (kişi)")
        ax.yaxis.get_major_locator().set_params(integer=True)
        _hour_axis(ax, xs)

        ax = fig.add_axes([0.08, 0.08, 0.38, 0.22])
        hist = qa["histogram"]
        _bars(ax, list(range(len(hist))), [b["count"] for b in hist], S1)
        ax.set_xticks(range(len(hist)), [b["label"] for b in hist], rotation=35, ha="right", fontsize=7)
        ax.tick_params(length=0)
        ax.set_title("Sıra bekleme dağılımı (kişi)")

        ax = fig.add_axes([0.56, 0.08, 0.38, 0.22])
        st = [h["staff_avg"] for h in hr]
        cu = [h["occupancy_avg"] for h in hr]
        ax.plot(xs, cu, color=S1, lw=2)
        ax.plot(xs, st, color=S2, lw=2)
        if xs:
            ax.annotate("Müşteri", (xs[-1], cu[-1]), xytext=(4, 0), textcoords="offset points", fontsize=7.5,
                        color=INK, va="center")
            ax.annotate("Personel", (xs[-1], st[-1]), xytext=(4, 0), textcoords="offset points", fontsize=7.5,
                        color=INK, va="center")
        ax.plot([], [], color=S1, lw=2, label="Müşteri")
        ax.plot([], [], color=S2, lw=2, label="Personel")
        ax.legend(frameon=False, fontsize=7.5, loc="upper left")
        ax.set_ylim(bottom=0)
        _hour_axis(ax, xs, 2)
        ax.set_title("Personel ve müşteri (ort. kişi)")
        pdf.savefig(fig)
        plt.close(fig)

        # ------------------------------------------------------------ page 3
        fig = plt.figure(figsize=A4)
        _header(fig, "Raf Analizi ve Isı Haritası", sub, 3)
        ax = fig.add_axes([0.28, 0.66, 0.66, 0.22])
        if zones:
            zz = list(reversed(zones))
            y = np.arange(len(zz))
            ax.barh(y + 0.2, [z["stops"] for z in zz], height=0.38, color=S1, edgecolor="white", label="Durdu")
            ax.barh(y - 0.2, [z["passes"] for z in zz], height=0.38, color=NEUTRAL, edgecolor="white", label="Geçti")
            ax.set_yticks(y, [z["name"] for z in zz])
            for i, z in enumerate(zz):
                ax.text(z["stops"] + 1, i + 0.2, f"{z['stops']}", va="center", fontsize=7.5, color=INK)
            ax.legend(frameon=False, loc="lower right", bbox_to_anchor=(1.0, 1.0), ncol=2, fontsize=8)
            ax.set_xlim(right=max(max(z["stops"], z["passes"]) for z in zz) * 1.15 + 1)
            ax.grid(axis="x")
            ax.grid(axis="y", visible=False)
            ax.tick_params(length=0)
        else:
            _empty(ax, "Raf bölgesi tanımlı değil")
        ax.set_title("Raf önünde durma (en az 3 sn) ve geçme")

        ax = fig.add_axes([0.08, 0.45, 0.86, 0.15])
        cell = [[z["name"], z["stops"], z["passes"], f"%{z['stop_rate']:.0f}" if z["stop_rate"] is not None else "—",
                 fmt_dur(z["avg_dwell_s"]), fmt_dur(z["max_dwell_s"])] for z in zones[:8]]
        ax.axis("off")
        if cell:
            t = ax.table(cellText=cell, colLabels=["Raf", "Duruş", "Geçiş", "Durma oranı", "Ort. süre", "En uzun"],
                         loc="upper center", cellLoc="center", colLoc="center")
            t.auto_set_font_size(False)
            t.set_fontsize(8)
            t.scale(1, 1.35)
            for (r, _), cl in t.get_celld().items():
                cl.set_edgecolor("#e1e7e5")
                if r == 0:
                    cl.set_facecolor(BRAND_L)
                    cl.set_text_props(fontweight="bold", color=INK)

        ax = fig.add_axes([0.08, 0.06, 0.84, 0.36])
        if grid is not None and grid.any():
            ax.imshow(hm.render(grid, background, size=(960, 720)))
            ax.set_title("Isı haritası: müşterilerin en çok vakit geçirdiği alanlar (koyu = yoğun)")
            ax.set_xticks([])
            ax.set_yticks([])
            ax.grid(False)
            for sp in ax.spines.values():
                sp.set_visible(False)
        else:
            _empty(ax, "Isı haritası verisi yok")
        pdf.savefig(fig)
        plt.close(fig)

        # ------------------------------------------------------------ page 4
        fig = plt.figure(figsize=A4)
        _header(fig, "Yarın İçin Tahmin", sub, 4)
        ax = fig.add_axes([0.08, 0.55, 0.86, 0.32])
        if tomorrow["days"] and tomorrow["days"][0]["expected_total"]:
            d = tomorrow["days"][0]
            hh = [h for h in d["hours"] if o <= h["hour"] < c]
            x = [h["hour"] for h in hh]
            ax.fill_between(x, [h["low"] for h in hh], [h["high"] for h in hh], color=S1_BAND, lw=0,
                            label="Olası aralık (%80)")
            ax.plot(x, [h["expected"] for h in hh], color=S1, lw=2, marker="o", ms=4, mec="white", mew=1,
                    label="Beklenen")
            _hour_axis(ax, x)
            ax.set_ylim(bottom=0)
            ax.legend(frameon=False, fontsize=8, loc="upper left")
            ax.set_title(f"Yarın ({d['weekday']}) saatlik müşteri tahmini")
            fig.text(0.08, 0.50, f"Beklenen toplam: {d['expected_total']} müşteri  "
                                 f"(olası aralık {d['low_total']}–{d['high_total']})", fontsize=11, color=INK,
                     fontweight="bold")
            peak = d["peak_hour"]
            if peak is not None:
                svc = s["avg_service_s"] or 90.0
                need = fc.staffing_hint(d["hours"][peak]["expected"], svc)
                fig.text(0.08, 0.475, f"En yoğun saat {peak:02d}:00 · tezgâhta önerilen personel: {need} kişi "
                                      f"(ort. hizmet süresi {fmt_dur(svc)} varsayımıyla)", fontsize=9, color=INK)
            fig.text(0.08, 0.425, "Yöntem: aynı haftanın gününe ait önceki haftalar ağırlıklı ortalanır (yakın hafta daha "
                                  "ağır)\nve son 2 haftanın eğilimiyle ölçeklenir. "
                                  f"Kullanılan geçmiş: {tomorrow['history_days']} gün.",
                     fontsize=7.5, color=MUTED, linespacing=1.5)
        else:
            _empty(ax, "Tahmin için yeterli geçmiş veri yok (en az 1 hafta önerilir)")
            ax.set_title("Yarın için tahmin")

        ax = fig.add_axes([0.08, 0.08, 0.86, 0.28])
        last = q.daily(store, timeutil.shift_day(day, -27), day)
        dx = list(range(len(last)))
        ys = [r["entries"] for r in last]
        ax.bar(dx, ys, color=[S1 if r["day"] == day else S1_BAND for r in last], width=0.72, edgecolor="white")
        ax.set_xticks(dx[::3], [f"{r['day'][8:]}.{r['day'][5:7]}" for r in last][::3], fontsize=7)
        ax.tick_params(length=0)
        ax.set_title("Son 4 hafta günlük müşteri (koyu = bu rapor günü)")
        pdf.savefig(fig)
        plt.close(fig)

        meta = pdf.infodict()
        meta["Title"] = f"PharmaCam Günlük Rapor {day}"
        meta["Author"] = "PharmaCam"
        meta["Subject"] = pharmacy

    store.add_report(day, str(path))
    return path
