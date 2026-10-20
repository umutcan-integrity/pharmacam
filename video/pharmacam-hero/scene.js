// PharmaCam — 8 s seamless hero loop for the website (silent, no copy: the page sets the type).
// An aerial drift over the pharmacy plan: customers come in, browse, queue and leave; staff work
// behind the counter; the door line pulses on every crossing. Every motion has a period of 8 s,
// so the last frame flows into the first.
import { TAU, rgba, vignette, radialBlob } from '../../engine/core.js';
import { BG, drawPlan, drawAgent } from '../pharmacam-film/plan.js';

const LOOP = 8;
const Q = [[22, -112], [22, -52], [22, 8], [22, 68]];
const SERVICE = [22, -170];
// routes in loop-local seconds; every route starts and ends outside the door
const ROUTES = {
  shelfQueue: [[0, -400, 440], [0.5, -400, 330], [1.6, -425, 0], [3.4, -425, 0], [4.3, ...Q[3]], [5.2, ...Q[1]], [5.7, ...SERVICE], [6.5, ...SERVICE], [7.1, -200, 150], [7.6, -380, 320], [7.95, -380, 440]],
  browse: [[0, -350, 440], [0.5, -350, 330], [2.1, 425, -60], [4.4, 425, -60], [5.4, 330, 214], [6.4, 330, 214], [7.5, -340, 320], [7.95, -340, 440]],
  queue: [[0, -440, 440], [0.5, -440, 330], [1.8, ...Q[2]], [3.4, ...Q[1]], [4.8, ...Q[0]], [5.3, ...SERVICE], [6.0, ...SERVICE], [7.3, -420, 320], [7.8, -420, 440]],
  quick: [[0, -320, 440], [0.45, -320, 330], [1.7, -200, -100], [3.6, -200, -100], [5.2, -320, 320], [5.6, -320, 440]],
  baby: [[0, -380, 440], [0.5, -380, 330], [1.9, 360, 214], [4.6, 360, 214], [6.3, -360, 320], [6.7, -360, 440]],
};
const PEOPLE = [['shelfQueue', 0], ['browse', 2.7], ['queue', 5.1], ['quick', 1.3], ['baby', 4.0], ['queue', 1.9], ['browse', 6.3]];
const STAFF = [[-90, -264, 0], [150, -264, 2.1]];

const local = (t, off) => (((t + off) % LOOP) + LOOP) % LOOP;
// door crossings (loop-local) → absolute times, repeated across the wrap for continuous ripples
const CROSS = [];
for (const [r, off] of PEOPLE) {
  const kf = ROUTES[r];
  for (const tc of [kf[1][0] + 0.02, kf[kf.length - 2][0] + 0.15]) {
    const a = (((tc - off) % LOOP) + LOOP) % LOOP;
    CROSS.push(a - LOOP, a, a + LOOP);
  }
}

export default {
  draw(ctx, t, api) {
    const { W, H } = api;
    const ph = (t / LOOP) * TAU;
    ctx.fillStyle = rgba(BG); ctx.fillRect(0, 0, W, H);
    radialBlob(ctx, W * (0.5 + 0.08 * Math.sin(ph)), H * 0.35, W * 0.55, [44, 44, 50], 0.6);
    radialBlob(ctx, W * (0.2 + 0.05 * Math.cos(ph)), H * 0.85, W * 0.4, [50, 40, 30], 0.35);

    // slow aerial drift — every term periodic in LOOP
    const s = Math.min(W / 1120, H / 640) * (0.9 + 0.03 * Math.sin(ph));
    const rot = -0.09 + 0.025 * Math.sin(ph + 0.6);
    const px = 40 * Math.sin(ph), py = 18 * Math.cos(ph);
    ctx.save();
    ctx.translate(W / 2 + px, H / 2 + py); ctx.rotate(rot); ctx.scale(s, s);

    const doorGlow = CROSS.reduce((a, tc) => a + (t >= tc ? Math.exp(-(t - tc) * 6) : 0), 0);
    drawPlan(ctx, t, {
      birth: 1,
      doorGlow,
      ripples: CROSS.map(tc => [-380, 320, tc]),
      zone: id => (id === 'queue' ? 0.25 + 0.15 * Math.sin(ph * 2) : id === 'derma' ? 0.12 + 0.12 * Math.sin(ph + 2) : 0),
    });
    // gentle heat under the busiest places
    ctx.save(); ctx.globalCompositeOperation = 'lighter';
    for (const [x, y, r, w, k] of [[22, -80, 220, 0.5, 0], [-425, 0, 150, 0.35, 1.7], [425, -60, 140, 0.28, 3.1]]) {
      const b = 0.75 + 0.25 * Math.sin(ph + k);
      const g = ctx.createRadialGradient(x, y, 0, x, y, r);
      g.addColorStop(0, `rgba(215,60,30,${0.35 * w * b})`); g.addColorStop(0.45, `rgba(255,140,20,${0.22 * w * b})`); g.addColorStop(1, 'rgba(255,190,60,0)');
      ctx.fillStyle = g; ctx.fillRect(x - r, y - r, r * 2, r * 2);
    }
    ctx.restore();

    for (const [x, y, off] of STAFF) {
      const u = ((t + off) / LOOP) * TAU;
      const kf = [[0, x + 50 * Math.sin(u), y + 4 * Math.sin(u * 2)], [LOOP, x + 50 * Math.sin(u), y + 4 * Math.sin(u * 2)]];
      // staff: static keyframes at their current spot, trail from the analytic path
      drawAgent(ctx, kf, LOOP / 2, 'staff');
    }
    for (const [r, off] of PEOPLE) drawAgent(ctx, ROUTES[r], local(t, off), 'customer');
    ctx.restore();
  },
  post(ctx, t, { W, H }) { vignette(ctx, W, H, 0.45); },
};

