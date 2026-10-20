/* PharmaCam dashboard — vanilla JS + Chart.js.
 * Runs against the device API, or in demo mode (GitHub Pages) against static
 * JSON files whose names come from apiKey() (mirrored in scripts/build_site.py). */
"use strict";

const DEMO = window.PHARMACAM_DEMO || null;
// ?shot=1 → still frame for documentation screenshots: no fades, trails or chart animation
const SHOT = new URLSearchParams(location.search).has("shot");
{
  const th = new URLSearchParams(location.search).get("theme");
  if (th === "light" || th === "dark") document.documentElement.dataset.theme = th;
}
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const S = { info: null, charts: {}, tab: "live", liveTimer: null, demoFrame: 0, fc: null, fcSel: 0, zones: [], zSel: -1 };
const ZONE_TYPES = {  // one palette across panel, live view and film
  entrance: { name: "Giriş kapısı", color: "#ffbe40" },
  queue: { name: "Sıra alanı", color: "#ffbe40" },
  service: { name: "Tezgâh", color: "#ff9670" },
  staff: { name: "Personel", color: "#be98ff" },
  shelf: { name: "Raf", color: "#ff5c8a" },
};
const LEVEL_ICON = { good: "✓", warn: "!", bad: "✕", info: "i" };

/* ------------------------------------------------------------------ api */
function apiKey(path, params) {
  let k = path.replace(/^\/?api\//, "").replace(/[^a-zA-Z0-9]+/g, "_");
  const ps = Object.entries(params || {}).filter(([, v]) => v !== undefined && v !== null && v !== "")
    .sort(([a], [b]) => (a < b ? -1 : 1));
  for (const [a, v] of ps) k += `__${a}-${v}`;
  return k;
}
function apiUrl(path, params, ext) {
  if (DEMO) return DEMO.base + apiKey(path, params) + (ext || ".json");
  const qs = new URLSearchParams(Object.entries(params || {}).filter(([, v]) => v !== undefined && v !== null && v !== ""));
  const s = qs.toString();
  return path.replace(/^\//, "") + (s ? "?" + s : "");
}
async function api(path, params) {
  const r = await fetch(apiUrl(path, params), { cache: "no-store" });
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}
async function send(method, path, body) {
  if (DEMO) { toast("Demo modunda değişiklikler kaydedilmez"); throw new Error("demo"); }
  const r = await fetch(path.replace(/^\//, ""), {
    method, headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) { toast(data.detail || `Hata: ${r.status}`); throw new Error(data.detail); }
  return data;
}

/* -------------------------------------------------------------- helpers */
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.add("hidden"), 3200);
}
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
function fmtDur(s) {
  if (s === null || s === undefined) return "—";
  s = Number(s);
  if (s < 60) return `${Math.round(s)} sn`;
  return `${Math.floor(s / 60)} dk ${String(Math.round(s % 60)).padStart(2, "0")} sn`;
}
const pad = (n) => String(n).padStart(2, "0");
const hh = (h) => `${pad(h)}:00`;
function shiftDay(day, n) {
  const d = new Date(day + "T12:00:00Z");
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}
function trDate(day) {
  const d = new Date(day + "T12:00:00Z");
  return d.toLocaleDateString("tr-TR", { day: "numeric", month: "long", weekday: "short", timeZone: "UTC" });
}
function timeOf(ts) {
  return new Date(ts * 1000).toLocaleTimeString("tr-TR", { timeZone: S.info?.device?.timezone || "Europe/Istanbul" });
}
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
function openHours() {
  const o = S.info?.opening_hour ?? 8, c = S.info?.closing_hour ?? 22;
  // opening hours, plus any hour that actually had traffic (late duty nights)
  return (rows) => {
    const busy = rows.filter((r) => r.entries || r.expected || r.queue_max).map((r) => r.hour);
    const lo = Math.min(o, ...busy), hi = Math.max(c - 1, ...busy);
    return rows.filter((r) => r.hour >= lo && r.hour <= hi);
  };
}
function tile(label, value, sub, status) {
  const b = status ? `<span class="badge ${status}" title="${status === "good" ? "iyi" : status === "warn" ? "dikkat" : "kötü"}">${LEVEL_ICON[status]}</span>` : "";
  const long = String(value).length > 10 ? " text" : "";
  return `<div class="tile"><div class="label"><span>${esc(label)}</span>${b}</div><div class="value${long}">${esc(value)}</div>${sub ? `<div class="sub">${esc(sub)}</div>` : ""}</div>`;
}
function renderInsights(el, items) {
  el.innerHTML = items.map((i) => `<li><span class="ic ${i.level}" aria-label="${i.level}">${LEVEL_ICON[i.level] || "i"}</span><span>${esc(i.text)}</span></li>`).join("")
    || `<li class="muted">Henüz değerlendirme yok.</li>`;
}

/* --------------------------------------------------------------- charts */
function chartDefaults() {
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  Chart.defaults.font.size = 12;
  if (SHOT) Chart.defaults.animation = false;
  Chart.defaults.color = css("--muted");
  Chart.defaults.borderColor = css("--grid");
  Chart.defaults.plugins.legend.labels.boxWidth = 12;
  Chart.defaults.plugins.legend.labels.boxHeight = 12;
  Chart.defaults.plugins.tooltip.backgroundColor = css("--ink");
  Chart.defaults.plugins.tooltip.titleColor = css("--surface");
  Chart.defaults.plugins.tooltip.bodyColor = css("--surface");
  Chart.defaults.plugins.tooltip.padding = 10;
  Chart.defaults.plugins.tooltip.cornerRadius = 8;
  Chart.defaults.maintainAspectRatio = false;
}
function chart(id, cfg) {
  if (S.charts[id]) S.charts[id].destroy();
  const base = {
    options: {
      responsive: true,
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { display: (cfg.data.datasets || []).length > 1, position: "top", align: "end" } },
      scales: {
        x: { grid: { display: false }, ticks: { maxRotation: 0, autoSkipPadding: 8 } },
        y: { beginAtZero: true, border: { display: false }, ticks: { precision: 0 } },
      },
    },
  };
  const merged = { ...cfg, options: deepMerge(base.options, cfg.options || {}) };
  S.charts[id] = new Chart(document.getElementById(id), merged);
  return S.charts[id];
}
function deepMerge(a, b) {
  const out = { ...a };
  for (const [k, v] of Object.entries(b)) out[k] = v && typeof v === "object" && !Array.isArray(v) && a[k] ? deepMerge(a[k], v) : v;
  return out;
}
const barDs = (label, data, color) => ({ label, data, backgroundColor: color, borderRadius: { topLeft: 4, topRight: 4 }, borderSkipped: "bottom", maxBarThickness: 34, borderColor: css("--surface"), borderWidth: { left: 1, right: 1 } });
const lineDs = (label, data, color, extra = {}) => ({ label, data, borderColor: color, backgroundColor: color, borderWidth: 2, pointRadius: 3, pointHoverRadius: 6, tension: 0.3, ...extra });

