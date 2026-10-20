"""Tracker benchmark: the analytics engine on the pharmacy simulator, scored against ground truth.

The simulator knows where every person really is, so we can measure what matters for the
product — entries/exits, occupancy, queue length, identity stability and phantom visitors —
for any tracker, under a detector noise model that mimics a real camera:

- bursts of missed detections (a person hidden behind a shelf or another person),
- the back person of an overlapping pair suppressed by NMS, or seen only above the front
  person's head (the foot point jumps up) with a low score,
- box jitter, a few random low-score false positives,
- a static false positive: a poster of a person on the wall.

    python -m pharmacam.tools.bench_trackers --minutes 60 --seeds 3
    python -m pharmacam.tools.bench_trackers --trackers ours,rf_bytetrack,rf_ocsort --noise hard

Optional trackers: Roboflow `trackers` (Apache-2.0: pip install trackers supervision scipy) and
the ByteTrack of Sony's IMX500 Application Module Library (Apache-2.0), loaded from a folder with
its byte_tracker.py, basetrack.py, kalman_filter.py and matching.py (`--sony-src DIR`, needs `lap`).
"""
from __future__ import annotations

import argparse
import itertools
import json
import random
import shutil
import statistics
import sys
import tempfile
import time
import types
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from pathlib import Path

import numpy as np

from .. import timeutil
from ..analytics import queries as q
from ..analytics.engine import AnalyticsEngine
from ..camera.base import Detection, Frame
from ..camera.simulator import PharmacySim
from ..config import Config
from ..storage.db import Store
from ..vision.geometry import iou, projection_t, signed_side
from ..vision.tracker import Track, Tracker

ROOT = Path(__file__).resolve().parents[2]
DAY = "2026-10-14"
POSTER = (0.86, 0.30, 0.92, 0.47)  # a life-size poster on the vitamin shelf

NOISE = {
    # what the simulator itself produces
    "clean": dict(miss=0.03, burst_start=0.0, burst_s=0.0, rel_jitter=0.012, occl_cover=9.0, occl_drop=0.0,
                  fp_rate=0.01, poster=0.0, score=(0.62, 0.95), arrival=1.0),
    # a real angled camera in a busy hour
    "realistic": dict(miss=0.04, burst_start=0.010, burst_s=0.6, rel_jitter=0.025, occl_cover=0.35, occl_drop=0.3,
                      fp_rate=0.02, poster=0.5, score=(0.55, 0.93), arrival=1.0),
    # busier counter (service ~85% utilised), poor light, more occlusion
    "hard": dict(miss=0.06, burst_start=0.020, burst_s=1.0, rel_jitter=0.04, occl_cover=0.30, occl_drop=0.75,
                 fp_rate=0.04, poster=0.7, score=(0.50, 0.90), arrival=1.15),
}

_ids = itertools.count(1_000_000)


