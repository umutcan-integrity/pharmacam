// Shared PharmaCam pharmacy floor plan (top-down) — used by the film and the hero loop.
// Plan units are pixels at scale 1, origin at the plan centre, y pointing down (towards the door).
import { TAU, clamp, lerp, prog, rgba, EASE, hash, rrect } from '../../engine/core.js';

export const BG = [10, 10, 11];
export const INK = [245, 245, 247];
export const SILVER = [161, 161, 166];
export const DIM = [58, 58, 63];
export const AMBER = [255, 159, 10];
export const LIVE = [48, 209, 88];

export const PLAN = { x0: -560, x1: 560, y0: -320, y1: 320 };
export const DOOR = { a: [-470, 320], b: [-290, 320] };
// zones: [x0, y0, x1, y1]
export const ZONES = {
  staff: { r: [-230, -306, 262, -224], kind: 'staff' },
  service: { r: [-200, -196, 232, -142], kind: 'service' },
  queue: { r: [-40, -142, 84, 168], kind: 'queue' },
  derma: { r: [-468, -200, -372, 206], kind: 'shelf' },
  vitamin: { r: [372, -224, 468, 104], kind: 'shelf' },
  bebek: { r: [236, 176, 468, 248], kind: 'shelf' },
};
// solid furniture: counter, wall shelves
export const FURNITURE = [
  { r: [-232, -222, 264, -198], fill: [44, 44, 48] },   // counter
  { r: [-556, -224, -476, 228], fill: [30, 30, 33] },   // derma shelf
  { r: [476, -250, 556, 128], fill: [30, 30, 33] },     // vitamin shelf
  { r: [236, 254, 470, 316], fill: [30, 30, 33] },      // baby shelf
  { r: [-226, -316, 258, -308], fill: [30, 30, 33] },   // back cabinets
];

/** Smoothly interpolated position along keyframes [[t, x, y], …]; null outside its life. */
export function pathPos(kf, t) {
  if (t < kf[0][0] || t > kf[kf.length - 1][0]) return null;
  for (let i = 0; i < kf.length - 1; i++) {
    const [t0, x0, y0] = kf[i], [t1, x1, y1] = kf[i + 1];
    if (t <= t1) {
      const e = t1 > t0 ? EASE.css(prog(t, t0, t1)) : 1;
      return [lerp(x0, x1, e), lerp(y0, y1, e)];
    }
  }
  const l = kf[kf.length - 1];
  return [l[1], l[2]];
}

/** Moving = how fast the agent travels right now (for trail strength). */
export function speedAt(kf, t) {
  const a = pathPos(kf, t - 0.04), b = pathPos(kf, t);
  if (!a || !b) return 0;
  return Math.hypot(b[0] - a[0], b[1] - a[1]) / 0.04;
}

const _dots = [];
(() => {
  const sp = 24;
  for (let y = PLAN.y0 + 12; y <= PLAN.y1 - 8; y += sp)
    for (let x = PLAN.x0 + 12; x <= PLAN.x1 - 8; x += sp) _dots.push({ x, y, d: Math.hypot(x - DOOR.a[0] - 90, y - DOOR.a[1]), h: hash(x * 0.37 + y * 1.13) });
})();
export const DOTS = _dots;

export const inRect = (p, r) => p[0] >= r[0] && p[0] <= r[2] && p[1] >= r[1] && p[1] <= r[3];

/**
 * Draw the plan in the current transform (plan units).
 * o.birth: 0→1 dot field + walls appearing (from the door outwards)
 * o.alpha: overall opacity · o.zone(id) → highlight 0..1 · o.ripples: [[x, y, t0]] dot pushes
 */