/* ----------------------------------------------------------------- tabs */
const LOADERS = {
  live: loadLive, analysis: loadAnalysis, shelves: loadShelves, queue: loadQueue, heatmap: loadHeatmap,
  forecast: loadForecast, visits: loadVisits, reports: loadReports, zones: loadZones, settings: loadSettings,
};
function showTab(tab) {
  S.tab = tab;
  $$("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  $$(".tab").forEach((s) => s.classList.toggle("active", s.id === `tab-${tab}`));
  if (location.hash !== `#${tab}`) history.replaceState(null, "", `#${tab}`);
  clearInterval(S.liveTimer);
  if (tab !== "live") liveStop();
  LOADERS[tab]().catch((e) => { console.error(e); if (e.message !== "demo") toast("Veri alınamadı: " + e.message); });
}

/* ----------------------------------------------------------------- live */
async function loadLive() {
  const today = S.info.today;
  const [hourly, ins] = await Promise.all([api("/api/hourly", { day: today }), api("/api/insights", { day: today })]);
  const rows = openHours()(hourly);
  chart("ch-live-hourly", {
    type: "bar",
    data: { labels: rows.map((r) => hh(r.hour)), datasets: [barDs("Giriş", rows.map((r) => r.entries), css("--s1"))] },
  });
  renderInsights($("#live-insights"), ins);
  $("#live-wrap").classList.toggle("clear", !S.info.blur_people);
  $("#lv-note").textContent = S.info.blur_people ? "kişiler bulanık · görüntü kaydedilmez" : "görüntü kaydedilmez";
  liveConnect();
  liveSetMode(S.info.live_view ? LIVE.mode : "plan");
  await tickLive();
  S.liveTimer = setInterval(tickLive, 500);
  cancelAnimationFrame(LIVE.raf);
  LIVE.raf = requestAnimationFrame(liveDraw);
}

/* -------------------------------------------------------------- live view
 * The device sends the raw frame (people blurred only if configured) plus people/zone data; the panel draws everything else
 * on a canvas at display rate: tinted zones with live counts, glowing people with trails,
 * queue timers and staff chips. Positions arrive twice a second and are eased in between. */
const LIVE = { people: new Map(), za: {}, last: null, raf: 0, mode: "cam", pulse: 0, entries: null, seq: null, i: -1, t: 0, es: null, at: 0, streaming: false };
try { LIVE.mode = localStorage.getItem("pc-live-mode") || "cam"; } catch { /* private mode */ }
const ZC = { shelf: [255, 92, 138], queue: [255, 190, 64], service: [255, 150, 112], staff: [190, 152, 255], entrance: [255, 190, 64] };
const ROSE = [255, 92, 138], GOLD = [255, 190, 64], LILAC = [190, 152, 255];
const rgbaJ = (c, a) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;
const mmss = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

function liveSetMode(m) {
  LIVE.mode = m;
  $("#live-wrap").classList.toggle("plan", m === "plan");
  $$("#lv-mode button").forEach((b) => b.classList.toggle("active", b.dataset.m === m));
  try { localStorage.setItem("pc-live-mode", m); } catch { /* ignore */ }
  if (!DEMO) liveVideo(m === "cam" && S.tab === "live" && !document.hidden);
}

/* Device: one MJPEG stream for the picture and one event stream for people/counters, both at
 * camera rate, so the overlay moves with the video. Both stop when the tab is not on screen. */
const BLANK = "data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==";
function liveVideo(on) {
  const img = $("#live-img");
  on = on && S.info.live_view && (LIVE.last?.camera_ok ?? true);
  if (on === LIVE.streaming) return;
  LIVE.streaming = on;
  img.onerror = on ? () => { LIVE.streaming = false; setTimeout(() => liveVideo(true), 2000); } : null;
  img.src = on ? `api/live.mjpg?raw=1&fps=30&t=${Date.now()}` : BLANK;   // a new src aborts the old stream
}
function liveConnect() {
  if (DEMO || LIVE.es || !window.EventSource) return;
  const es = (LIVE.es = new EventSource("api/live/events"));
  es.onmessage = (e) => { LIVE.at = performance.now(); liveIngest(JSON.parse(e.data)); };
  // after a reconnect (service restart) the old MJPEG connection is dead too: reopen it
  es.onopen = () => { if (LIVE.streaming) { LIVE.streaming = false; liveVideo(LIVE.mode === "cam"); } };
}
function liveStop() {
  if (LIVE.es) { LIVE.es.close(); LIVE.es = null; }
  if (!DEMO) liveVideo(false);
}
document.addEventListener("visibilitychange", () => {
  if (S.tab !== "live") return;
  if (document.hidden) liveStop(); else { liveConnect(); liveVideo(LIVE.mode === "cam"); }
});

function liveIngest(lv) {
  const now = performance.now(), seen = new Set();
  for (const t of lv.tracks || []) {
    const foot = t.foot || [(t.box[0] + t.box[2]) / 2, t.box[3]];
    let p = LIVE.people.get(t.id);
    if (!p) { p = { id: t.id, x: foot[0], y: foot[1], trail: [], born: now, lastTrail: 0 }; LIVE.people.set(t.id, p); }
    Object.assign(p, { tx: foot[0], ty: foot[1], staff: t.staff, wait: t.wait, waitAt: now, gone: 0 });
    seen.add(t.id);
  }
  for (const p of LIVE.people.values()) if (!seen.has(p.id) && !p.gone) p.gone = now;
  if (LIVE.entries !== null && lv.entries_today > LIVE.entries) LIVE.pulse = now;
  LIVE.entries = lv.entries_today;
  LIVE.last = lv;
}

const chipFont = (k) => `600 ${Math.round(Math.max(10, Math.min(13, 12 * k)))}px -apple-system, BlinkMacSystemFont, Inter, "Segoe UI", sans-serif`;
function chipSize(g, text, dot, k) {
  g.font = chipFont(k);
  return [g.measureText(text).width + 18 + (dot ? 12 : 0), Math.max(19, 22 * k)];
}
/** Place chips greedily in priority order; each chip tries its candidate positions and is
 *  skipped when every candidate would overlap a chip already placed (or leave the frame). */
function placeChips(g, chips, k, W, H, reserved = []) {
  const placed = [...reserved];
  chips.sort((a, b) => b.prio - a.prio);
  for (const c of chips) {
    const [w, h] = chipSize(g, c.text, c.dot, k);
    for (const [x, y] of c.at) {
      const r = [x - w / 2 - 3, y - h / 2 - 3, x + w / 2 + 3, y + h / 2 + 3];
      if (r[0] < 2 || r[2] > W - 2 || r[1] < 2 || r[3] > H - 2) continue;
      if (placed.some((q) => r[0] < q[2] && r[2] > q[0] && r[1] < q[3] && r[3] > q[1])) continue;
      placed.push(r);
      g.globalAlpha = c.alpha ?? 1; chipAt(g, x, y, c.text, c.dot, k); g.globalAlpha = 1;
      break;
    }
  }
}
function chipAt(g, x, y, text, dot, k) {
  const [w, h] = chipSize(g, text, dot, k), padX = 9, dw = dot ? 12 : 0;
  g.beginPath(); g.roundRect(x - w / 2, y - h / 2, w, h, h / 2);
  g.fillStyle = "rgba(16,16,18,.78)"; g.fill();
  g.strokeStyle = "rgba(255,255,255,.16)"; g.lineWidth = 1; g.stroke();
  if (dot) { g.fillStyle = rgbaJ(dot, 1); g.beginPath(); g.arc(x - w / 2 + padX + 3.5, y, 3.5, 0, 7); g.fill(); }
  g.fillStyle = "#f5f5f7"; g.textBaseline = "middle"; g.textAlign = "left";
  g.fillText(text, x - w / 2 + padX + dw, y + 0.5);
}

function liveDraw(now) {
  const cv = $("#live-canvas");
  if (!cv || S.tab !== "live") return;
  const wrap = $("#live-wrap"), dpr = Math.min(2, devicePixelRatio || 1);
  const W = wrap.clientWidth, H = wrap.clientHeight;
  if (cv.width !== Math.round(W * dpr) || cv.height !== Math.round(H * dpr)) { cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr); }
  const g = cv.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, W, H);
  const k = W / 820, rawDt = (now - (LIVE.t || now)) / 1000, dt = Math.min(0.1, rawDt); LIVE.t = now;
  const lv = LIVE.last || {}, counts = lv.zone_counts || {};
  const chips = [];
  // keep the corners free for the HUD / legend that sit on top of the canvas
  const hudOn = W >= 560;
  const reserved = hudOn ? [[0, 0, Math.min(W, 470), 50], [Math.max(0, W - 420), H - 34, W, H]] : [[Math.max(0, W - 200), H - 30, W, H]];

  if (LIVE.mode === "plan") {          // "no image" mode: a quiet dotted floor
    g.fillStyle = "rgba(255,255,255,.07)";
    const sp = 22 * k;
    for (let y = sp / 2; y < H; y += sp) for (let x = sp / 2; x < W; x += sp) { g.beginPath(); g.arc(x, y, 1.1, 0, 7); g.fill(); }
  }

  // zones
  for (const z of S.info.zones || []) {
    const col = ZC[z.type] || [200, 200, 200];
    if (z.type === "entrance") {
      if (!z.line || z.line.length < 2) continue;
      const [[ax, ay], [bx, by]] = z.line.map(([x, y]) => [x * W, y * H]);
      const pu = Math.max(0, 1 - (now - LIVE.pulse) / 900);
      const lg = g.createLinearGradient(ax, ay, bx, by);
      lg.addColorStop(0, rgbaJ(ROSE, .9)); lg.addColorStop(1, rgbaJ(GOLD, .9));
      g.lineCap = "round";
      g.strokeStyle = rgbaJ(GOLD, .16 + .3 * pu); g.lineWidth = 14 * k + 10 * pu; g.beginPath(); g.moveTo(ax, ay); g.lineTo(bx, by); g.stroke();
      g.strokeStyle = lg; g.lineWidth = 3.2 * k; g.beginPath(); g.moveTo(ax, ay); g.lineTo(bx, by); g.stroke();
      if (pu > 0) { g.strokeStyle = rgbaJ(GOLD, .6 * pu); g.lineWidth = 2; g.beginPath(); g.arc((ax + bx) / 2, (ay + by) / 2, 10 + (1 - pu) * 60 * k, Math.PI, 2 * Math.PI); g.stroke(); }
      chips.push({ text: z.name, dot: GOLD, prio: 50, at: [[(ax + bx) / 2, ay - 18 * k], [(ax + bx) / 2, ay + 18 * k]] });
      continue;
    }
    if (!z.polygon || z.polygon.length < 3) continue;
    const pts = z.polygon.map(([x, y]) => [x * W, y * H]);
    const n = counts[z.id] || 0;
    LIVE.za[z.id] = (LIVE.za[z.id] ?? 0) + ((n > 0 ? 1 : 0) - (LIVE.za[z.id] ?? 0)) * (1 - Math.exp(-dt * 4));
    const a = LIVE.za[z.id];
    const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
    const x0 = Math.min(...xs), y0 = Math.min(...ys), x1 = Math.max(...xs), y1 = Math.max(...ys);
    g.beginPath(); pts.forEach(([x, y], i) => (i ? g.lineTo(x, y) : g.moveTo(x, y))); g.closePath();
    const fg = g.createLinearGradient(x0, y0, x1, y1);
    fg.addColorStop(0, rgbaJ(col, .12 + .2 * a)); fg.addColorStop(1, rgbaJ(col, .02 + .05 * a));
    g.fillStyle = fg; g.fill();
    g.setLineDash([5 * k, 6 * k]); g.strokeStyle = rgbaJ(col, .4 + .45 * a); g.lineWidth = 1.3 + a; g.stroke(); g.setLineDash([]);
    // label: name + live count, inside the zone (top-left, else bottom-left, else centre)
    const label = n ? `${z.name} · ${n}` : z.name;
    const [lw, lh] = chipSize(g, label, col, k);
    chips.push({ text: label, dot: col, prio: 40 + n, at: [[x0 + 8 + lw / 2, y0 + 8 + lh / 2], [x0 + 8 + lw / 2, y1 - 8 - lh / 2], [(x0 + x1) / 2, (y0 + y1) / 2]] });
  }

  // people
  for (const [id, p] of LIVE.people) {
    // after a stall (hidden tab, slow device) jump straight to the target instead of streaking across
    if (rawDt > 0.25 || SHOT) { p.x = p.tx; p.y = p.ty; p.trail = []; }
    const f = 1 - Math.exp(-dt * (DEMO ? 7 : 22));
    p.x += (p.tx - p.x) * f; p.y += (p.ty - p.y) * f;
    if (now - p.lastTrail > 50) { p.trail.push([p.x, p.y]); if (p.trail.length > 22) p.trail.shift(); p.lastTrail = now; }
    const fadeIn = SHOT ? 1 : Math.min(1, (now - p.born) / 400), fadeOut = p.gone ? 1 - (now - p.gone) / 600 : 1;
    if (fadeOut <= 0) { LIVE.people.delete(id); continue; }
    const al = fadeIn * fadeOut, X = p.x * W, Y = p.y * H;
    const c1 = p.staff ? LILAC : ROSE, c2 = p.staff ? LILAC : GOLD;
    for (let i = 1; i < p.trail.length; i++) {            // trail
      const [ax, ay] = p.trail[i - 1], [bx, by] = p.trail[i];
      g.strokeStyle = rgbaJ(c1, al * .45 * (i / p.trail.length)); g.lineWidth = 3 * k * (i / p.trail.length) + .5;
      g.beginPath(); g.moveTo(ax * W, ay * H); g.lineTo(bx * W, by * H); g.stroke();
    }
    const R = 26 * k, hg = g.createRadialGradient(X, Y, 0, X, Y, R);
    hg.addColorStop(0, rgbaJ(c1, .45 * al)); hg.addColorStop(1, rgbaJ(c2, 0));
    g.fillStyle = hg; g.fillRect(X - R, Y - R, 2 * R, 2 * R);
    if (!p.staff) {                                          // soft pulse
      const ph = ((now / 1000 + id * 0.37) % 2.4) / 2.4;
      g.strokeStyle = rgbaJ(c1, (1 - ph) * .35 * al); g.lineWidth = 1.5;
      g.beginPath(); g.arc(X, Y, 8 * k + 20 * k * ph, 0, 7); g.stroke();
    }
    const cg = g.createLinearGradient(X - 7, Y - 7, X + 7, Y + 7);
    cg.addColorStop(0, rgbaJ(c1, al)); cg.addColorStop(1, rgbaJ(c2, al));
    g.fillStyle = cg; g.beginPath(); g.arc(X, Y, 6.5 * k + 1.5, 0, 7); g.fill();
    g.strokeStyle = `rgba(12,12,14,${.9 * al})`; g.lineWidth = 2; g.stroke();
    const off = 16 * k + 4;
    if (p.staff) chips.push({ text: "Personel", dot: LILAC, prio: 20, alpha: al, at: [[X, Y - off - 6], [X, Y + off + 6]] });
    else if (p.wait != null) {
      const w = p.wait + (now - p.waitAt) / 1000, txt = `⏱ ${mmss(w)}`, [cw] = chipSize(g, txt, null, k);
      chips.push({ text: txt, dot: null, prio: 30 + w / 60, alpha: al, at: [[X + off + cw / 2, Y], [X - off - cw / 2, Y], [X, Y - off - 6]] });
    }
  }
  placeChips(g, chips, k, W, H, reserved);
  LIVE.raf = requestAnimationFrame(liveDraw);
}