# ---------------------------------------------------------------- detector model
class Detector:
    """Turns the simulator's true person boxes into what a camera network would report."""

    def __init__(self, level: str, fps: float, rng: random.Random):
        self.p = NOISE[level]
        self.fps = fps
        self.rng = rng
        self.hidden: dict[int, bool] = {}

    def __call__(self, agents) -> list[Detection]:
        p, r = self.p, self.rng
        boxes = {a.id: PharmacySim.box_of(a) for a in agents}
        end_p = 1.0 / max(1.0, p["burst_s"] * self.fps)
        dets = []
        for a in agents:
            box = boxes[a.id]
            # occlusion bursts
            if self.hidden.get(a.id):
                if r.random() < end_p:
                    self.hidden[a.id] = False
                else:
                    continue
            elif p["burst_start"] and r.random() < p["burst_start"]:
                self.hidden[a.id] = True
                continue
            if r.random() < p["miss"]:
                continue
            score = r.uniform(*p["score"])
            # overlap with someone nearer the camera (larger foot y = in front)
            front = [boxes[b.id] for b in agents if b.id != a.id and boxes[b.id][3] > box[3]]
            cover = max((_cover(box, f) for f in front), default=0.0)
            if cover > p["occl_cover"]:
                if r.random() < p["occl_drop"]:
                    continue  # suppressed by NMS
                score = r.uniform(0.30, 0.55)
                f = max(front, key=lambda f: _cover(box, f))
                if r.random() < 0.5 and f[1] - box[1] > 0.3 * (box[3] - box[1]):
                    box = (box[0], box[1], box[2], f[1])  # only the part above the front person shows
            if box[3] > 0.97:
                score *= 0.9  # cut by the image edge at the door
            h = box[3] - box[1]
            j = p["rel_jitter"] * h
            box = tuple(min(1.0, max(0.0, v + r.gauss(0, j))) for v in box)
            if box[3] - box[1] > 0.03 and box[2] - box[0] > 0.01:
                dets.append(Detection(box, score))
        if p["poster"] and r.random() < p["poster"]:
            dets.append(Detection(tuple(v + r.gauss(0, 0.003) for v in POSTER), r.uniform(0.40, 0.72)))
        if r.random() < p["fp_rate"]:
            x, y = r.uniform(0.1, 0.9), r.uniform(0.3, 0.9)
            dets.append(Detection((x - 0.03, y - 0.1, x + 0.03, y), r.uniform(0.30, 0.50)))
        return [d for d in dets if d.score >= 0.30]  # the camera source keeps >= low threshold


def _cover(a, b) -> float:
    """Fraction of box a hidden behind box b."""
    iw = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    ih = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    area = (a[2] - a[0]) * (a[3] - a[1])
    return iw * ih / area if area > 0 else 0.0


# ---------------------------------------------------------------- tracker adapters
class _Adapter:
    """Runs an external tracker behind the engine's Tracker interface: update() → (active, ended)."""

    name = "?"

    def __init__(self):
        self.by_ext: dict[int, Track] = {}
        self.tracks: list[Track] = []
        self.last_active: list[Track] = []

    def _step(self, dets: list[Detection], ts: float) -> tuple[dict[int, tuple], set[int]]:
        raise NotImplementedError

    def update(self, detections: list[Detection], ts: float):
        updated, alive = self._step(detections, ts)
        active = []
        for eid, box in updated.items():
            box = tuple(float(min(1.0, max(0.0, v))) for v in box)
            t = self.by_ext.get(eid)
            if t is None:
                t = self.by_ext[eid] = Track(id=next(_ids), box=box, score=1.0, first_ts=ts, last_ts=ts,
                                             confirmed=True)
            else:
                t.box, t.last_ts, t.lost = box, ts, False
                t.hits += 1
            t.motion.append((ts, t.foot))
            active.append(t)
        ended = []
        for eid in list(self.by_ext):
            if eid in updated:
                continue
            t = self.by_ext[eid]
            if eid in alive:
                t.lost = True
            else:
                ended.append(self.by_ext.pop(eid))
        self.tracks = list(self.by_ext.values())
        self.last_active = active
        return active, ended

    def flush(self) -> list[Track]:
        ended = list(self.by_ext.values())
        self.by_ext.clear()
        self.tracks = []
        return ended


class Ours:
    """PharmaCam's own ByteTrack-lite, recording its output for the identity metrics."""

    name = "ours"

    def __init__(self, cfg):
        self.t = Tracker.from_config(cfg["tracker"])
        self.last_active: list[Track] = []

    @property
    def tracks(self):
        return self.t.tracks

    def update(self, detections, ts):
        active, ended = self.t.update(detections, ts)
        self.last_active = active
        return active, ended

    def flush(self):
        return self.t.flush()


