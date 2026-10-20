// PharmaCam — 20 s product film (120 BPM, 1 bar = 2 s).
// Concept "Kapıdan tezgâha": a customer's journey across a top-down pharmacy plan, each step
// revealing a metric; a privacy turn (pixels → chip → numbers); the dashboard; the end card.
// Layout adapts to the canvas: landscape = copy left / visual right, portrait/square = copy top.
import { TAU, clamp, lerp, prog, rgba, E, EASE, spring, wobble, setFont, wrap, rrect, pill, vignette, chromatic, hash, radialBlob, shake } from '../../engine/core.js';
import { BG, INK, SILVER, AMBER, LIVE, ROSE, PEACH, GOLD, LILAC, drawPlan, drawAgent, pathPos, doorBurst } from './plan.js';

const COPY = {
  tr: {
    l1: 'Bugün kaç kişi geldi?',
    l2: 'PharmaCam sayar.',
    hl: { l1: 'kaç kişi', l2: 'sayar.', l3: 'Hangi raf', l4: 'ne kadar', l5a: 'personel.', l6: 'haritası.', l7a: 'kaydedilmez.', l8: 'Yarını' },
    countLabel: 'bugün giren müşteri',
    l3: 'Hangi raf ilgi görüyor?',
    shelf: 'Dermokozmetik',
    dwell: 'sn duruş',
    l4: 'Sırada ne kadar beklendi?',
    wait: 'bekleme',
    served: 'hizmette',
    l5a: 'Beyaz önlük: personel.',
    l5b: 'Sayılmaz.',
    staff: 'Personel',
    l6: 'Mağazanızın ısı haritası.',
    l7a: 'Görüntü kaydedilmez.',
    l7b: 'Yalnızca sayılar.',
    chip: 'çip üzerinde yapay zekâ',
    n1: 'içeride', n2: 'sırada', n3: 'bugün giren',
    l8: 'Yarını bugünden bilin.',
    tiles: ['Şu an içeride', 'Sırada', 'Bugün giren', 'Ort. bekleme'],
    waitVal: '1 dk 04 sn',
    tomorrow: 'Yarın ≈ 477',
    fcTitle: 'Yarın için saatlik tahmin',
    tagline: 'Eczanenizin nabzı, gerçek zamanlı.',
    cta: 'Ücretsiz keşif görüşmesi',
    phone: '+90 530 244 27 58   ·   +90 535 366 15 63',
    min: 'dk', sec: 'sn',
  },
  en: {
    l1: 'How many came in today?',
    l2: 'PharmaCam counts.',
    hl: { l1: 'How many', l2: 'counts.', l3: 'Which shelf', l4: 'How long', l5a: 'staff.', l6: 'heatmap.', l7a: 'stored.', l8: 'tomorrow,' },
    countLabel: 'customers today',
    l3: 'Which shelf gets attention?',
    shelf: 'Skincare',
    dwell: 's dwell',
    l4: 'How long did the queue wait?',
    wait: 'waiting',
    served: 'served',
    l5a: 'White coat: staff.',
    l5b: 'Not counted.',
    staff: 'Staff',
    l6: 'Your store heatmap.',
    l7a: 'No video is stored.',
    l7b: 'Only numbers.',
    chip: 'on-chip AI',
    n1: 'inside', n2: 'in queue', n3: 'today',
    l8: 'Know tomorrow, today.',
    tiles: ['Inside now', 'In queue', 'Today', 'Avg. wait'],
    waitVal: '1 min 04 s',
    tomorrow: 'Tomorrow ≈ 477',
    fcTitle: 'Hourly forecast for tomorrow',
    tagline: 'The pulse of your pharmacy, live.',
    cta: 'Book a free walkthrough',
    phone: '+90 530 244 27 58   ·   +90 535 366 15 63',
    min: 'min', sec: 's',
  },
};

// ───────────────────────────────────────────── agents (plan units, scene seconds)
const Q = [[22, -112], [22, -52], [22, 8], [22, 68]];
const SERVICE = [22, -170];
const AGENTS = {
  c1: [[2.15, -400, 440], [2.6, -400, 330], [3.35, -425, 40], [3.6, -425, 12], [6.2, -425, 12], [6.85, ...Q[3]], [7.75, ...Q[3]], [8.15, ...Q[2]], [13.8, ...Q[2]]],
  c2: [[2.35, -350, 440], [2.8, -350, 330], [3.9, 425, -60], [13.8, 425, -60]],
  c3: [[2.5, -440, 440], [2.95, -440, 330], [3.7, ...Q[0]], [7.3, ...Q[0]], [7.7, ...SERVICE], [11.0, ...SERVICE], [11.7, -200, 150], [12.4, -380, 300], [12.8, -380, 440]],
  c4: [[2.7, -310, 440], [3.15, -320, 330], [3.95, ...Q[1]], [7.45, ...Q[1]], [7.85, ...Q[0]], [13.8, ...Q[0]]],
  c5: [[2.95, -380, 440], [3.4, -380, 330], [4.15, ...Q[2]], [7.6, ...Q[2]], [8.0, ...Q[1]], [13.8, ...Q[1]]],
  c6: [[3.1, -330, 440], [3.55, -330, 330], [4.4, 330, 214], [13.8, 330, 214]],
};
const STAFF = {
  s1: [[0, -90, -266], [2.5, -40, -262], [5, -130, -270], [7.5, -70, -262], [10, -30, -268], [13.8, -100, -264]],
  s2: [[0, 150, -262], [3, 196, -268], [6, 128, -262], [9, 176, -266], [13.8, 140, -262]],
};
const CROSSINGS = [2.6, 2.8, 2.95, 3.15, 3.4, 3.55];