async function tickLive() {
  let lv, frame;
  if (DEMO) {
    if (!LIVE.seq) LIVE.seq = await fetch(`${DEMO.base}live/seq.json`).then((r) => r.json());
    LIVE.i = (LIVE.i + 1) % LIVE.seq.length;
    lv = LIVE.seq[LIVE.i];
    frame = `${DEMO.base}live/frame_${String(LIVE.i).padStart(3, "0")}.jpg`;
  } else if (LIVE.es && LIVE.last && performance.now() - LIVE.at < 2500) {
    lv = LIVE.last;                                   // pushed by /api/live/events
  } else {
    lv = await api("/api/live");
    liveIngest(lv);
  }
  if (DEMO) liveIngest(lv);
  $("#lv-occ").textContent = lv.occupancy;
  $("#lv-queue").textContent = lv.queue_length;
  $("#lv-queue-sub").textContent = lv.queue_waits?.length ? `en uzun bekleme ${fmtDur(lv.queue_waits[0])}` : "sıra yok";
  $("#lv-staff").textContent = lv.staff_on_duty;
  $("#lv-in").textContent = lv.entries_today;
  $("#lv-out").textContent = lv.exits_today;
  $("#lv-in-sub").textContent = lv.staff_entries_today ? `+${lv.staff_entries_today} personel girişi` : "müşteri";
  const mdl = S.info.model?.title?.split(" ")[0];
  $("#lv-fps").textContent = lv.camera_ok ? `${lv.fps} kare/sn işleniyor${mdl ? ` · ${mdl}` : ""}` : "kamera beklemede";
  $("#hud-occ").textContent = lv.occupancy; $("#hud-q").textContent = lv.queue_length; $("#hud-st").textContent = lv.staff_on_duty;
  const names = lv.zone_names || {};
  const zl = (S.info.zones || []).filter((z) => z.type !== "entrance");
  $("#zone-live").innerHTML = zl.map((z) => `<li><span class="sw" style="background:${ZONE_TYPES[z.type].color}"></span>${esc(names[z.id] || z.name)}<span class="muted small">${ZONE_TYPES[z.type].name}</span><span class="n">${lv.zone_counts?.[z.id] || 0}</span></li>`).join("");

  const img = $("#live-img"), off = $("#live-off");
  if (!lv.camera_ok && !DEMO) {
    off.textContent = "Kamera bağlı değil veya başlatılıyor. Sayaçlar ve geçmiş veriler kullanılabilir.";
    off.classList.remove("hidden");
    liveVideo(false);
    return;
  }
  off.classList.add("hidden");
  if (!DEMO) { liveVideo(LIVE.mode === "cam" && !document.hidden); return; }
  if (S.info.live_view && LIVE.mode === "cam" && !img._busy) {
    img._busy = true;
    const pre = new Image();
    pre.onload = () => { img.src = pre.src; img._busy = false; };
    pre.onerror = () => { img._busy = false; };
    pre.src = frame;
  }
}