class Roboflow(_Adapter):
    def __init__(self, kind: str, fps: float, max_lost_s: float, high: float, min_hits: int):
        super().__init__()
        import trackers as rt

        self.name = f"rf_{kind}"
        buf = int(round(max_lost_s * 30))  # the library counts the buffer in 30-fps frames
        if kind == "bytetrack":
            self.trk = rt.ByteTrackTracker(lost_track_buffer=buf, frame_rate=fps, track_activation_threshold=high,
                                           minimum_consecutive_frames=min_hits, high_conf_det_threshold=high)
        elif kind == "ocsort":
            self.trk = rt.OCSORTTracker(lost_track_buffer=buf, frame_rate=fps, minimum_consecutive_frames=min_hits,
                                        high_conf_det_threshold=high)
        elif kind == "botsort":
            self.trk = rt.BoTSORTTracker(lost_track_buffer=buf, frame_rate=fps, track_activation_threshold=high,
                                         minimum_consecutive_frames=min_hits, high_conf_det_threshold=high,
                                         enable_cmc=False)
        else:
            raise ValueError(kind)

    def _step(self, dets, ts):
        import supervision as sv

        xyxy = np.array([d.box for d in dets], dtype=float).reshape(-1, 4)
        conf = np.array([d.score for d in dets], dtype=float)
        self.trk.update(sv.Detections(xyxy=xyxy, confidence=conf, class_id=np.zeros(len(conf), dtype=int)),
                        timestamp=ts)
        updated, alive = {}, set()
        for tl in self.trk.tracks:
            tid = int(tl.tracker_id)
            if tid < 0:
                continue
            alive.add(tid)
            if tl.time_since_update == 0:
                updated[tid] = tuple(np.asarray(tl.get_state_bbox(), dtype=float).ravel()[:4])
        return updated, alive


def _load_sony(src: str):
    """Import Sony's modlib tracker files as a package, with the modlib imports stubbed."""
    pkg = Path(tempfile.mkdtemp(prefix="sonybt_")) / "sonybt"
    pkg.mkdir()
    for f in ("byte_tracker.py", "basetrack.py", "kalman_filter.py", "matching.py"):
        shutil.copy(Path(src) / f, pkg / f)
    (pkg / "__init__.py").write_text("")
    for name in ("modlib", "modlib.devices", "modlib.devices.frame", "modlib.models"):
        sys.modules.setdefault(name, types.ModuleType(name))
    sys.modules["modlib.devices.frame"].Frame = object
    for cls in ("Detections", "Poses", "InstanceSegments"):
        setattr(sys.modules["modlib.models"], cls, type(cls, (), {}))
    sys.path.insert(0, str(pkg.parent))
    import sonybt.byte_tracker as bt  # noqa: E402

    return bt


class Sony(_Adapter):
    name = "sony_bytetrack"

    def __init__(self, src: str, fps: float, max_lost_s: float, high: float):
        super().__init__()
        bt = _load_sony(src)

        class Args:  # Sony's queue-monitor defaults, buffer and thresholds aligned with ours
            track_thresh = high - 0.1  # Sony starts new tracks above track_thresh + 0.1
            track_buffer = int(round(max_lost_s * 30))
            match_thresh = 0.8
            aspect_ratio_thresh = 3.0
            min_box_area = 1.0
            mot20 = False

        self.trk = bt.BYTETracker(Args(), frame_rate=fps)

    def _step(self, dets, ts):
        out = np.array([[*d.box, d.score] for d in dets], dtype=float).reshape(-1, 5)
        res = types.SimpleNamespace(output_results=out, img_info=(1, 1), img_size=(1, 1))
        online = self.trk._update(res)
        updated = {int(s.track_id): tuple(float(v) for v in s.tlbr) for s in online}
        alive = {int(s.track_id) for s in self.trk.tracked_stracks + self.trk.lost_stracks if s.is_activated}
        return updated, alive


def make_tracker(name: str, cfg: Config, fps: float, sony_src: str | None):
    tc = cfg["tracker"]
    if name == "ours":
        return Ours(cfg)
    if name.startswith("rf_"):
        return Roboflow(name[3:], fps, tc["max_lost_seconds"], tc["high_threshold"], tc["min_hits"])
    if name == "sony_bytetrack":
        if not sony_src:
            raise SystemExit("sony_bytetrack için --sony-src gerekli")
        return Sony(sony_src, fps, tc["max_lost_seconds"], tc["high_threshold"])
    raise SystemExit(f"bilinmeyen takipçi: {name}")