// heat sources (plan units) for bar 5: [x, y, radius, weight, delay]
const HEAT = [[22, -60, 190, 1.0, 0.0], [22, -168, 160, 0.95, 0.12], [-425, 10, 150, 0.85, 0.24], [425, -60, 130, 0.7, 0.36],
  [330, 214, 110, 0.55, 0.48], [-380, 260, 100, 0.45, 0.6], [-180, 120, 120, 0.3, 0.7]];

// ───────────────────────────────────────────── helpers
function layoutFor(W, H) {
  const land = W / H > 1.2;
  if (land) {
    const s = Math.min((W * 0.56) / 1120, (H * 0.8) / 640);
    return { land, s, ax: W * 0.66, ay: H * 0.53, tx: W * 0.0625, ty: H * 0.3, tW: W * 0.32, hs: Math.round(H * 0.074) };
  }
  const s = Math.min((W * 0.9) / 1120, (H * 0.48) / 640);
  return { land, s, ax: W / 2, ay: H * 0.64, tx: W * 0.08, ty: H * (W === H ? 0.11 : 0.16), tW: W * 0.84, hs: Math.round(W * 0.075) };
}

/** Camera: which plan point sits on the anchor, and how far we are zoomed in. */
function camera(t) {
  let fx = 0, fy = 0, z = lerp(1.08, 1.0, EASE.css(prog(t, 0, 3.8))), rot = 0;
  const shots = [[3.8, 4.45, -380, 10, 1.65, -0.035], [5.8, 6.45, 22, -36, 1.55, 0.03], [7.8, 8.45, 30, -248, 1.8, -0.025], [9.8, 10.6, 0, 0, 1.0, 0.0], [11.8, 12.6, 0, 0, 0.9, 0.0]];
  for (const [t0, t1, x, y, zz, rr] of shots) {
    const e = EASE.expo(prog(t, t0, t1)), b = E.outBack(prog(t, t0, t1), 1.4);
    fx = lerp(fx, x, e); fy = lerp(fy, y, e); z = lerp(z, zz, b); rot = lerp(rot, rr, e);
  }
  return { fx, fy, z, rot };
}

/** A glossy diagonal light sweep across the frame (used on section changes). */
function sweep(ctx, W, H, t, t0, dur = 0.55, a = 0.13) {
  const u = prog(t, t0, t0 + dur);
  if (u <= 0 || u >= 1) return;
  const x = lerp(-W * 0.4, W * 1.4, E.inOutCubic(u));
  const g = ctx.createLinearGradient(x - 260, 0, x + 260, H * 0.35);
  g.addColorStop(0, 'rgba(255,255,255,0)'); g.addColorStop(0.5, `rgba(255,235,225,${a})`); g.addColorStop(1, 'rgba(255,255,255,0)');
  ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
}
const CUSTOMER_COLORS = { c1: [ROSE, PEACH], c2: [GOLD, PEACH], c3: [PEACH, ROSE], c4: [GOLD, ROSE], c5: [ROSE, GOLD], c6: [PEACH, GOLD] };
const ZONE_COLORS = { derma: ROSE, vitamin: ROSE, bebek: PEACH, queue: GOLD, service: PEACH, staff: LILAC };
const DOOR_X = [-400, -350, -440, -320, -380, -330];

/** A headline that rises out of a mask and leaves upwards. Returns block height. */
function headline(ctx, text, x, y, size, maxW, t0, t1, t, { color = INK, weight = 800, hl = null } = {}) {
  setFont(ctx, weight, size, 'Inter', -size * 0.035);
  ctx.textAlign = 'left'; ctx.textBaseline = 'alphabetic';
  const lines = wrap(ctx, text, maxW);
  const lh = size * 1.08;
  if (t < t0 - 0.01 || t > t1 + 0.45) return lines.length * lh;
  lines.forEach((ln, i) => {
    const pin = EASE.expo(prog(t, t0 + i * 0.07, t0 + i * 0.07 + 0.7));
    const pout = E.inExpo(prog(t, t1 + i * 0.04, t1 + i * 0.04 + 0.35));
    const base = y + i * lh;
    ctx.save();
    ctx.beginPath(); ctx.rect(x - 20, base - size * 1.08, maxW + 400, size * 1.45); ctx.clip();
    const yy = base + (1 - pin) * size * 1.15 - pout * size * 1.2;
    ctx.fillStyle = rgba(color, 1);
    ctx.fillText(ln, x, yy);
    const k = hl ? ln.indexOf(hl) : -1;
    if (k >= 0) {   // keyword in a warm gradient, with an underline that swipes in
      const x0 = x + ctx.measureText(ln.slice(0, k)).width, w = ctx.measureText(hl).width;
      const g = ctx.createLinearGradient(x0, 0, x0 + w, 0);
      g.addColorStop(0, rgba(ROSE)); g.addColorStop(0.55, rgba(PEACH)); g.addColorStop(1, rgba(GOLD));
      ctx.fillStyle = g; ctx.fillText(hl, x0, yy);
      const u = EASE.expo(prog(t, t0 + 0.35, t0 + 0.95)) * (1 - pout);
      if (u > 0) { rrect(ctx, x0, yy + size * 0.16, w * u, size * 0.075, size * 0.04); ctx.fill(); }
    }
    ctx.restore();
  });
  return lines.length * lh;
}