/* ------------------------------------------------------------- analysis */
function fillDaySelects() {
  const days = [...S.info.days].reverse();
  if (!days.includes(S.info.today)) days.unshift(S.info.today);
  for (const sel of $$(".day-select")) {
    const cur = sel.value;
    sel.innerHTML = days.map((d) => `<option value="${d}">${trDate(d)}${d === S.info.today ? " (bugün)" : ""}</option>`).join("");
    // default: today if it already has data, otherwise the most recent day that does
    const asked = new URLSearchParams(location.search).get("day");
    const def = days.includes(asked) ? asked
      : S.info.days.includes(S.info.today) ? S.info.today : (S.info.days[S.info.days.length - 1] || days[0]);
    sel.value = cur && days.includes(cur) ? cur : def;
  }
}
async function loadAnalysis() {
  const day = $("#an-day").value;
  const end = S.info.today;
  const [s, hourly, daily, ins] = await Promise.all([
    api("/api/summary", { day }), api("/api/hourly", { day }),
    api("/api/daily", { start: shiftDay(end, -29), end }), api("/api/insights", { day }),
  ]);
  const ch = s.entries && s.entries_same_day_last_week ? Math.round((100 * (s.entries - s.entries_same_day_last_week)) / s.entries_same_day_last_week) : null;
  const waitSt = s.avg_wait_s == null ? null : s.avg_wait_s > 180 ? "bad" : s.avg_wait_s > 120 ? "warn" : "good";
  $("#an-tiles").innerHTML = [
    tile("Giren müşteri", s.entries, ch === null ? "geçen haftayla kıyas yok" : `geçen ${s.weekday}: ${ch > 0 ? "+" : ""}${ch}%`),
    tile("En yoğun saat", s.busiest_hour == null ? "—" : hh(s.busiest_hour)),
    tile("Ort. ziyaret süresi", fmtDur(s.avg_visit_s)),
    tile("En yüksek doluluk", `${s.peak_occupancy} kişi`),
    tile("Ort. sıra bekleme", fmtDur(s.avg_wait_s), `%90: ${fmtDur(s.p90_wait_s)}`, waitSt),
    tile("Hizmet alan", s.served, s.service_rate ? `girenlerin %${Math.round(s.service_rate)}'i` : ""),
    tile("Sıradan vazgeçen", s.abandoned, "", s.abandoned ? "warn" : null),
    tile("En çok durulan raf", s.top_zone_name || "—"),
  ].join("");
  const rows = openHours()(hourly);
  chart("ch-an-entries", {
    type: "bar",
    data: { labels: rows.map((r) => hh(r.hour)), datasets: [barDs("Giriş", rows.map((r) => r.entries), css("--s1"))] },
  });
  chart("ch-an-occ", {
    type: "line",
    data: { labels: rows.map((r) => hh(r.hour)), datasets: [lineDs("Ort. içerideki", rows.map((r) => r.occupancy_avg), css("--s1"), { fill: { target: "origin", above: css("--s1-band") } })] },
    options: { scales: { y: { ticks: { precision: 1 } } } },
  });
  chart("ch-an-daily", {
    type: "bar",
    data: {
      labels: daily.map((r) => r.day.slice(8) + "." + r.day.slice(5, 7)),
      datasets: [{ ...barDs("Müşteri", daily.map((r) => r.entries), css("--s1")), backgroundColor: daily.map((r) => (r.day === day ? css("--s1") : css("--s1-band"))) }],
    },
    options: {
      onClick: (e, els) => { if (els.length) { $("#an-day").value = daily[els[0].index].day; loadAnalysis(); } },
      plugins: { tooltip: { callbacks: { title: (it) => trDate(daily[it[0].dataIndex].day) } } },
    },
  });
  renderInsights($("#an-insights"), ins);
}