# ---------------------------------------------------------------- one run
def run_one(job: dict) -> dict:
    name, level, seed, minutes, fps = job["tracker"], job["noise"], job["seed"], job["minutes"], job["fps"]
    cfg = Config.load(ROOT / "config" / "pharmacam.simulator.yaml")
    timeutil.set_timezone(cfg["device"]["timezone"])
    door = cfg.zones_of("entrance")[0]
    a, b = door.line
    sign = 1.0 if signed_side(door.inside_point, a, b) >= 0 else -1.0

    def door_d(p):
        return signed_side(p, a, b) * sign, projection_t(p, a, b)

    sim = PharmacySim(seed=seed, arrival_scale=NOISE[level]["arrival"], n_staff=2, hour_override=18)
    det = Detector(level, fps, random.Random(seed * 7919 + 13))
    store = Store(":memory:")
    eng = AnalyticsEngine(cfg, store)
    eng.tracker = make_tracker(name, cfg, fps, job.get("sony_src"))

    y, m, d = map(int, DAY.split("-"))
    ts = datetime(y, m, d, 18, 0, tzinfo=timeutil.tz()).timestamp()
    dt = 1.0 / fps
    for k in range(int(120 * fps)):  # warm-up: people already inside when we start watching
        sim.step(ts - 120 + k * dt, dt)

    side: dict[int, int] = {}
    t_entries = t_exits = 0
    occ_err, q_err, occ_true = [], [], []
    gt_tracks: dict[int, dict[int, int]] = {}  # person → {track id: frames}
    tr_gts: dict[int, dict[int, int]] = {}  # track → {person: frames}
    gt_frames: dict[int, int] = {}
    corr: dict[int, int] = {}  # person → track matched in the previous frame
    id_switches = 0
    customers = set()
    q_join: dict[int, float] = {}
    true_waits: list[float] = []
    prev_state: dict[int, str] = {}
    tracker_s = 0.0
    n_frames = int(minutes * 60 * fps)
    orig_update = eng.tracker.update

    def timed_update(dets, t):
        nonlocal tracker_s
        t0 = time.perf_counter()
        r = orig_update(dets, t)
        tracker_s += time.perf_counter() - t0
        return r

    eng.tracker.update = timed_update
    for _ in range(n_frames):
        sim.step(ts, dt)
        agents = list(sim.agents)
        eng.process(Frame(ts=ts, image=None, detections=det(agents)))
        # ---- ground truth
        inside = 0
        for ag in agents:
            dd, along = door_d(ag.pos)
            s = 1 if dd > 0.004 else -1 if dd < -0.004 else 0
            if not ag.staff:
                customers.add(ag.id)
                prev = side.get(ag.id)
                if s and prev and s != prev and -0.25 <= along <= 1.25:
                    if s > 0:
                        t_entries += 1
                    else:
                        t_exits += 1
                if s:
                    side[ag.id] = s
                if dd > 0:
                    inside += 1
        for ag in agents:  # true queue waits: joined the line → called to the counter
            st, before = ag.state, prev_state.get(ag.id)
            if st == "queued" and before != "queued":
                q_join[ag.id] = ts
            elif before == "queued" and st == "to_service" and ag.id in q_join:
                true_waits.append(ts - q_join.pop(ag.id))
            prev_state[ag.id] = st
        lv = eng.live
        occ_err.append(abs(lv.occupancy - inside))
        occ_true.append(inside)
        q_true = sum(1 for ag in agents if ag.state == "queued")
        q_err.append(abs(lv.queue_length - q_true))
        # ---- identity, CLEAR-MOT style: keep last frame's person↔track pairs while they still
        # overlap (IoU ≥ 0.5), match the rest greedily; a person whose track changes is an ID switch
        gt_boxes = [(ag.id, PharmacySim.box_of(ag)) for ag in agents]
        gbox = dict(gt_boxes)
        tbox = {t.id: t.box for t in eng.tracker.last_active}
        used_t, used_g, matched = set(), set(), []
        for gid, tid in corr.items():
            if gid in gbox and tid in tbox and iou(tbox[tid], gbox[gid]) >= 0.5:
                used_t.add(tid)
                used_g.add(gid)
                matched.append((gid, tid))
        pairs = sorted(((iou(tb, gb), tid, gid) for tid, tb in tbox.items() if tid not in used_t
                        for gid, gb in gt_boxes if gid not in used_g), reverse=True)
        for v, tid, gid in pairs:
            if v < 0.5:
                break
            if tid in used_t or gid in used_g:
                continue
            used_t.add(tid)
            used_g.add(gid)
            matched.append((gid, tid))
        for gid, tid in matched:
            if gid in corr and corr[gid] != tid:
                id_switches += 1
            corr[gid] = tid
            gt_tracks.setdefault(gid, {}).setdefault(tid, 0)
            gt_tracks[gid][tid] += 1
            tr_gts.setdefault(tid, {}).setdefault(gid, 0)
            tr_gts[tid][gid] += 1
        for t in eng.tracker.last_active:
            tr_gts.setdefault(t.id, {})
            tr_gts[t.id].setdefault(-1, 0)
            if t.id not in used_t:
                tr_gts[t.id][-1] += 1
        for gid, _ in gt_boxes:
            gt_frames[gid] = gt_frames.get(gid, 0) + 1
        ts += dt
    eng.flush()
    s = q.summary(store, DAY)

    # identity metrics over customers seen for at least 3 s
    min_frames = int(3 * fps)
    people = [g for g in customers if gt_frames.get(g, 0) >= min_frames]
    ids_per = [len(gt_tracks.get(g, {})) for g in people]
    covered = [sum(gt_tracks.get(g, {}).values()) / gt_frames[g] for g in people]
    merged = sum(1 for t, m in tr_gts.items() if len([g for g, n in m.items() if g >= 0 and n >= int(fps)]) > 1)
    phantom = sum(1 for t, m in tr_gts.items()
                  if m.get(-1, 0) >= 2 * fps and m.get(-1, 0) > 0.8 * sum(m.values()))
    return {
        **{k: job[k] for k in ("tracker", "noise", "seed", "minutes", "fps")},
        "true_entries": t_entries, "entries": s["entries"],
        "true_exits": t_exits, "exits": s["exits"],
        "visits": s["visits"], "true_visitors": len(people),
        "true_served": len(true_waits), "served": s["served"],
        "true_wait_s": float(np.mean(true_waits)) if true_waits else None, "wait_s": s["avg_wait_s"],
        "occ_mae": float(np.mean(occ_err)), "occ_mean": float(np.mean(occ_true)),
        "queue_mae": float(np.mean(q_err)),
        "ids_per_person": float(np.mean(ids_per)) if ids_per else None,
        "single_id_pct": 100.0 * sum(1 for n in ids_per if n == 1) / len(ids_per) if ids_per else None,
        "coverage_pct": 100.0 * float(np.mean(covered)) if covered else None,
        "merged_tracks": merged, "phantom_tracks": phantom, "id_switches": id_switches,
        "tracker_ms": 1000.0 * tracker_s / n_frames,
    }