/** Glass pill with text, scaled from its centre by s. */
function chipLabel(ctx, x, y, text, s, { size = 30, dot = null, fill = null, ink = INK, weight = 600, alpha = 1 } = {}) {
  if (s <= 0.001 || alpha <= 0) return;
  setFont(ctx, weight, size, 'Inter', -0.3);
  const tw = ctx.measureText(text).width;
  const pad = size * 0.62, h = size * 1.75, w = tw + pad * 2 + (dot ? size * 0.9 : 0);
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.translate(x, y); ctx.scale(s, s);
  ctx.shadowColor = 'rgba(0,0,0,0.45)'; ctx.shadowBlur = 24; ctx.shadowOffsetY = 8;
  pill(ctx, 0, 0, w, h);
  ctx.fillStyle = fill ?? 'rgba(36,36,40,0.92)'; ctx.fill();
  ctx.shadowColor = 'transparent';
  ctx.strokeStyle = 'rgba(255,255,255,0.16)'; ctx.lineWidth = 1.5; ctx.stroke();
  let x0 = -w / 2 + pad;
  if (dot) { ctx.fillStyle = rgba(dot); ctx.beginPath(); ctx.arc(x0 + size * 0.28, 0, size * 0.22, 0, TAU); ctx.fill(); x0 += size * 0.9; }
  ctx.fillStyle = rgba(ink); ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
  ctx.fillText(text, x0, size * 0.04);
  ctx.restore();
}

export function logoTile(ctx, x, y, size, s = 1) {
  if (s <= 0.001) return;
  ctx.save(); ctx.translate(x, y); ctx.scale(s, s);
  const r = size * 0.24;
  const g = ctx.createLinearGradient(0, -size / 2, 0, size / 2);
  g.addColorStop(0, '#3a3a3f'); g.addColorStop(1, '#1c1c1f');
  ctx.shadowColor = 'rgba(0,0,0,0.6)'; ctx.shadowBlur = size * 0.3; ctx.shadowOffsetY = size * 0.08;
  rrect(ctx, -size / 2, -size / 2, size, size, r); ctx.fillStyle = g; ctx.fill();
  ctx.shadowColor = 'transparent';
  ctx.strokeStyle = 'rgba(255,255,255,0.18)'; ctx.lineWidth = Math.max(1, size * 0.015); ctx.stroke();
  const a = size * 0.19, L = size * 0.28;   // white cross
  ctx.fillStyle = '#fff';
  rrect(ctx, -a / 2, -L, a, 2 * L, a * 0.18); ctx.fill();
  rrect(ctx, -L, -a / 2, 2 * L, a, a * 0.18); ctx.fill();
  ctx.restore();
}

const fmtWait = (sec, c) => `${Math.floor(sec / 60)} ${c.min} ${String(Math.floor(sec % 60)).padStart(2, '0')} ${c.sec}`;

// ───────────────────────────────────────────── privacy turn data
const FRAME = { w: 600, h: 360 };
const PEOPLE = [[-170, 30, [196, 120, 60]], [-30, -10, [60, 70, 92]], [70, 40, [170, 50, 50]], [190, -20, [235, 235, 238]]];
function frameColor(x, y) {
  // soft "camera view": warm floor + blurred people (what the sensor sees — never stored)
  const k = (y + FRAME.h / 2) / FRAME.h;
  let c = [lerp(150, 196, k), lerp(142, 186, k), lerp(130, 170, k)];
  for (const [px, py, col] of PEOPLE) {
    const w = Math.exp(-(Math.pow((x - px) / 34, 2) + Math.pow((y - py) / 92, 2)));
    c = [lerp(c[0], col[0], w), lerp(c[1], col[1], w), lerp(c[2], col[2], w)];
  }
  return c;
}
const PARTS = [];
for (let y = -FRAME.h / 2 + 10; y < FRAME.h / 2; y += 20)
  for (let x = -FRAME.w / 2 + 10; x < FRAME.w / 2; x += 20) PARTS.push({ x, y, d: Math.hypot(x, y), c: frameColor(x, y), h: hash(x * 3.1 + y * 7.7) });