/* -------------------------------------------------------------- shelves */
function rangeOf(segId) {
  const days = Number($(`#${segId} button.active`).dataset.days);
  const end = S.info.today;
  return { start: shiftDay(end, -(days - 1)), end };
}
async function loadShelves() {
  const rows = await api("/api/zones/stats", rangeOf("sh-range"));
  chart("ch-shelves", {
    type: "bar",
    data: {
      labels: rows.map((r) => r.name),
      datasets: [barDs("Durdu", rows.map((r) => r.stops), css("--s1")), barDs("Geçti", rows.map((r) => r.passes), css("--neutral"))],
    },
    options: { indexAxis: "y", scales: { x: { grid: { display: true } }, y: { grid: { display: false } } } },
  });
  const max = Math.max(1, ...rows.map((r) => r.stops));
  $("#tb-shelves").innerHTML = `<thead><tr><th>Raf</th><th class="num">Duruş</th><th class="num">Geçiş</th><th class="num">Durma oranı</th><th class="num">Ort. süre</th><th class="num">En uzun</th><th class="num">Toplam</th></tr></thead><tbody>` +
    (rows.map((r) => `<tr><td><div class="bar-cell"><i style="width:${Math.round((60 * r.stops) / max)}px"></i>${esc(r.name)}</div></td><td class="num">${r.stops}</td><td class="num">${r.passes}</td><td class="num">${r.stop_rate == null ? "—" : "%" + Math.round(r.stop_rate)}</td><td class="num">${fmtDur(r.avg_dwell_s)}</td><td class="num">${fmtDur(r.max_dwell_s)}</td><td class="num">${r.total_dwell_min} dk</td></tr>`).join("")
      || `<tr><td colspan="7" class="muted">Bu aralıkta veri yok. Raf bölgelerini "Bölgeler" sekmesinden tanımlayın.</td></tr>`) + "</tbody>";
}

/* ---------------------------------------------------------------- queue */
async function loadQueue() {
  const day = $("#q-day").value;
  const qa = await api("/api/queue", { day });
  const st = qa.avg_wait_s == null ? null : qa.avg_wait_s > 180 ? "bad" : qa.avg_wait_s > 120 ? "warn" : "good";
  $("#q-tiles").innerHTML = [
    tile("Hizmet alan", qa.served),
    tile("Ortalama bekleme", fmtDur(qa.avg_wait_s), "", st),
    tile("Medyan bekleme", fmtDur(qa.median_wait_s)),
    tile("%90 bekleme", fmtDur(qa.p90_wait_s)),
    tile("En uzun bekleme", fmtDur(qa.max_wait_s)),
    tile("Ort. hizmet süresi", fmtDur(qa.avg_service_s)),
    tile("Vazgeçen", qa.abandoned, "hizmet almadan ayrılan", qa.abandoned ? "warn" : null),
  ].join("");
  const rows = openHours()(qa.by_hour);
  const waits = rows.map((r) => (r.avg_wait_s == null ? 0 : +(r.avg_wait_s / 60).toFixed(2)));
  chart("ch-q-hour", {
    type: "bar",
    data: { labels: rows.map((r) => hh(r.hour)), datasets: [{ ...barDs("Ort. bekleme (dk)", waits, css("--s1")), backgroundColor: waits.map((w) => (w > 3 ? css("--bad") : css("--s1"))) }] },
    options: { scales: { y: { ticks: { precision: 1 } } }, plugins: { tooltip: { callbacks: { label: (c) => ` ${fmtDur(c.raw * 60)}${c.raw > 3 ? " — hedef 3 dk aşıldı" : ""}` } } } },
  });
  chart("ch-q-hist", {
    type: "bar",
    data: { labels: qa.histogram.map((b) => b.label), datasets: [barDs("Kişi", qa.histogram.map((b) => b.count), css("--s1"))] },
  });
  chart("ch-q-max", {
    type: "bar",
    data: { labels: rows.map((r) => hh(r.hour)), datasets: [barDs("En uzun sıra", rows.map((r) => r.queue_max), css("--s1"))] },
  });
}