# ---------------------------------------------------------------- report
def _pct_err(est: float, true: float) -> float:
    return 100.0 * abs(est - true) / true if true else 0.0


def summarise(rows: list[dict]) -> list[dict]:
    out = []
    for (tr, nz), grp in itertools.groupby(sorted(rows, key=lambda r: (r["noise"], r["tracker"])),
                                           key=lambda r: (r["tracker"], r["noise"])):
        g = list(grp)
        te, e = sum(r["true_entries"] for r in g), sum(r["entries"] for r in g)
        tx, x = sum(r["true_exits"] for r in g), sum(r["exits"] for r in g)
        out.append({
            "tracker": tr, "noise": nz, "runs": len(g),
            "true_entries": te, "entries": e, "entry_err_pct": _pct_err(e, te),
            "worst_run_entry_err_pct": max(_pct_err(r["entries"], r["true_entries"]) for r in g),
            "true_exits": tx, "exits": x, "exit_err_pct": _pct_err(x, tx),
            "visits_per_visitor": sum(r["visits"] for r in g) / max(1, sum(r["true_visitors"] for r in g)),
            "served_err_pct": _pct_err(sum(r["served"] for r in g), sum(r["true_served"] for r in g)),
            "true_wait_s": statistics.mean(r["true_wait_s"] for r in g if r["true_wait_s"] is not None),
            "wait_s": statistics.mean(r["wait_s"] or 0.0 for r in g if r["true_wait_s"] is not None),
            "occ_mae": statistics.mean(r["occ_mae"] for r in g),
            "queue_mae": statistics.mean(r["queue_mae"] for r in g),
            "ids_per_person": statistics.mean(r["ids_per_person"] for r in g if r["ids_per_person"] is not None),
            "single_id_pct": statistics.mean(r["single_id_pct"] for r in g if r["single_id_pct"] is not None),
            "coverage_pct": statistics.mean(r["coverage_pct"] for r in g if r["coverage_pct"] is not None),
            "merged_tracks": sum(r["merged_tracks"] for r in g),
            "id_switches_per_100": 100.0 * sum(r["id_switches"] for r in g) / max(1, sum(r["true_visitors"] for r in g)),
            "phantom_tracks": sum(r["phantom_tracks"] for r in g),
            "tracker_ms": statistics.mean(r["tracker_ms"] for r in g),
        })
    return out