export function drawPlan(ctx, t, o = {}) {
  const alpha = o.alpha ?? 1, birth = o.birth ?? 1;
  if (alpha <= 0.001) return;
  const maxD = 1250;
  ctx.save();
  ctx.globalAlpha = alpha;

  // floor
  const fp = EASE.expo(prog(birth, 0, 0.6));
  ctx.fillStyle = rgba([18, 18, 20], fp);
  rrect(ctx, PLAN.x0, PLAN.y0, PLAN.x1 - PLAN.x0, PLAN.y1 - PLAN.y0, 18); ctx.fill();

  // dot field: springs in from the door, pushed by ripples
  const buckets = new Map();
  for (const p of DOTS) {
    const local = clamp(birth * 1.35 - p.d / maxD);
    if (local <= 0) continue;
    let dx = 0, dy = 0, glow = 0;
    for (const [rx, ry, t0] of o.ripples ?? []) {
      const u = t - t0;
      if (u < 0 || u > 1.1) continue;
      const d = Math.hypot(p.x - rx, p.y - ry) || 1;
      const w = Math.exp(-Math.pow((d - u * 620) / 46, 2)) * (1 - u / 1.1);
      dx += (p.x - rx) / d * w * 7; dy += (p.y - ry) / d * w * 7; glow += w;
    }
    const a = Math.min(1, (0.17 + 0.06 * p.h) * EASE.expo(local) + glow * 0.55);
    const key = Math.round(a * 20);
    if (!buckets.has(key)) buckets.set(key, []);
    buckets.get(key).push([p.x + dx, p.y + dy, 1.7 + glow * 2.2]);
  }
  for (const [key, pts] of buckets) {
    ctx.fillStyle = rgba(INK, key / 20);
    ctx.beginPath();
    for (const [x, y, r] of pts) { ctx.moveTo(x + r, y); ctx.arc(x, y, r, 0, TAU); }
    ctx.fill();
  }

  // furniture
  const fu = EASE.expo(prog(birth, 0.25, 0.9));
  for (const f of FURNITURE) {
    ctx.fillStyle = rgba(f.fill, fu);
    rrect(ctx, f.r[0], f.r[1], f.r[2] - f.r[0], f.r[3] - f.r[1], 6); ctx.fill();
  }
  // shelf product rows (tiny ticks)
  ctx.fillStyle = rgba([92, 92, 98], 0.6 * fu);
  for (const [x0, y0, x1, y1, vertical] of [[-548, -214, -484, 220, 1], [484, -240, 548, 120, 1], [244, 262, 462, 308, 0]]) {
    if (vertical) for (let y = y0; y < y1; y += 22) ctx.fillRect(x0, y, x1 - x0, 3);
    else for (let x = x0; x < x1; x += 22) ctx.fillRect(x, y0, 3, y1 - y0);
  }

  // walls (drawn as a stroke that grows), door gap left open
  const wp = EASE.expo(prog(birth, 0.1, 1));
  ctx.strokeStyle = rgba(SILVER, 0.55);
  ctx.lineWidth = 3; ctx.lineCap = 'round';
  const wall = [[DOOR.a[0], PLAN.y1], [PLAN.x0, PLAN.y1], [PLAN.x0, PLAN.y0], [PLAN.x1, PLAN.y0], [PLAN.x1, PLAN.y1], [DOOR.b[0], PLAN.y1]];
  let total = 0;
  const segs = [];
  for (let i = 0; i < wall.length - 1; i++) { const l = Math.hypot(wall[i + 1][0] - wall[i][0], wall[i + 1][1] - wall[i][1]); segs.push(l); total += l; }
  let left = total * wp;
  ctx.beginPath(); ctx.moveTo(...wall[0]);
  for (let i = 0; i < segs.length && left > 0; i++) {
    const f = Math.min(1, left / segs[i]);
    ctx.lineTo(lerp(wall[i][0], wall[i + 1][0], f), lerp(wall[i][1], wall[i + 1][1], f));
    left -= segs[i];
  }
  ctx.stroke();

  // zones
  for (const [id, z] of Object.entries(ZONES)) {
    const h = o.zone ? o.zone(id) : 0;
    const base = 0.16 * fu;
    const [x0, y0, x1, y1] = z.r;
    ctx.setLineDash([6, 7]);
    ctx.strokeStyle = rgba(INK, base + h * 0.7);
    ctx.lineWidth = 1.6 + h * 1.4;
    rrect(ctx, x0, y0, x1 - x0, y1 - y0, 10); ctx.stroke();
    ctx.setLineDash([]);
    if (h > 0) { ctx.fillStyle = rgba(INK, 0.07 * h); ctx.fill(); }
  }

  // door line
  const dg = o.doorGlow ?? 0;
  ctx.strokeStyle = rgba(AMBER, 0.35 * fu + 0.65 * Math.min(1, dg));
  ctx.lineWidth = 4 + 4 * Math.min(1, dg);
  ctx.beginPath(); ctx.moveTo(DOOR.a[0] + 6, DOOR.a[1]); ctx.lineTo(DOOR.b[0] - 6, DOOR.b[1]); ctx.stroke();
  if (dg > 0.02) {
    ctx.strokeStyle = rgba(AMBER, 0.18 * Math.min(1, dg));
    ctx.lineWidth = 22;
    ctx.beginPath(); ctx.moveTo(DOOR.a[0] + 6, DOOR.a[1]); ctx.lineTo(DOOR.b[0] - 6, DOOR.b[1]); ctx.stroke();
  }
  ctx.restore();
}

/** A person seen from above: glowing dot + motion trail. kind: 'customer' | 'staff'. */
export function drawAgent(ctx, kf, t, kind = 'customer', o = {}) {
  const p = pathPos(kf, t);
  if (!p) return null;
  const a = (o.alpha ?? 1) * clamp((t - kf[0][0]) / 0.25) * clamp((kf[kf.length - 1][0] - t) / 0.25);
  if (a <= 0) return p;
  const col = kind === 'staff' ? (o.color ?? INK) : AMBER;
  // trail
  for (let k = 8; k >= 1; k--) {
    const q = pathPos(kf, t - k * 0.05);
    if (!q) continue;
    ctx.fillStyle = rgba(col, a * 0.22 * (1 - k / 9));
    ctx.beginPath(); ctx.arc(q[0], q[1], 9 * (1 - k / 14), 0, TAU); ctx.fill();
  }
  // halo
  const g = ctx.createRadialGradient(p[0], p[1], 0, p[0], p[1], 34);
  g.addColorStop(0, rgba(col, 0.38 * a)); g.addColorStop(1, rgba(col, 0));
  ctx.fillStyle = g; ctx.fillRect(p[0] - 34, p[1] - 34, 68, 68);
  ctx.fillStyle = rgba(col, a);
  ctx.beginPath(); ctx.arc(p[0], p[1], 10, 0, TAU); ctx.fill();
  ctx.strokeStyle = rgba(BG, 0.9 * a); ctx.lineWidth = 2.5; ctx.stroke();
  return p;
}