/* -------------------------------------------------------------- heatmap */
async function loadHeatmap() {
  const r = rangeOf("hm-range");
  $("#hm-img").src = DEMO ? apiUrl("/api/heatmap", r, ".png") : apiUrl("/api/heatmap", { ...r, t: Date.now() });
}

/* ------------------------------------------------------------- forecast */
async function loadForecast() {
  S.fc = await api("/api/forecast", { days: 7 });
  const days = S.fc.days;
  if (!days.length) {
    $("#fc-days").innerHTML = `<p class="muted">Tahmin için en az birkaç günlük veri gerekiyor.</p>`;
    return;
  }
  $("#fc-days").innerHTML = days.map((d, i) => `<button class="fc-day${i === S.fcSel ? " active" : ""}" data-i="${i}"><div class="d">${esc(d.weekday)}</div><div class="r">${trDate(d.day)}</div><div class="t">${d.expected_total ? "≈ " + d.expected_total : "Kapalı"}</div><div class="r">${d.expected_total ? `${d.low_total}–${d.high_total} · zirve ${hh(d.peak_hour)}` : "geçmişte açık değil"}</div></button>`).join("");
  $$(".fc-day").forEach((b) => b.addEventListener("click", () => { S.fcSel = +b.dataset.i; loadForecast(); }));
  const d = days[Math.min(S.fcSel, days.length - 1)];
  const rows = openHours()(d.hours);
  $("#fc-title").textContent = `${d.weekday} — saatlik müşteri tahmini`;
  $("#fc-meta").textContent = `${S.fc.history_days} günlük geçmiş · eğilim x${S.fc.trend}`;
  chart("ch-fc", {
    type: "line",
    data: {
      labels: rows.map((r) => hh(r.hour)),
      datasets: [
        // higher `order` is drawn first: the band sits under the expected line
        lineDs("Alt sınır", rows.map((r) => r.low), "transparent", { pointRadius: 0, pointHoverRadius: 0, borderWidth: 0, order: 2 }),
        lineDs("Olası aralık", rows.map((r) => r.high), "transparent", { pointRadius: 0, pointHoverRadius: 0, borderWidth: 0, fill: "-1", backgroundColor: css("--s1-band"), order: 2 }),
        lineDs("Beklenen", rows.map((r) => r.expected), css("--s1"), { order: 0 }),
      ],
    },
    options: {
      plugins: {
        legend: { labels: { filter: (it) => it.text !== "Alt sınır" } },
        tooltip: { filter: (it) => it.dataset.label === "Beklenen", callbacks: { afterLabel: (c) => `aralık: ${rows[c.dataIndex].low}–${rows[c.dataIndex].high}` } },
      },
    },
  });
  $("#fc-staff").innerHTML = rows.map((r) => `<div><span>${hh(r.hour)}</span><b>${r.staff_needed}</b><span>kişi</span></div>`).join("");
}

/* --------------------------------------------------------------- visits */
async function loadVisits() {
  const day = $("#v-day").value;
  const inc = $("#v-staff").checked;
  const rows = await api("/api/visits", { day, include_staff: inc ? "true" : "false", limit: 300 });
  $("#v-csv").href = DEMO ? "#" : apiUrl("/api/export/visits.csv", { start: day, end: day });
  const names = Object.fromEntries((S.info.zones || []).map((z) => [z.id, z.name]));
  const yes = (v) => (v ? "✓" : "—");
  $("#tb-visits").innerHTML = `<thead><tr><th>Takip no</th><th>Başlangıç</th><th class="num">Süre</th><th>Tür</th><th>Giriş</th><th>Çıkış</th><th class="num">Sıra bekleme</th><th class="num">Hizmet</th><th>Bölgeler</th></tr></thead><tbody>` +
    (rows.map((v) => `<tr><td class="muted">${esc(v.uid.split("-")[1])}</td><td>${timeOf(v.started_at)}</td><td class="num">${fmtDur(v.duration_s)}</td><td>${v.is_staff ? '<span class="pill staff">Personel</span>' : '<span class="pill">Müşteri</span>'}</td><td>${yes(v.entered)}</td><td>${yes(v.exited)}</td><td class="num">${v.abandoned ? `${fmtDur(v.queue_wait_s)} (vazgeçti)` : fmtDur(v.queue_wait_s)}</td><td class="num">${fmtDur(v.service_s)}</td><td>${Object.entries(v.zones).map(([k, s]) => `${esc(names[k] || k)} ${Math.round(s)}sn`).join(", ")}</td></tr>`).join("")
      || `<tr><td colspan="9" class="muted">Kayıt yok.</td></tr>`) + "</tbody>";
}

/* -------------------------------------------------------------- reports */
async function loadReports() {
  const rows = await api("/api/reports");
  $("#rep-list").innerHTML = rows.map((r) => {
    const href = DEMO ? `${DEMO.base}reports/PharmaCam_${r.day}.pdf` : `api/reports/${r.day}.pdf`;
    return `<li><span><b>${trDate(r.day)}</b> <span class="muted small">· ${r.day}</span></span><a class="btn ghost" href="${href}" target="_blank" rel="noopener">PDF aç</a></li>`;
  }).join("") || `<li class="muted">Henüz rapor yok.</li>`;
}