def table(summary: list[dict]) -> str:
    hdr = ("| Senaryo | Takipçi | Giriş (gerçek→sayılan) | Giriş hatası | En kötü koşu | Çıkış hatası | "
           "Ziyaret/kişi | İçeride MAE | Sıra MAE | Hizmet hatası | Bekleme (gerçek→ölçülen) | "
           "Kişi başı kimlik | Tek kimlik % | Kimlik değişimi/100 kişi | Kapsama % | Karışan iz | Hayalet iz | "
           "ms/kare |")
    lines = [hdr, "|" + "---|" * 18]
    for s in summary:
        lines.append(
            f"| {s['noise']} | {s['tracker']} | {s['true_entries']}→{s['entries']} | {s['entry_err_pct']:.1f}% | "
            f"{s['worst_run_entry_err_pct']:.1f}% | {s['exit_err_pct']:.1f}% | {s['visits_per_visitor']:.2f} | "
            f"{s['occ_mae']:.2f} | {s['queue_mae']:.2f} | {s['served_err_pct']:.1f}% | "
            f"{s['true_wait_s']:.0f}→{s['wait_s']:.0f} sn | {s['ids_per_person']:.2f} | {s['single_id_pct']:.0f} | "
            f"{s['id_switches_per_100']:.0f} | "
            f"{s['coverage_pct']:.0f} | {s['merged_tracks']} | {s['phantom_tracks']} | {s['tracker_ms']:.2f} |")
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Takipçi karşılaştırması (simülatör, gerçek değerlere karşı)")
    p.add_argument("--trackers", default="ours,rf_bytetrack,rf_ocsort,rf_botsort")
    p.add_argument("--noise", default="clean,realistic,hard")
    p.add_argument("--seeds", type=int, default=3)
    p.add_argument("--minutes", type=float, default=60)
    p.add_argument("--fps", type=float, default=15)
    p.add_argument("--workers", type=int, default=0)
    p.add_argument("--sony-src", help="Sony modlib tracker dosyalarının klasörü (sony_bytetrack için)")
    p.add_argument("--out", help="sonuçları JSON olarak yaz")
    a = p.parse_args(argv)
    jobs = [{"tracker": t, "noise": n, "seed": s, "minutes": a.minutes, "fps": a.fps, "sony_src": a.sony_src}
            for n in a.noise.split(",") for s in range(1, a.seeds + 1) for t in a.trackers.split(",")]
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=a.workers or None) as ex:
        rows = list(ex.map(run_one, jobs))
    summary = summarise(rows)
    print(table(summary))
    print(f"\n{len(jobs)} koşu, {a.minutes:g} dk simülasyon/koşu, {time.time() - t0:.0f} sn")
    if a.out:
        Path(a.out).write_text(json.dumps({"runs": rows, "summary": summary}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