// ───────────────────────────────────────────── scene
export default {
  draw(ctx, t, api) {
    const { W, H } = api;
    const c = COPY[api.lang] ?? COPY.tr;
    const L = layoutFor(W, H);
    ctx.fillStyle = rgba(BG); ctx.fillRect(0, 0, W, H);

    // ambient light (slow, neutral)
    // warm, moving light — rose, gold and lilac washes drifting behind everything
    radialBlob(ctx, W * (0.78 + 0.08 * Math.sin(t * 0.35)), H * (0.18 + 0.06 * Math.cos(t * 0.3)), W * 0.55, ROSE, 0.30);
    radialBlob(ctx, W * (0.12 + 0.06 * Math.cos(t * 0.28)), H * (0.92 + 0.05 * Math.sin(t * 0.4)), W * 0.5, GOLD, 0.24);
    radialBlob(ctx, W * (0.45 + 0.1 * Math.sin(t * 0.22 + 1)), H * (0.55 + 0.08 * Math.sin(t * 0.33)), W * 0.4, LILAC, 0.14);

    const [sx, sy] = shake(t, [[12.95, 7]]);
    ctx.save(); ctx.translate(sx, sy);

    // ── the plan (bars 0–6)
    const cam = camera(t);
    const planAlpha = (1 - 0.75 * EASE.expo(prog(t, 11.9, 12.5))) * (1 - E.inCubic(prog(t, 13.4, 14.1)));
    const S = L.s * cam.z;
    const cr = Math.cos(cam.rot), sr = Math.sin(cam.rot);
    const toScreen = (x, y) => { const dx = (x - cam.fx) * S, dy = (y - cam.fy) * S; return [L.ax + dx * cr - dy * sr, L.ay + dx * sr + dy * cr]; };
    if (planAlpha > 0.001) {
      ctx.save();
      ctx.translate(L.ax, L.ay); ctx.rotate(cam.rot); ctx.scale(S, S); ctx.translate(-cam.fx, -cam.fy);
      const doorGlow = CROSSINGS.reduce((a, tc) => a + (t >= tc ? Math.exp(-(t - tc) * 6) : 0), 0);
      drawPlan(ctx, t, {
        alpha: planAlpha,
        birth: prog(t, 0.05, 1.9),
        doorGlow,
        ripples: CROSSINGS.map(tc => [-380, 320, tc]).concat([[-425, 12, 3.6], [22, -112, 3.7], [22, -170, 7.7]]),
        zoneColor: id => ZONE_COLORS[id],
        zone: id => {
          if (id === 'derma') return EASE.expo(prog(t, 4.0, 4.5)) * (1 - prog(t, 6.0, 6.4));
          if (id === 'queue') return EASE.expo(prog(t, 6.0, 6.5)) * (1 - prog(t, 8.0, 8.4));
          if (id === 'service') return EASE.expo(prog(t, 7.5, 7.9)) * (1 - prog(t, 8.2, 8.6));
          if (id === 'staff') return EASE.expo(prog(t, 8.1, 8.6)) * (1 - prog(t, 9.9, 10.3));
          return 0;
        },
      });
      // heatmap (bar 5): accumulated dwell glows over the plan
      if (t > 10.1) {
        ctx.save(); ctx.globalCompositeOperation = 'lighter'; ctx.globalAlpha = planAlpha;
        for (const [x, y, r, w, d] of HEAT) {
          const e = EASE.expo(prog(t, 10.1 + d, 11.1 + d)) * (1 + 0.04 * Math.sin(t * 3 + x));
          if (e <= 0) continue;
          const g = ctx.createRadialGradient(x, y, 0, x, y, r * e);
          g.addColorStop(0, `rgba(215,40,30,${0.62 * w})`); g.addColorStop(0.35, `rgba(255,120,10,${0.42 * w})`);
          g.addColorStop(0.7, `rgba(255,190,60,${0.16 * w})`); g.addColorStop(1, 'rgba(255,200,80,0)');
          ctx.fillStyle = g; ctx.fillRect(x - r, y - r, r * 2, r * 2);
        }
        ctx.restore();
      }
      // people
      const staffGrey = EASE.expo(prog(t, 8.9, 9.4));
      for (const kf of Object.values(STAFF)) drawAgent(ctx, kf, t, 'staff', { alpha: planAlpha, color: [lerp(245, 110, staffGrey), lerp(245, 110, staffGrey), lerp(247, 116, staffGrey)] });
      CROSSINGS.forEach((tc, i) => doorBurst(ctx, DOOR_X[i], 318, tc, t, [ROSE, GOLD, PEACH]));
      for (const [k, kf] of Object.entries(AGENTS)) drawAgent(ctx, kf, t, 'customer', { alpha: planAlpha * (1 - 0.6 * prog(t, 10.2, 10.8)), color: CUSTOMER_COLORS[k][0], color2: CUSTOMER_COLORS[k][1], halo: 42 });
      // dwell ring around the shelf customer (bar 2)
      if (t > 3.6 && t < 6.3) {
        const p = pathPos(AGENTS.c1, t);
        ctx.strokeStyle = rgba(ROSE, 0.95 * (1 - prog(t, 6.0, 6.3)));
        ctx.lineWidth = 4; ctx.lineCap = 'round';
        ctx.beginPath(); ctx.arc(p[0], p[1], 26, -Math.PI / 2, -Math.PI / 2 + TAU * prog(t, 3.65, 5.7)); ctx.stroke();
      }
      ctx.restore();

      // screen-space chips (constant size) that follow the plan
      { // bar 2: shelf name + dwell seconds
        const out = 1 - E.inBack(prog(t, 5.85, 6.15), 2);
        const p = pathPos(AGENTS.c1, Math.min(t, 6.1)) ?? [-425, 12];
        const [x, y] = toScreen(p[0], p[1]);
        chipLabel(ctx, x + 40, y - 120, c.shelf, spring(t - 4.2, 13, 5.5) * out, { size: 30, dot: ROSE });
        const secs = Math.round(24 * EASE.css(prog(t, 3.7, 5.6)));
        chipLabel(ctx, x + 40, y + 108, `${secs} ${c.dwell}`, spring(t - 4.45, 15, 7) * out, { size: 30 });
      }
      { // bar 3: wait timer on the queue head, then "served"
        const p = pathPos(AGENTS.c3, Math.min(t, 7.3)) ?? Q[0];
        const [x, y] = toScreen(p[0], p[1]);
        const waitS = 58 + 6 * prog(t, 6.2, 7.2);
        chipLabel(ctx, x + 210, y + 4, `${c.wait} ${fmtWait(waitS, c)}`, spring(t - 6.25, 15, 7) * (1 - E.inBack(prog(t, 7.25, 7.5), 2)), { size: 30, dot: GOLD });
        const [x2, y2] = toScreen(...SERVICE);
        chipLabel(ctx, x2 + 160, y2 - 6, c.served, spring(t - 7.75, 15, 7) * (1 - E.inBack(prog(t, 7.95, 8.2), 2)), { size: 30, dot: LIVE });
      }
      for (const [k, kf] of Object.entries(STAFF)) { // bar 4: staff chips
        const s = spring(t - (k === 's1' ? 8.35 : 8.47), 15, 7) * (1 - E.inBack(prog(t, 9.8, 10.1), 2));
        const p = pathPos(kf, t);
        if (!p) continue;
        const [x, y] = toScreen(p[0], p[1]);
        chipLabel(ctx, x, y + 80, c.staff, s, { size: 28, dot: LILAC });
      }
    }

    // ── privacy turn (bar 6)
    if (t >= 11.9 && t < 14.6) {
      const cx = L.ax, cy = L.ay;
      const fIn = EASE.expo(prog(t, 12.0, 12.45));
      const scatter = prog(t, 12.5, 13.0);
      if (scatter < 0.02 && fIn > 0) { // the camera view — shown briefly, then dissolved
        ctx.save(); ctx.globalAlpha = fIn;
        ctx.translate(cx, cy); ctx.scale(0.9 + 0.1 * fIn, 0.9 + 0.1 * fIn);
        rrect(ctx, -FRAME.w / 2, -FRAME.h / 2, FRAME.w, FRAME.h, 18);
        ctx.save(); ctx.clip();
        for (const p of PARTS) { ctx.fillStyle = rgba(p.c); ctx.fillRect(p.x - 10, p.y - 10, 20.5, 20.5); }
        ctx.restore();
        ctx.strokeStyle = 'rgba(255,255,255,0.25)'; ctx.lineWidth = 2; ctx.stroke();
        ctx.strokeStyle = rgba(INK, 0.9); ctx.lineWidth = 4; ctx.lineCap = 'round';
        for (const [kx, ky] of [[-1, -1], [1, -1], [1, 1], [-1, 1]]) {     // viewfinder corners
          const x = kx * (FRAME.w / 2 + 16), y = ky * (FRAME.h / 2 + 16);
          ctx.beginPath(); ctx.moveTo(x, y - ky * 34); ctx.lineTo(x, y); ctx.lineTo(x - kx * 34, y); ctx.stroke();
        }
        ctx.restore();
      }
      if (scatter >= 0.02 && t < 13.05) { // pixels sucked into the chip
        ctx.save(); ctx.translate(cx, cy);
        for (const p of PARTS) {
          const u = E.inExpo(clamp(scatter * 1.25 - (1 - p.d / 360) * 0.25 - p.h * 0.08));
          const ang = Math.atan2(p.y, p.x) + u * 1.6, r = p.d * (1 - u);
          const sz = lerp(17, 3, u);
          ctx.fillStyle = rgba(p.c, 1 - 0.5 * u);
          ctx.fillRect(Math.cos(ang) * r - sz / 2, Math.sin(ang) * r - sz / 2, sz, sz);
        }
        ctx.restore();
      }
      const cs = spring(t - 12.55, 14, 6) * (1 + 0.18 * wobble(t - 13.0)) * (1 - E.inBack(prog(t, 14.0, 14.3), 2));
      if (cs > 0.001) { // the chip
        const flash = t > 13.0 ? Math.exp(-(t - 13.0) * 5) : 0;
        ctx.save(); ctx.translate(cx, cy); ctx.scale(cs, cs);
        ctx.shadowColor = 'rgba(0,0,0,0.6)'; ctx.shadowBlur = 40; ctx.shadowOffsetY = 12;
        rrect(ctx, -78, -78, 156, 156, 22); ctx.fillStyle = rgba([30, 30, 34]); ctx.fill();
        ctx.restore();
        chromatic(ctx, INK, 9 * flash, (col, dx) => {
          ctx.save(); ctx.translate(cx + dx, cy); ctx.scale(cs, cs);
          ctx.fillStyle = rgba(col, 0.9);
          for (let i = 0; i < 6; i++) {
            const o = -55 + i * 22;
            ctx.fillRect(o - 3, -92, 6, 16); ctx.fillRect(o - 3, 76, 6, 16); ctx.fillRect(-92, o - 3, 16, 6); ctx.fillRect(76, o - 3, 16, 6);
          }
          rrect(ctx, -78, -78, 156, 156, 22);
          ctx.strokeStyle = rgba(col, 0.6); ctx.lineWidth = 2.5; ctx.stroke();
          rrect(ctx, -40, -40, 80, 80, 10); ctx.strokeStyle = rgba(col, 0.9); ctx.lineWidth = 3; ctx.stroke();
          setFont(ctx, 700, 16, 'Inter', 0.3); ctx.fillStyle = rgba(col, 0.95); ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
          ctx.fillText('IMX500', 0, 0);
          ctx.restore();
        });
        if (t > 13.0 && t < 13.8) {
          const u = prog(t, 13.0, 13.8);
          ctx.strokeStyle = rgba(INK, 0.5 * (1 - u)); ctx.lineWidth = 3;
          ctx.beginPath(); ctx.arc(cx, cy, 90 + EASE.expo(u) * 420, 0, TAU); ctx.stroke();
        }
        setFont(ctx, 500, 28, 'Inter', 0); ctx.fillStyle = rgba(SILVER, EASE.expo(prog(t, 13.05, 13.45)) * (1 - prog(t, 13.85, 14.1)));
        ctx.textAlign = 'center'; ctx.textBaseline = 'alphabetic';
        ctx.fillText(c.chip, cx, cy + 136);
      }
    }

    // ── numbers leave the chip (13.1 →) and become dashboard tiles (14.0 →)
    const tiles = [[c.tiles[0], '7'], [c.tiles[1], '4'], [c.tiles[2], '452'], [c.tiles[3], c.waitVal]];
    const pills = [[`7 ${c.n1}`, -270, -170], [`4 ${c.n2}`, 280, -150], [`452 ${c.n3}`, 0, 236]];
    const P = { w: Math.min(980, W * (L.land ? 0.5 : 0.86)), h: 600 };
    P.x = L.ax - P.w / 2; P.y = L.ay - P.h / 2;
    const tileW = (P.w - 3 * 22) / 2, tileH = 120;
    const tilePos = i => [P.x + 22 + (i % 2) * (tileW + 22), P.y + 22 + Math.floor(i / 2) * (tileH + 18)];
    const morph = EASE.expo(prog(t, 14.0, 14.5));
    const dashOut = E.inBack(prog(t, 15.75, 16.05), 1.8);
    if (t > 13.1 && t < 16.1) {
      const pa = EASE.expo(prog(t, 14.0, 14.45)) * (1 - dashOut);
      let peak = null;
      if (pa > 0.001) { // glass dashboard panel + forecast
        ctx.save(); ctx.globalAlpha = pa;
        ctx.translate(L.ax, L.ay); ctx.scale(0.94 + 0.06 * pa, 0.94 + 0.06 * pa); ctx.translate(-L.ax, -L.ay);
        ctx.shadowColor = 'rgba(0,0,0,0.55)'; ctx.shadowBlur = 60; ctx.shadowOffsetY = 24;
        rrect(ctx, P.x, P.y, P.w, P.h, 28);
        const g = ctx.createLinearGradient(P.x, P.y, P.x + P.w, P.y + P.h);
        g.addColorStop(0, 'rgba(255,255,255,0.10)'); g.addColorStop(1, 'rgba(255,255,255,0.035)');
        ctx.fillStyle = g; ctx.fill(); ctx.shadowColor = 'transparent';
        ctx.strokeStyle = 'rgba(255,255,255,0.16)'; ctx.lineWidth = 1.5; ctx.stroke();
        const gx = P.x + 40, gw = P.w - 80, gy = P.y + 340, gh = 210;
        setFont(ctx, 600, 26, 'Inter', -0.2); ctx.fillStyle = rgba(SILVER); ctx.textAlign = 'left'; ctx.textBaseline = 'alphabetic';
        ctx.fillText(c.fcTitle, gx, gy - 18);
        const hours = [0.12, 0.34, 0.5, 0.62, 0.46, 0.4, 0.5, 0.56, 0.6, 0.82, 0.92, 1.0, 0.8, 0.44, 0.2];
        const pt = (i, v) => [gx + (i / (hours.length - 1)) * gw, gy + gh - v * (gh - 30)];
        const draw = EASE.css(prog(t, 14.55, 15.45));
        ctx.save(); ctx.beginPath(); ctx.rect(gx - 10, gy - 40, (gw + 20) * draw, gh + 60); ctx.clip();
        ctx.beginPath();
        hours.forEach((v, i) => { const [x, y] = pt(i, v + 0.06 + 0.06 * v); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
        for (let i = hours.length - 1; i >= 0; i--) { const [x, y] = pt(i, Math.max(0, hours[i] - 0.06 - 0.06 * hours[i])); ctx.lineTo(x, y); }
        ctx.closePath();
        const bg2 = ctx.createLinearGradient(gx, 0, gx + gw, 0); bg2.addColorStop(0, rgba(ROSE, 0.2)); bg2.addColorStop(1, rgba(GOLD, 0.2));
        ctx.fillStyle = bg2; ctx.fill();
        ctx.beginPath();
        hours.forEach((v, i) => { const [x, y] = pt(i, v); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
        const lg = ctx.createLinearGradient(gx, 0, gx + gw, 0); lg.addColorStop(0, rgba(ROSE)); lg.addColorStop(0.6, rgba(PEACH)); lg.addColorStop(1, rgba(GOLD));
        ctx.strokeStyle = lg; ctx.lineWidth = 5; ctx.lineJoin = 'round'; ctx.stroke();
        ctx.restore();
        ctx.strokeStyle = 'rgba(255,255,255,0.12)'; ctx.lineWidth = 1.5;
        ctx.beginPath(); ctx.moveTo(gx, gy + gh); ctx.lineTo(gx + gw, gy + gh); ctx.stroke();
        peak = pt(11, 1.0);
        const ps = spring(t - 15.25, 15, 7);
        if (ps > 0) { ctx.fillStyle = rgba(GOLD); ctx.shadowColor = rgba(GOLD, 0.8); ctx.shadowBlur = 20; ctx.beginPath(); ctx.arc(peak[0], peak[1], 9 * clamp(ps), 0, TAU); ctx.fill(); ctx.shadowBlur = 0; ctx.shadowColor = 'transparent'; }
        ctx.restore();
        chipLabel(ctx, peak[0], peak[1] - 56, c.tomorrow, spring(t - 15.3, 15, 7) * (1 - dashOut), { size: 32, fill: 'rgba(245,245,247,0.96)', ink: [10, 10, 11], weight: 700 });
      }
      for (let i = 0; i < 4; i++) { // pills → tiles
        const inT = i < 3 ? 13.15 + i * 0.12 : 14.25;
        const sp = spring(t - inT, 13, 6.5);
        if (sp <= 0.001) continue;
        const [tx0, ty0] = tilePos(i);
        const tcx = tx0 + tileW / 2, tcy = ty0 + tileH / 2;
        const from = i < 3 ? [L.ax + pills[i][1], L.ay + pills[i][2]] : [tcx, tcy];
        const m = i < 3 ? morph : 1;
        const x = lerp(from[0], tcx, m), y = lerp(from[1], tcy, m);
        if (m < 0.5 && i < 3) {
          chipLabel(ctx, x, y, pills[i][0], sp * (1 - m * 2), { size: 34, weight: 700, fill: 'rgba(245,245,247,0.96)', ink: [10, 10, 11] });
        } else {
          const k = i < 3 ? (m - 0.5) * 2 : clamp(sp);
          const w = lerp(160, tileW, EASE.expo(k)), h = lerp(56, tileH, EASE.expo(k));
          ctx.save(); ctx.globalAlpha = 1 - dashOut;
          rrect(ctx, x - w / 2, y - h / 2, w, h, 18); ctx.fillStyle = 'rgba(255,255,255,0.07)'; ctx.fill();
          ctx.strokeStyle = 'rgba(255,255,255,0.12)'; ctx.lineWidth = 1.5; ctx.stroke();
          const ac = [ROSE, GOLD, PEACH, LILAC][i];
          ctx.save(); ctx.clip(); ctx.fillStyle = rgba(ac, 0.95); ctx.fillRect(x - w / 2, y - h / 2, w, 5);
          const tg = ctx.createRadialGradient(x - w / 2, y - h / 2, 0, x - w / 2, y - h / 2, w * 0.7);
          tg.addColorStop(0, rgba(ac, 0.22)); tg.addColorStop(1, rgba(ac, 0)); ctx.fillStyle = tg; ctx.fillRect(x - w / 2, y - h / 2, w, h);
          ctx.restore();
          ctx.globalAlpha = (1 - dashOut) * clamp(k * 1.6 - 0.4);
          setFont(ctx, 500, 24, 'Inter', 0); ctx.fillStyle = rgba(SILVER); ctx.textAlign = 'left'; ctx.textBaseline = 'alphabetic';
          ctx.fillText(tiles[i][0], x - w / 2 + 24, y - h / 2 + 40);
          setFont(ctx, 800, i === 3 ? 46 : 54, 'Inter', -1.5); ctx.fillStyle = rgba(INK);
          ctx.fillText(tiles[i][1], x - w / 2 + 24, y + h / 2 - 22);
          ctx.restore();
        }
      }
    }
    ctx.restore(); // shake

    // ── copy column (darkened behind it so the plan never fights the type)
    if (t < 16.05) {
      if (L.land) {
        const g = ctx.createLinearGradient(0, 0, W * 0.5, 0);
        g.addColorStop(0, rgba(BG, 0.86)); g.addColorStop(0.6, rgba(BG, 0.6)); g.addColorStop(1, rgba(BG, 0));
        ctx.fillStyle = g; ctx.fillRect(0, 0, W * 0.5, H);
      } else {
        const g = ctx.createLinearGradient(0, 0, 0, H * 0.45);
        g.addColorStop(0, rgba(BG, 0.94)); g.addColorStop(0.7, rgba(BG, 0.7)); g.addColorStop(1, rgba(BG, 0));
        ctx.fillStyle = g; ctx.fillRect(0, 0, W, H * 0.45);
      }
      const la = EASE.expo(prog(t, 0.5, 1.2)) * (1 - prog(t, 15.7, 16.0)); // small lockup top-left
      if (la > 0) {
        const ly = L.land ? H * 0.1 : H * 0.055;
        ctx.save(); ctx.globalAlpha = la;
        logoTile(ctx, L.tx + 22, ly, 44);
        setFont(ctx, 700, 30, 'Inter', -0.6); ctx.fillStyle = rgba(INK); ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
        ctx.fillText('PharmaCam', L.tx + 58, ly + 1);
        ctx.restore();
      }
      const hs = L.hs, x = L.tx, y = L.ty + hs;
      headline(ctx, c.l1, x, y, hs, L.tW, 0.25, 1.75, t, { hl: c.hl.l1 });
      headline(ctx, c.l2, x, y, hs, L.tW, 2.15, 3.7, t, { hl: c.hl.l2 });
      if (t > 2.2 && t < 4.2) { // live counter under "PharmaCam sayar."
        const p = EASE.expo(prog(t, 2.3, 3.75));
        const out = E.inExpo(prog(t, 3.75, 4.1));
        const base = y + hs * 1.25 + 150;
        ctx.save(); ctx.beginPath(); ctx.rect(x - 10, base - 170, L.tW + 200, 210); ctx.clip();
        setFont(ctx, 800, 168, 'Inter', -7); ctx.textAlign = 'left'; ctx.textBaseline = 'alphabetic';
        const cg = ctx.createLinearGradient(x, base - 150, x + 340, base);
        cg.addColorStop(0, rgba(ROSE)); cg.addColorStop(0.5, rgba(PEACH)); cg.addColorStop(1, rgba(GOLD));
        ctx.shadowColor = rgba(ROSE, 0.55); ctx.shadowBlur = 50;
        ctx.fillStyle = cg;
        ctx.fillText(String(Math.round(452 * p)), x - 6, base + out * 200 + (1 - EASE.expo(prog(t, 2.25, 2.7))) * 180);
        ctx.shadowBlur = 0; ctx.shadowColor = 'transparent';
        ctx.restore();
        setFont(ctx, 600, 32, 'Inter', 0); ctx.fillStyle = rgba(PEACH, (1 - out) * EASE.expo(prog(t, 2.5, 2.9)));
        ctx.fillText(c.countLabel, x, base + 56);
      }
      headline(ctx, c.l3, x, y, hs, L.tW, 4.15, 5.7, t, { hl: c.hl.l3 });
      headline(ctx, c.l4, x, y, hs, L.tW, 6.15, 7.7, t, { hl: c.hl.l4 });
      const h5 = headline(ctx, c.l5a, x, y, hs, L.tW, 8.15, 9.7, t, { hl: c.hl.l5a });
      headline(ctx, c.l5b, x, y + h5, hs, L.tW, 8.55, 9.75, t, { color: SILVER });
      headline(ctx, c.l6, x, y, hs, L.tW, 10.15, 11.7, t, { hl: c.hl.l6 });
      const h7 = headline(ctx, c.l7a, x, y, hs, L.tW, 12.15, 13.75, t, { hl: c.hl.l7a });
      headline(ctx, c.l7b, x, y + h7, hs, L.tW, 13.0, 13.8, t, { color: SILVER });
      headline(ctx, c.l8, x, y, hs, L.tW, 14.15, 15.65, t, { hl: c.hl.l8 });
    }

    for (const ts of [3.82, 5.82, 7.82, 9.82, 11.82, 13.95, 15.85]) sweep(ctx, W, H, t, ts);

    // ── end card (bars 8–9)
    if (t >= 15.9) {
      const cx = W / 2, cy = H * (L.land ? 0.42 : 0.42);
      const wa = EASE.expo(prog(t, 15.9, 16.8));
      radialBlob(ctx, cx + 320 * Math.sin(t * 0.6), cy - 140, W * 0.5, ROSE, 0.32 * wa);
      radialBlob(ctx, cx - 360, cy + 260, W * 0.42, GOLD, 0.26 * wa);
      radialBlob(ctx, cx + 420, cy + 220, W * 0.36, LILAC, 0.18 * wa);
      if (t > 16.05 && t < 17.2) {   // a ring leaves the logo as it lands
        const u = prog(t, 16.05, 17.2);
        ctx.strokeStyle = rgba(ROSE, 0.55 * (1 - u)); ctx.lineWidth = 3;
        ctx.beginPath(); ctx.arc(cx, cy, 80 + EASE.expo(u) * 520, 0, TAU); ctx.stroke();
      }
      const ls = Math.min(1, W / 1920) * (L.land ? 1 : 1.25);
      const tile = 132 * ls, gap = 30 * ls, word = 118 * ls;
      setFont(ctx, 800, word, 'Inter', -word * 0.04);
      const ww = ctx.measureText('PharmaCam').width;
      const lx = cx - (tile + gap + ww) / 2;
      logoTile(ctx, lx + tile / 2, cy, tile, spring(t - 16.05, 11, 5));
      { // glossy shimmer across the tile
        const u = prog(t, 16.55, 17.25);
        if (u > 0 && u < 1) {
          ctx.save(); rrect(ctx, lx, cy - tile / 2, tile, tile, tile * 0.24); ctx.clip();
          const sx2 = lx - tile + u * tile * 3;
          const sg = ctx.createLinearGradient(sx2 - tile * 0.4, cy - tile / 2, sx2 + tile * 0.4, cy + tile / 2);
          sg.addColorStop(0, 'rgba(255,255,255,0)'); sg.addColorStop(0.5, 'rgba(255,240,235,0.55)'); sg.addColorStop(1, 'rgba(255,255,255,0)');
          ctx.fillStyle = sg; ctx.fillRect(lx, cy - tile / 2, tile, tile); ctx.restore();
        }
      }
      ctx.save(); ctx.beginPath(); ctx.rect(lx + tile + gap - 10, cy - word, ww + 40, word * 1.6); ctx.clip();
      const wp = EASE.expo(prog(t, 16.3, 17.0));
      ctx.fillStyle = rgba(INK); ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
      ctx.fillText('PharmaCam', lx + tile + gap, cy + 6 * ls + (1 - wp) * word * 0.9);
      ctx.restore();
      const tg = Math.round(46 * ls);
      setFont(ctx, 600, tg, 'Inter', -tg * 0.02);
      ctx.textAlign = 'center'; ctx.textBaseline = 'alphabetic';
      ctx.save(); ctx.beginPath(); ctx.rect(0, cy + 150 * ls - tg * 1.1, W, tg * 1.5); ctx.clip();
      const tp = EASE.expo(prog(t, 16.85, 17.55));
      const tw2 = ctx.measureText(c.tagline).width;
      const tgr = ctx.createLinearGradient(cx - tw2 / 2, 0, cx + tw2 / 2, 0);
      tgr.addColorStop(0, rgba(ROSE)); tgr.addColorStop(0.5, rgba(PEACH)); tgr.addColorStop(1, rgba(GOLD));
      ctx.fillStyle = tgr; ctx.fillText(c.tagline, cx, cy + 150 * ls + (1 - tp) * tg * 1.2);
      ctx.restore();
      chipLabel(ctx, cx, cy + 262 * ls, c.cta, spring(t - 17.5, 13, 6.5), { size: Math.round(36 * ls), weight: 700, fill: 'rgba(245,245,247,0.97)', ink: [10, 10, 11] });
      setFont(ctx, 600, Math.round(34 * ls), 'Inter', 0.2);
      ctx.fillStyle = rgba(INK, EASE.expo(prog(t, 17.85, 18.5)));
      ctx.textAlign = 'center'; ctx.textBaseline = 'alphabetic';
      ctx.fillText(c.phone, cx, cy + 360 * ls);
    }

    // loop: fade back to the opening black
    const fade = prog(t, 19.5, 20.0);
    if (fade > 0) { ctx.fillStyle = rgba(BG, EASE.css(fade)); ctx.fillRect(0, 0, W, H); }
  },
  post(ctx, t, { W, H }) { vignette(ctx, W, H, 0.32); },
};