/* ---------------------------------------------------------------- zones */
async function loadZones() {
  S.zones = await api("/api/config/zones");
  const img = new Image();
  img.onload = () => { S.zImg = img; drawZones(); };
  img.onerror = () => { S.zImg = null; drawZones(); };
  img.src = DEMO ? `${DEMO.base}snapshot.jpg` : `api/snapshot.jpg?t=${Date.now()}`;
  renderZoneList();
}
function renderZoneList() {
  $("#ze-list").innerHTML = S.zones.map((z, i) => `<li data-i="${i}" class="${i === S.zSel ? "active" : ""}"><span class="sw" style="background:${ZONE_TYPES[z.type].color}"></span>${esc(z.name)}<span class="ty">${ZONE_TYPES[z.type].name}</span></li>`).join("");
  $$("#ze-list li").forEach((li) => li.addEventListener("click", () => selectZone(+li.dataset.i)));
  const z = S.zones[S.zSel];
  $("#ze-form").classList.toggle("hidden", !z);
  if (z) {
    $("#ze-name").value = z.name;
    $("#ze-id").value = z.id;
    $("#ze-help").textContent = z.type === "entrance"
      ? "Görüntüye iki kez tıklayarak kapı çizgisini, üçüncü tıkla mağazanın İÇ tarafını işaretleyin."
      : "Görüntüye tıklayarak köşe ekleyin, noktaları sürükleyin, sağ tıkla nokta silin. Kişinin AYAK noktası bölge içindeyse sayılır.";
  }
  $("#ze-hint").textContent = z ? `Düzenleniyor: ${z.name}` : "Bir bölge seçin veya yeni ekleyin";
}
function selectZone(i) { S.zSel = i; renderZoneList(); drawZones(); }
function zonePoints(z) { return z.type === "entrance" ? [...(z.line || []), ...(z.inside_point ? [z.inside_point] : [])] : z.polygon || []; }
function drawZones() {
  const cv = $("#ze-canvas");
  const w = S.zImg ? S.zImg.naturalWidth : 960, h = S.zImg ? S.zImg.naturalHeight : 720;
  cv.width = w; cv.height = h;
  const g = cv.getContext("2d");
  g.fillStyle = "#1b2322"; g.fillRect(0, 0, w, h);
  if (S.zImg) g.drawImage(S.zImg, 0, 0, w, h);
  else { g.fillStyle = "#9aa"; g.font = "20px sans-serif"; g.fillText("Kamera görüntüsü yok — bölgeler yine de çizilebilir", 30, 40); }
  const lw = Math.max(2, w / 400);
  S.zones.forEach((z, i) => {
    const col = ZONE_TYPES[z.type].color, sel = i === S.zSel;
    g.lineWidth = sel ? lw * 1.8 : lw;
    g.strokeStyle = col;
    if (z.type === "entrance") {
      const [a, b] = z.line || [];
      if (a && b) { g.beginPath(); g.moveTo(a[0] * w, a[1] * h); g.lineTo(b[0] * w, b[1] * h); g.stroke(); }
      if (z.inside_point) {
        g.fillStyle = col; g.font = `${Math.round(w / 55)}px sans-serif`;
        g.fillText("İÇERİ", z.inside_point[0] * w + 10, z.inside_point[1] * h + 5);
      }
    } else if ((z.polygon || []).length) {
      g.beginPath();
      z.polygon.forEach(([x, y], k) => (k ? g.lineTo(x * w, y * h) : g.moveTo(x * w, y * h)));
      g.closePath();
      g.globalAlpha = sel ? 0.3 : 0.16; g.fillStyle = col; g.fill(); g.globalAlpha = 1; g.stroke();
      const [x0, y0] = z.polygon[0];
      g.font = `600 ${Math.round(w / 60)}px sans-serif`; g.fillStyle = "#fff";
      g.fillText(z.name, x0 * w + 6, y0 * h + w / 50);
    }
    if (sel) zonePoints(z).forEach(([x, y]) => { g.beginPath(); g.arc(x * w, y * h, lw * 3.5, 0, 7); g.fillStyle = "#fff"; g.fill(); g.lineWidth = lw; g.stroke(); });
  });
}
function canvasPt(e) {
  const cv = $("#ze-canvas"), r = cv.getBoundingClientRect();
  return [Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), Math.min(1, Math.max(0, (e.clientY - r.top) / r.height))];
}
function hitPoint(z, p) {
  const cv = $("#ze-canvas"), r = cv.getBoundingClientRect();
  const pts = zonePoints(z);
  for (let k = 0; k < pts.length; k++) {
    const dx = (pts[k][0] - p[0]) * r.width, dy = (pts[k][1] - p[1]) * r.height;
    if (dx * dx + dy * dy < 14 * 14) return k;
  }
  return -1;
}
function setPoint(z, k, p) {
  const r = (v) => Math.round(v * 1000) / 1000;
  const q = [r(p[0]), r(p[1])];
  if (z.type === "entrance") { if (k < 2) z.line[k] = q; else z.inside_point = q; }
  else z.polygon[k] = q;
}
function bindZoneEditor() {
  const cv = $("#ze-canvas");
  cv.addEventListener("pointerdown", (e) => {
    const z = S.zones[S.zSel];
    if (!z || e.button === 2) return;
    const p = canvasPt(e), k = hitPoint(z, p);
    if (k >= 0) { S.drag = k; cv.setPointerCapture(e.pointerId); return; }
    if (z.type === "entrance") {
      z.line = z.line || [];
      if (z.line.length < 2) z.line.push(p.map((v) => Math.round(v * 1000) / 1000));
      else z.inside_point = p.map((v) => Math.round(v * 1000) / 1000);
    } else {
      z.polygon = z.polygon || [];
      z.polygon.push(p.map((v) => Math.round(v * 1000) / 1000));
    }
    drawZones();
  });
  cv.addEventListener("pointermove", (e) => { if (S.drag == null) return; setPoint(S.zones[S.zSel], S.drag, canvasPt(e)); drawZones(); });
  cv.addEventListener("pointerup", () => { S.drag = null; });
  cv.addEventListener("contextmenu", (e) => {
    e.preventDefault();
    const z = S.zones[S.zSel];
    if (!z || z.type === "entrance") return;
    const k = hitPoint(z, canvasPt(e));
    if (k >= 0) { z.polygon.splice(k, 1); drawZones(); }
  });
  $("#ze-new").addEventListener("click", () => {
    const type = $("#ze-type").value;
    let n = 1;
    const prefix = { shelf: "raf", queue: "sira", service: "tezgah", staff: "personel", entrance: "kapi" }[type];
    while (S.zones.some((z) => z.id === `${prefix}${n}`)) n++;
    const z = { id: `${prefix}${n}`, type, name: `${ZONE_TYPES[type].name} ${n}` };
    if (type === "entrance") { z.line = []; z.inside_point = null; } else z.polygon = [];
    S.zones.push(z);
    selectZone(S.zones.length - 1);
  });
  $("#ze-name").addEventListener("input", (e) => { S.zones[S.zSel].name = e.target.value; renderZoneListLite(); drawZones(); });
  $("#ze-id").addEventListener("change", (e) => { S.zones[S.zSel].id = e.target.value.trim().replace(/[^a-zA-Z0-9_-]/g, "_"); renderZoneList(); });
  $("#ze-clear").addEventListener("click", () => { const z = S.zones[S.zSel]; if (z.type === "entrance") { z.line = []; z.inside_point = null; } else z.polygon = []; drawZones(); });
  $("#ze-del").addEventListener("click", () => { S.zones.splice(S.zSel, 1); S.zSel = -1; renderZoneList(); drawZones(); });
  $("#ze-save").addEventListener("click", async () => {
    S.zones = await send("PUT", "/api/config/zones", S.zones);
    S.info = await api("/api/info");
    toast("Bölgeler kaydedildi ve uygulandı");
    renderZoneList(); drawZones();
  });
}
function renderZoneListLite() {
  const li = $(`#ze-list li[data-i="${S.zSel}"]`);
  if (li) li.childNodes[1].textContent = S.zones[S.zSel].name;
}

