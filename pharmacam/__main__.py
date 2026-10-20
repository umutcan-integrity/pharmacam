"""Command line interface.

    python -m pharmacam run                     # device service (camera + API + jobs)
    python -m pharmacam check                   # camera / model self-test
    python -m pharmacam models [--fetch]        # detection models: list / download the configured one
    python -m pharmacam report --day 2026-10-19 # build a daily PDF
    python -m pharmacam backfill --days 35      # simulated history (demo / development only)
"""
from __future__ import annotations

import argparse
import logging
import sys

from . import __version__, timeutil
from .config import Config


def cmd_run(args, cfg: Config) -> None:
    from .service import run

    run(cfg)


def cmd_check(args, cfg: Config) -> int:
    """Open the camera, read a few frames and print what the network sees."""
    import time

    from .camera import open_source

    print(f"PharmaCam {__version__} — kamera testi ({cfg['camera']['source']})")
    try:
        src = open_source(cfg)
    except Exception as e:
        print(f"HATA: kamera açılamadı: {e}")
        return 1
    t0, n, people = time.time(), 0, 0
    try:
        for f in src.frames():
            n += 1
            people = max(people, len([d for d in f.detections if d.score >= cfg["camera"]["score_threshold"]]))
            if time.time() - t0 > args.seconds:
                break
    finally:
        src.close()
    dt = time.time() - t0
    print(f"OK: {n} kare / {dt:.1f} sn ({n / dt:.1f} fps), aynı anda görülen en fazla kişi: {people}")
    return 0


def cmd_models(args, cfg: Config) -> int:
    """List the detection presets and (with --fetch) download the configured one."""
    from .camera import models

    active = models.resolve(cfg)
    print(f"{'':2}{'ad':<9}{'model':<34}{'COCO mAP':>9}  {'kare/sn':>7}  lisans")
    for m in models.MODELS.values():
        mark = "→ " if active.spec is m else "  "
        print(f"{mark}{m.key:<9}{m.title:<34}{m.coco_map:>9.3f}  {m.fps:>7}  {m.licence}")
    if not active.spec:
        print(f"→ özel model: {active.path}")
    if args.fetch:
        try:
            m = models.ensure(cfg)
        except Exception as e:
            print(f"HATA: {e}")
            return 1
        print(f"OK: {m.title} hazır ({m.path})")
    return 0


def cmd_report(args, cfg: Config) -> None:
    from .reports.daily import generate_report
    from .storage.db import Store

    store = Store(cfg.resolve(cfg["storage"]["path"]))
    day = args.day or timeutil.today()
    bg = None
    bgp = cfg.resolve(cfg["storage"]["path"]).parent / "background.jpg"
    if bgp.exists():
        import cv2

        bg = cv2.cvtColor(cv2.imread(str(bgp)), cv2.COLOR_BGR2RGB)
    print(generate_report(store, cfg, day, cfg.resolve(args.out or cfg["reports"]["dir"]), background=bg))


def cmd_backfill(args, cfg: Config) -> None:
    from .tools.backfill import backfill

    if cfg["camera"]["source"] != "simulator" and not args.force:
        sys.exit("backfill yalnızca simülatör yapılandırmasıyla çalışır (gerçek veriyi kirletmemek için). "
                 "--force ile zorlayabilirsiniz.")
    backfill(cfg, days=args.days, end=args.end, workers=args.workers)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="pharmacam", description="PharmaCam eczane kamera analitiği")
    p.add_argument("-c", "--config", help="yapılandırma dosyası (varsayılan: $PHARMACAM_CONFIG veya config/pharmacam.yaml)")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="servisi başlat")
    c = sub.add_parser("check", help="kamera testi")
    c.add_argument("--seconds", type=float, default=5)
    m = sub.add_parser("models", help="yapay zekâ modellerini listele / indir")
    m.add_argument("--fetch", action="store_true", help="seçili modeli indir ve doğrula")
    r = sub.add_parser("report", help="günlük PDF rapor üret")
    r.add_argument("--day")
    r.add_argument("--out")
    b = sub.add_parser("backfill", help="simülasyonla geçmiş veri üret (demo)")
    b.add_argument("--days", type=int, default=35)
    b.add_argument("--end", help="son gün (varsayılan: dün)")
    b.add_argument("--workers", type=int, default=0)
    b.add_argument("--force", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = Config.load(args.config)
    timeutil.set_timezone(cfg["device"]["timezone"])
    rc = {"run": cmd_run, "check": cmd_check, "models": cmd_models, "report": cmd_report,
          "backfill": cmd_backfill}[args.cmd](args, cfg)
    return int(rc or 0)


if __name__ == "__main__":
    sys.exit(main())