/* ------------------------------------------------------------- settings */
const SETTING_LABELS = {
  "device.pharmacy_name": "Eczane adı",
  "staff.enabled": "Beyaz önlükle personel ayrımı",
  "staff.white_ratio_threshold": "Önlük eşiği (0–1, düşük = daha kolay personel)",
  "staff.staff_zone_ratio": "Tezgâh arkası oranı (0–1)",
  "analytics.occupancy_mode": "Doluluk hesabı",
  "analytics.min_zone_dwell_seconds": "Rafta durma için en az süre (sn)",
  "analytics.min_queue_seconds": "Sıraya girmiş sayılma süresi (sn)",
  "server.live_view": "Canlı görüntü",
  "server.blur_people": "Canlı görüntüde kişileri bulanıklaştır",
  "reports.opening_hour": "Açılış saati",
  "reports.closing_hour": "Kapanış saati (rapor bu saatte üretilir)",
};
async function loadSettings() {
  const [st, health] = await Promise.all([api("/api/config/settings"), api("/api/health")]);
  const f = $("#set-form");
  f.innerHTML = "";
  for (const [sec, vals] of Object.entries(st)) {
    for (const [k, v] of Object.entries(vals)) {
      const key = `${sec}.${k}`, label = SETTING_LABELS[key] || key;
      let html;
      if (typeof v === "boolean") html = `<label class="inline"><input type="checkbox" data-k="${key}" ${v ? "checked" : ""}> ${esc(label)}</label>`;
      else if (key === "analytics.occupancy_mode") html = `<label>${esc(label)}<select data-k="${key}">${["hybrid", "visible", "counter"].map((o) => `<option value="${o}" ${o === v ? "selected" : ""}>${{ hybrid: "Hibrit (önerilen)", visible: "Yalnızca görünen", counter: "Kapı sayacı" }[o]}</option>`).join("")}</select></label>`;
      else html = `<label>${esc(label)}<input data-k="${key}" type="${typeof v === "number" ? "number" : "text"}" step="any" value="${esc(v)}"></label>`;
      f.insertAdjacentHTML("beforeend", html);
    }
  }
  const sys = health.system || {};
  const rows = [
    ["Cihaz", health.device_id], ["Eczane", health.pharmacy], ["Kamera", health.camera_ok ? `çalışıyor (${health.fps} fps)` : `bağlı değil${health.camera_error ? " — " + health.camera_error : ""}`],
    ["Kaynak", health.camera_source], ["Çalışma süresi", fmtDur(health.uptime_s)], ["İşlenen kare", health.frames?.toLocaleString("tr-TR")],
    ["CPU sıcaklığı", sys.cpu_temp_c != null ? `${sys.cpu_temp_c} °C` : "—"], ["Yük", sys.load ?? "—"], ["Boş disk", sys.disk_free_gb != null ? `${sys.disk_free_gb} GB` : "—"],
    ["Bulut eşitleme", health.sync?.enabled ? (health.sync.last_ok ? `son: ${timeOf(health.sync.last_ok)}` : "bekliyor") + (health.sync.error ? ` — ${health.sync.error}` : "") : "kapalı"],
  ];
  $("#health").innerHTML = rows.map(([a, b]) => `<dt>${esc(a)}</dt><dd>${esc(b)}</dd>`).join("");
}
async function saveSettings() {
  const body = {};
  for (const el of $$("#set-form [data-k]")) {
    const [sec, k] = el.dataset.k.split(".");
    body[sec] = body[sec] || {};
    body[sec][k] = el.type === "checkbox" ? el.checked : el.type === "number" ? Number(el.value) : el.value;
  }
  await send("PATCH", "/api/config/settings", body);
  S.info = await api("/api/info");
  $("#pharmacy-name").textContent = S.info.device.pharmacy_name;
  toast("Ayarlar kaydedildi");
}

/* --------------------------------------------------------------- status */
async function tickHealth() {
  try {
    const h = DEMO ? { camera_ok: true, fps: 14.8 } : await api("/api/health");
    const pill = $("#status-pill");
    pill.classList.toggle("ok", !!h.camera_ok);
    pill.classList.toggle("bad", !h.camera_ok);
    $("#status-text").textContent = DEMO ? "Demo verisi" : h.camera_ok ? `Kamera aktif · ${h.fps} fps` : "Kamera bağlı değil";
    pill.title = h.camera_error || "";
  } catch {
    $("#status-pill").className = "status bad";
    $("#status-text").textContent = "Cihaza ulaşılamıyor";
  }
}

/* ----------------------------------------------------------------- boot */
async function boot() {
  chartDefaults();
  if (DEMO && !SHOT) {
    document.body.insertAdjacentHTML("afterbegin", `<div class="demo-banner">Bu bir <b>demo</b>: veriler simülasyondur. ${DEMO.home ? `<a href="${DEMO.home}">← Ürün sayfasına dön</a>` : ""}</div>`);
  }
  S.info = await api("/api/info");
  $("#pharmacy-name").textContent = S.info.device.pharmacy_name;
  fillDaySelects();
  $$("#tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
  for (const id of ["an-day", "q-day", "v-day"]) $(`#${id}`).addEventListener("change", () => showTab(S.tab));
  $("#v-staff").addEventListener("change", loadVisits);
  const askedDays = new URLSearchParams(location.search).get("days");
  for (const seg of ["sh-range", "hm-range"]) {
    if (askedDays) $$(`#${seg} button`).forEach((x) => x.classList.toggle("active", x.dataset.days === askedDays));
    $$(`#${seg} button`).forEach((b) => b.addEventListener("click", () => {
      $$(`#${seg} button`).forEach((x) => x.classList.toggle("active", x === b));
      showTab(S.tab);
    }));
  }
  $("#rep-make").addEventListener("click", async () => {
    await send("POST", `/api/reports/${S.info.today}`);
    toast("Rapor oluşturuldu");
    loadReports();
  });
  $("#set-save").addEventListener("click", (e) => { e.preventDefault(); saveSettings(); });
  $$("#lv-mode button").forEach((b) => b.addEventListener("click", () => {
    if (b.dataset.m === "cam" && !S.info.live_view) { toast("Canlı görüntü ayarlardan kapalı"); return; }
    liveSetMode(b.dataset.m);
  }));
  bindZoneEditor();
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { chartDefaults(); showTab(S.tab); });
  tickHealth();
  setInterval(tickHealth, 5000);
  const tab = location.hash.slice(1);
  showTab(LOADERS[tab] ? tab : "live");
  window.addEventListener("hashchange", () => {
    const t = location.hash.slice(1);
    if (LOADERS[t] && t !== S.tab) showTab(t);
  });
}
boot().catch((e) => {
  console.error(e);
  $("#status-text").textContent = "Bağlantı hatası";
  toast("Panel yüklenemedi: " + e.message);
});
