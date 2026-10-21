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
  ignore: { name: "Yok say", color: "#8e8e93" },  // mirrors, posters, a TV: nothing there is counted
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
  s = Math.round(Number(s));  // round first: 119.6 s is "2 dk 00 sn", not "1 dk 60 sn"
  if (s < 60) return `${s} sn`;
  return `${Math.floor(s / 60)} dk ${String(s % 60).padStart(2, "0")} sn`;
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
  live: loadLive, analysis: loadAnalysis, profile: loadProfile, shelves: loadShelves, queue: loadQueue, heatmap: loadHeatmap,
  forecast: loadForecast, visits: loadVisits, reports: loadReports, zones: loadZones, setup: loadSetup, settings: loadSettings,
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
 * on a canvas at display rate: tinted zones with live counts, people in thin boxes with labels,
 * queue timers and staff chips. Positions arrive twice a second and are eased in between. */
const LIVE = { people: new Map(), za: {}, last: null, raf: 0, mode: "cam", pulse: 0, entries: null, seq: null, i: -1, t: 0, es: null, at: 0, streaming: false };
try { LIVE.mode = localStorage.getItem("pc-live-mode") || "cam"; } catch { /* private mode */ }
const ZC = { shelf: [255, 92, 138], queue: [255, 190, 64], service: [255, 150, 112], staff: [190, 152, 255], entrance: [255, 190, 64], ignore: [142, 142, 147] };
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
  // ?shot=1 (documentation screenshots) takes one still frame: a stream never finishes loading
  img.src = on ? (SHOT ? `api/live.jpg?raw=1&t=${Date.now()}` : `api/live.mjpg?raw=1&fps=30&t=${Date.now()}`) : BLANK;
}
function liveConnect() {
  if (DEMO || SHOT || LIVE.es || !window.EventSource) return;  // a still screenshot polls once instead
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
    const b = t.box || [t.foot[0] - 0.03, t.foot[1] - 0.2, t.foot[0] + 0.03, t.foot[1]];
    let p = LIVE.people.get(t.id);
    if (!p) { p = { id: t.id, b: [...b], born: now }; LIVE.people.set(t.id, p); }
    Object.assign(p, { tb: b, staff: t.staff, wait: t.wait, n: t.n, inS: t.in_s ?? t.seen_s, g: t.g, age: t.age, at: now, gone: 0 });
    seen.add(t.id);
  }
  for (const p of LIVE.people.values()) if (!seen.has(p.id) && !p.gone) p.gone = now;
  if (LIVE.entries !== null && lv.entries_today > LIVE.entries) LIVE.pulse = now;
  LIVE.entries = lv.entries_today;
  LIVE.last = lv;
}

/* Glass layer: person boxes and every tag are DOM elements over the picture, so the browser can
 * give them real frosted glass (backdrop-filter); the canvas below draws zones, the door line
 * and — in "no picture" mode — people as glowing dots. */
const GL = { els: new Map(), widths: new Map(), mctx: null, ts: 0 };
const SYS = '-apple-system, BlinkMacSystemFont, "SF Pro Text", Inter, "Segoe UI", sans-serif';
const CLOCK = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="8" cy="8" r="6.2"/><path d="M8 4.6V8l2.3 1.5"/></svg>';
const ARROW_IN = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M3 8h9M8.5 4.5 12 8l-3.5 3.5"/></svg>';
function glEl(key, cls) {
  let el = GL.els.get(key);
  if (!el) {
    el = document.createElement("div");
    el.className = el._cls = cls;
    el.style.opacity = el._o = "0";
    $("#live-layer").appendChild(el);
    GL.els.set(key, el);
  }
  return el;
}
function glSet(el, cls, html) {
  if (el._cls !== cls) { el.className = el._cls = cls; }
  if (html != null && el._html !== html) { el.innerHTML = el._html = html; }
}
function glPlace(el, x, y, op) {
  const t = `translate3d(${x.toFixed(1)}px,${y.toFixed(1)}px,0)`, o = op.toFixed(2);
  if (el._t !== t) { el.style.transform = el._t = t; }
  if (el._o !== o) { el.style.opacity = el._o = o; }
}
function textW(text, font) {
  const key = `${font}|${text}`;
  let w = GL.widths.get(key);
  if (w == null) {
    GL.mctx ||= document.createElement("canvas").getContext("2d");
    GL.mctx.font = font;
    w = GL.mctx.measureText(text).width;
    if (GL.widths.size > 3000) GL.widths.clear();
    GL.widths.set(key, w);
  }
  return w;
}
/** Greedy, collision-free tag placement in priority order. A tag keeps its previous spot while
 *  that still fits (no jumping between candidates); tags that fit nowhere fade out. */
function layoutTags(items, W, H, reserved) {
  const placed = reserved.map((r) => r.slice());
  items.sort((a, b) => b.prio - a.prio);
  for (const it of items) {
    const el = glEl(it.key, it.cls);
    glSet(el, it.cls, it.html);
    const order = el._ci != null && el._ci < it.at.length ? [el._ci, ...it.at.keys()] : [...it.at.keys()];
    let pos = null;
    for (const ci of order) {
      const [x, y] = it.at[ci];
      const r = [x - 3, y - 3, x + it.w + 3, y + it.h + 3];
      if (r[0] < 2 || r[2] > W - 2 || r[1] < 2 || r[3] > H - 2) continue;
      if (placed.some((q) => r[0] < q[2] && r[2] > q[0] && r[1] < q[3] && r[3] > q[1])) continue;
      placed.push(r);
      pos = [x, y];
      el._ci = ci;
      break;
    }
    if (pos) glPlace(el, pos[0], pos[1], it.op);
    else if (el._o !== "0.00") { el.style.opacity = el._o = "0.00"; }  // no room: fade where it is
  }
}

function liveDraw(now) {
  const cv = $("#live-canvas");
  if (!cv || S.tab !== "live") return;
  const wrap = $("#live-wrap"), dpr = Math.min(2, devicePixelRatio || 1);
  const img = $("#live-img");
  if (img.naturalWidth > 1 && img.naturalHeight > 1) {  // follow the stream: 4:3 camera, 16:9 recording...
    const ar = `${img.naturalWidth} / ${img.naturalHeight}`;
    if (wrap.dataset.ar !== ar) { wrap.dataset.ar = ar; wrap.style.aspectRatio = ar; }
  }
  const W = wrap.clientWidth, H = wrap.clientHeight;
  if (cv.width !== Math.round(W * dpr) || cv.height !== Math.round(H * dpr)) { cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr); }
  const g = cv.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, W, H);
  const k = W / 820, rawDt = (now - (LIVE.t || now)) / 1000, dt = Math.min(0.1, rawDt); LIVE.t = now;
  const lv = LIVE.last || {}, counts = lv.zone_counts || {};
  const cam = LIVE.mode !== "plan";
  const ts = W < 560 ? 0.86 : 1;  // tag scale (matches the CSS container query)
  if (GL.ts !== ts) { GL.ts = ts; GL.widths.clear(); }
  const small = W < 560;
  // corners kept free for the HUD bar and the legend
  const reserved = small ? [[Math.max(0, W - 200), H - 40, W, H]] : [[0, 0, Math.min(W, 380), 54], [Math.max(0, W - 360), H - 46, W, H]];
  const items = [], keep = new Set();
  const tagH = 24 * ts, ztagH = 22 * ts;

  if (!cam) {          // "no picture" mode: a quiet dotted floor
    g.fillStyle = "rgba(255,255,255,.07)";
    const sp = 22 * k;
    for (let y = sp / 2; y < H; y += sp) for (let x = sp / 2; x < W; x += sp) { g.beginPath(); g.arc(x, y, 1.1, 0, 7); g.fill(); }
  }

  // zones: a whisper of colour and a hairline edge; a zone with people in it lights up
  g.lineJoin = "round";
  for (const z of S.info.zones || []) {
    if (z.type === "ignore") continue;
    const col = ZC[z.type] || [200, 200, 200], hex = ZONE_TYPES[z.type]?.color || "#c8c8c8";
    if (z.type === "entrance") {
      if (!z.line || z.line.length < 2) continue;
      const [[ax, ay], [bx, by]] = z.line.map(([x, y]) => [x * W, y * H]);
      const pu = Math.max(0, 1 - (now - LIVE.pulse) / 900);
      const lg = g.createLinearGradient(ax, ay, bx, by);
      lg.addColorStop(0, rgbaJ(ROSE, .95)); lg.addColorStop(1, rgbaJ(GOLD, .95));
      g.lineCap = "round";
      g.strokeStyle = rgbaJ(GOLD, .12 + .3 * pu); g.lineWidth = 10 * k + 10 * pu; g.beginPath(); g.moveTo(ax, ay); g.lineTo(bx, by); g.stroke();
      g.strokeStyle = lg; g.lineWidth = Math.max(1.6, 2.4 * k); g.beginPath(); g.moveTo(ax, ay); g.lineTo(bx, by); g.stroke();
      // which way is in: two soft chevrons drifting from the line towards the inside
      const mx = (ax + bx) / 2, my = (ay + by) / 2, len = Math.hypot(bx - ax, by - ay) || 1;
      let nx = -(by - ay) / len, ny = (bx - ax) / len;
      if (z.inside_point && (z.inside_point[0] * W - mx) * nx + (z.inside_point[1] * H - my) * ny < 0) { nx = -nx; ny = -ny; }
      const tx = (bx - ax) / len, ty = (by - ay) / len, cs = 6 * Math.max(1, k);
      for (let i = 0; i < 2; i++) {
        const ph = ((now / 1400 + i * 0.5) % 1), d = 10 + ph * 26 * Math.max(1, k), a2 = Math.sin(ph * Math.PI) * .85;
        const cx = mx + nx * d, cy = my + ny * d;
        g.strokeStyle = `rgba(255,255,255,${a2})`; g.lineWidth = 1.8;
        g.beginPath(); g.moveTo(cx - nx * cs + tx * cs, cy - ny * cs + ty * cs); g.lineTo(cx, cy); g.lineTo(cx - nx * cs - tx * cs, cy - ny * cs - ty * cs); g.stroke();
      }
      const name = esc(z.name), w = (9 + 11 + 6 + 9 + 1) * ts + textW(z.name, `500 ${11 * ts}px ${SYS}`);
      const off = 20 * Math.max(1, k);
      items.push({ key: `z:${z.id}`, cls: "ztag door", html: `${ARROW_IN}<span>${name}</span>`, w, h: ztagH, prio: 45, op: 1,
                   at: [[mx + nx * off - w / 2, my + ny * off - ztagH / 2], [mx - nx * off - w / 2, my - ny * off - ztagH / 2]] });
      keep.add(`z:${z.id}`);
      continue;
    }
    if (!z.polygon || z.polygon.length < 3) continue;
    const pts = z.polygon.map(([x, y]) => [x * W, y * H]);
    const n = counts[z.id] || 0;
    LIVE.za[z.id] = (LIVE.za[z.id] ?? 0) + ((n > 0 ? 1 : 0) - (LIVE.za[z.id] ?? 0)) * (1 - Math.exp(-dt * 4));
    const a = LIVE.za[z.id];
    const xs = pts.map((q) => q[0]), ys = pts.map((q) => q[1]);
    const x0 = Math.min(...xs), y0 = Math.min(...ys), x1 = Math.max(...xs), y1 = Math.max(...ys);
    g.beginPath(); pts.forEach(([x, y], i) => (i ? g.lineTo(x, y) : g.moveTo(x, y))); g.closePath();
    const fg = g.createLinearGradient(x0, y0, x1, y1);
    fg.addColorStop(0, rgbaJ(col, .05 + .13 * a)); fg.addColorStop(1, rgbaJ(col, .01 + .05 * a));
    g.fillStyle = fg; g.fill();
    g.strokeStyle = rgbaJ(col, .2 + .45 * a); g.lineWidth = 1; g.stroke();
    if (a > 0.05) { g.strokeStyle = rgbaJ(col, .18 * a); g.lineWidth = 5; g.stroke(); }  // soft glow when occupied
    const badge = n ? `<b style="background:${hex}">${n}</b>` : "";
    const w = (9 + 7 + 6 + 9 + 1) * ts + textW(z.name, `500 ${11 * ts}px ${SYS}`) +
              (n ? 6 * ts + Math.max(16 * ts, textW(String(n), `700 ${10.5 * ts}px ${SYS}`) + 8 * ts) : 0);
    items.push({ key: `z:${z.id}`, cls: `ztag${n ? " on" : ""}`, html: `<i style="background:${hex}"></i>${esc(z.name)}${badge}`,
                 w, h: ztagH, prio: 40 + n, op: n ? 1 : 0.78,
                 at: [[x0 + 8, y0 + 8], [x0 + 8, y1 - 8 - ztagH], [(x0 + x1) / 2 - w / 2, (y0 + y1) / 2 - ztagH / 2]] });
    keep.add(`z:${z.id}`);
  }

  // people
  for (const [id, p] of LIVE.people) {
    if (rawDt > 0.25 || SHOT) { p.b = [...p.tb]; p.trail = []; }  // after a stall jump, don't sweep across
    const f = 1 - Math.exp(-dt * (DEMO ? 7 : 18));
    for (let i = 0; i < 4; i++) p.b[i] += (p.tb[i] - p.b[i]) * f;
    const fadeIn = SHOT ? 1 : Math.min(1, (now - p.born) / 300), fadeOut = p.gone ? 1 - (now - p.gone) / 500 : 1;
    if (fadeOut <= 0) { LIVE.people.delete(id); continue; }
    const al = fadeIn * fadeOut;
    const x1 = p.b[0] * W, y1 = p.b[1] * H, x2 = p.b[2] * W, y2 = p.b[3] * H, bw = x2 - x1, bh = y2 - y1;
    if (bw < 3 || bh < 3) continue;
    const fx = (x1 + x2) / 2, fy = y2, queue = !p.staff && p.wait != null;
    p.trail ||= [];
    if (now - (p.lt || 0) > 60) { p.trail.push([fx / W, fy / H]); if (p.trail.length > 18) p.trail.shift(); p.lt = now; }
    if (cam) {
      const box = glEl(`b:${id}`, "pbox");
      glSet(box, `pbox${p.staff ? " staff" : queue ? " queue" : ""}`);
      const bwS = `${bw.toFixed(1)}px`, bhS = `${bh.toFixed(1)}px`;
      if (box._w !== bwS) { box.style.width = box._w = bwS; }
      if (box._h !== bhS) { box.style.height = box._h = bhS; }
      glPlace(box, x1, y1, al);
      keep.add(`b:${id}`);
    } else {           // a glowing dot with a short trail where the person stands
      const c1 = p.staff ? LILAC : ROSE, c2 = p.staff ? LILAC : GOLD;
      for (let i = 1; i < p.trail.length; i++) {
        const [ax, ay] = p.trail[i - 1], [bx2, by2] = p.trail[i];
        g.strokeStyle = rgbaJ(c1, al * .4 * (i / p.trail.length)); g.lineWidth = 2.6 * Math.max(1, k) * (i / p.trail.length) + .5;
        g.beginPath(); g.moveTo(ax * W, ay * H); g.lineTo(bx2 * W, by2 * H); g.stroke();
      }
      const R = 24 * Math.max(.8, k), hg = g.createRadialGradient(fx, fy, 0, fx, fy, R);
      hg.addColorStop(0, rgbaJ(c1, .42 * al)); hg.addColorStop(1, rgbaJ(c2, 0));
      g.fillStyle = hg; g.fillRect(fx - R, fy - R, 2 * R, 2 * R);
      const cg = g.createLinearGradient(fx - 7, fy - 7, fx + 7, fy + 7);
      cg.addColorStop(0, rgbaJ(c1, al)); cg.addColorStop(1, rgbaJ(c2, al));
      g.fillStyle = cg; g.beginPath(); g.arc(fx, fy, 6 * Math.max(.85, k) + 1.5, 0, 7); g.fill();
      g.strokeStyle = `rgba(255,255,255,${.85 * al})`; g.lineWidth = 1.5; g.stroke();
    }
    // tag: number badge of the n-th customer today · time in the shop (or the wait in the queue)
    const el = (now - p.at) / 1000;
    let html, cls, w;
    const fnum = `700 ${11 * ts}px ${SYS}`, ftxt = `${queue ? 600 : 500} ${12 * ts}px ${SYS}`;
    if (p.staff) {
      html = `<span class="num">P</span><span class="t">Personel</span>`; cls = "ptag staff";
      w = (3 + 6 + 10 + 1) * ts + Math.max(18 * ts, textW("P", fnum) + 10 * ts) + textW("Personel", ftxt);
    } else {
      const t = queue ? mmss(p.wait + el) : p.inS != null ? mmss(p.inS + el) : "";
      // estimated gender / age when the installer shows them: "K 34", "E", "38"
      const dtx = [p.g === "F" ? "K" : p.g === "M" ? "E" : "", p.age != null ? String(p.age) : ""].filter(Boolean).join(" ");
      // a dot in the profile charts' colours: amber = woman, light grey = man
      const dot = p.g === "F" || p.g === "M" ? `<i class="${p.g === "F" ? "f" : "m"}"></i>` : "";
      const demo = dtx ? `<span class="d">${dot}${dtx}</span>` : "";
      const dw = dtx ? 13 * ts + (dot ? 10 * ts : 0) + textW(dtx, `600 ${11 * ts}px ${SYS}`) : 0;
      if (p.n != null) {
        html = `<span class="num">${p.n}</span><span class="t">${queue ? CLOCK : ""}${t}</span>${demo}`; cls = `ptag${queue ? " queue" : ""}`;
        w = (3 + 6 + 10 + 1) * ts + Math.max(18 * ts, textW(String(p.n), fnum) + 10 * ts) + textW(t, ftxt) + (queue ? 15 * ts : 0) + dw;
      } else {
        html = `<span class="t">${queue ? CLOCK : ""}${t}</span>${demo}`; cls = "ptag new";
        w = (10 + 10 + 1) * ts + textW(t, ftxt) + (queue ? 15 * ts : 0) + dw;
      }
    }
    const at = cam
      ? [[x1, y1 - tagH - 6], [x1 + 5, y1 + 5], [x1, y2 + 6]]
      : [[fx + 14, fy - tagH / 2], [fx - 14 - w, fy - tagH / 2], [fx - w / 2, fy - tagH - 16]];
    items.push({ key: `t:${id}`, cls, html, w, h: tagH, at, prio: queue ? 70 : p.staff ? 55 : 60, op: al });
    keep.add(`t:${id}`);
  }
  layoutTags(items, W, H, reserved);
  for (const [key, el] of GL.els) if (!keep.has(key)) { el.remove(); GL.els.delete(key); }
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
  const zl = (S.info.zones || []).filter((z) => z.type !== "entrance" && z.type !== "ignore");
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

/* ---------------------------------------------------- installation assistant */
const MOUNT_TIPS = {
  above_door: "Kapının hemen üstüne, 2,5–2,8 m yüksekliğe; 25–30° aşağı eğimle tezgâha doğru. Kapı eşiği görüntüye girmez, çizgi kapıdan yaklaşık 2,5 m içeride sayar. Tezgâhtaki kişilerin başı görünmeli.",
  facing_door: "Arka duvara ya da tezgâhın üstüne, 2,4–3 m yüksekliğe; kapıyı tam karşıdan görecek şekilde. Cam vitrinden ters ışık gelebilir. Çizgiyi kapı eşiğinin hemen içine çizin.",
  corner: "Bir köşeye, 2,6–3 m yüksekliğe; 30–40° eğimle. Kapı görüntünün bir kenarında kalmalı; çizgiyi kapının iç tarafına çizin.",
};
const SETUP = { mount: "above_door", door: null, bg: null, timer: null, poll: null, busy: false };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function loadSetup() {
  await Promise.all([setupChecks(), setupDoor(), setupAudits(), setupAuditState()]);
  clearInterval(SETUP.timer);
  SETUP.timer = setInterval(() => (S.tab === "setup" ? setupChecks().catch(() => {}) : clearInterval(SETUP.timer)), 2000);
}
async function setupChecks() {
  const d = await api("/api/setup/checks");
  const ic = { good: "✓", warn: "!", bad: "✕", info: "i" };
  $("#st-checks").innerHTML = d.checks.map((c) => `<li><span class="ic ${c.status}">${ic[c.status]}</span><span class="t">${esc(c.title)}</span>` +
    `<span class="v">${esc(c.value)}</span>${c.advice ? `<span class="a">${esc(c.advice)}</span>` : ""}</li>`).join("");
  const fp = d.light?.focus_live ?? d.light?.focus_pct;  // the bar follows the lens as it is turned
  $("#st-focus").style.width = fp == null ? "0" : `${Math.min(100, fp)}%`;
  $("#st-focus-v").textContent = fp == null ? "—" : `%${fp}`;
}
async function setupDoor() {
  $("#st-mount-tip").textContent = MOUNT_TIPS[SETUP.mount];
  SETUP.door = await api("/api/setup/door", { mount: SETUP.mount });
  const l = SETUP.door.learnt;
  $("#st-learnt").textContent = l.zone ? `Gözleme göre: ${l.note} (${l.samples} kişi).` : `Gözleme dayalı öneri için ${l.note}.`;
  $("#st-door-learnt").disabled = !l.zone;
  drawDoorPreview();
}
function drawDoorPreview() {
  const cv = $("#st-door-cv");
  const draw = () => {
    const r = cv.getBoundingClientRect(), dpr = Math.min(2, devicePixelRatio || 1);
    if (!r.width) return;
    cv.width = Math.round(r.width * dpr); cv.height = Math.round(r.height * dpr);
    const g = cv.getContext("2d"), W = r.width, H = r.height;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    if (SETUP.bg?.naturalWidth) g.drawImage(SETUP.bg, 0, 0, W, H);
    g.fillStyle = "rgba(0,0,0,.28)"; g.fillRect(0, 0, W, H);
    const line = (z, color, dash, label) => {
      if (!z?.line || z.line.length < 2) return;
      const [[ax, ay], [bx, by]] = z.line.map(([x, y]) => [x * W, y * H]);
      g.lineCap = "round"; g.setLineDash(dash); g.strokeStyle = color; g.lineWidth = 3;
      g.beginPath(); g.moveTo(ax, ay); g.lineTo(bx, by); g.stroke(); g.setLineDash([]);
      if (z.inside_point) {  // a small arrow towards the inside
        const mx = (ax + bx) / 2, my = (ay + by) / 2, ix = z.inside_point[0] * W, iy = z.inside_point[1] * H;
        const L = Math.hypot(ix - mx, iy - my) || 1, ux = (ix - mx) / L, uy = (iy - my) / L, tx = mx + ux * 22, ty = my + uy * 22;
        g.lineWidth = 2; g.beginPath(); g.moveTo(mx, my); g.lineTo(tx, ty); g.lineTo(tx - ux * 7 - uy * 5, ty - uy * 7 + ux * 5);
        g.moveTo(tx, ty); g.lineTo(tx - ux * 7 + uy * 5, ty - uy * 7 - ux * 5); g.stroke();
      }
      g.font = `600 12px ${SYS}`;
      const tw = g.measureText(label).width + 16, lx = Math.min(Math.max(4, (ax + bx) / 2 - tw / 2), W - tw - 4);
      const ly = Math.min(Math.max(4, Math.min(ay, by) - 30), H - 26);
      g.fillStyle = "rgba(20,20,22,.72)"; g.beginPath(); g.roundRect(lx, ly, tw, 22, 11); g.fill();
      g.fillStyle = color; g.fillText(label, lx + 8, ly + 15);
    };
    line(SETUP.door.current, "rgba(255,255,255,.75)", [5, 5], "Şu anki");
    line(SETUP.door.learnt?.zone, "#ff5c8a", [8, 5], "Gözleme göre");
    line(SETUP.door.preset, "#ffbe40", [], "Öneri");
  };
  if (!SETUP.bg) {
    SETUP.bg = new Image();
    SETUP.bg.onload = () => { cv.parentElement.style.aspectRatio = `${SETUP.bg.naturalWidth} / ${SETUP.bg.naturalHeight}`; draw(); };
    SETUP.bg.src = DEMO ? `${DEMO.base}snapshot.jpg` : `api/snapshot.jpg?t=${Date.now()}`;
  }
  draw();
}
async function setupApplyDoor(zone) {
  await send("POST", "/api/setup/door", zone);
  S.info = await api("/api/info");
  toast("Giriş çizgisi kaydedildi");
  setupDoor();
}
async function setupCoat() {
  const btn = $("#st-coat-measure");
  btn.disabled = true;
  let best = null, last = null;
  try {
    for (let i = 5; i > 0; i--) {
      btn.textContent = `Ölçülüyor… ${i * 2} sn`;
      last = await api("/api/setup/coat");
      if (last.behind_counter.length) best = Math.max(best ?? 0, ...last.behind_counter);
      await sleep(2000);
    }
  } finally { btn.disabled = false; btn.textContent = "Tekrar ölç"; }
  const res = $("#st-coat-res"), apply = $("#st-coat-apply");
  if (best == null) {
    res.textContent = "Kasa arkasında kimse görülmedi. Çalışan 'Tezgâh Arkası' bölgesinde, kameraya dönük dursun.";
    apply.classList.add("hidden");
    return;
  }
  const thr = last.suggested ?? Math.round(best * 80) / 100;
  SETUP.coatThr = thr;
  res.innerHTML = `Kasa arkasındaki önlük beyazlığı <b>%${Math.round(best * 100)}</b>` +
    (last.customers_p95 != null ? `, bugünkü müşterilerin %95'i <b>%${Math.round(last.customers_p95 * 100)}</b>'in altında` : "") +
    `. Önerilen eşik <b>${thr}</b> (şu an ${last.threshold}).`;
  apply.classList.remove("hidden");
}
async function setupAuditState() {
  const st = await api("/api/audit").catch(() => ({ active: false }));
  renderAudit(st);
  if (st.active) startAuditPoll();
}
function renderAudit(st) {
  SETUP.audit = st;
  const on = !!st.active;
  $("#st-audit-pad").classList.toggle("hidden", !on);
  $("#st-audit-start").classList.toggle("hidden", on);
  for (const id of ["st-audit-stop", "st-audit-undo-in", "st-audit-undo-out"]) $(`#${id}`).classList.toggle("hidden", !on);
  if (!on) return;
  $("#st-m-in").textContent = st.manual_in; $("#st-m-out").textContent = st.manual_out;
  $("#st-s-in").textContent = st.system_in; $("#st-s-out").textContent = st.system_out;
}
function startAuditPoll() {
  clearInterval(SETUP.poll);
  SETUP.poll = setInterval(async () => {
    if (S.tab !== "setup" || !SETUP.audit?.active) { clearInterval(SETUP.poll); return; }
    if (!SETUP.busy) renderAudit(await api("/api/audit").catch(() => SETUP.audit));
  }, 1000);
}
async function auditTap(kind, undo = false) {
  if (!undo) {
    navigator.vibrate?.(25);
    const b = $(`#st-audit-pad .tap.${kind}`);
    b.classList.add("flash"); setTimeout(() => b.classList.remove("flash"), 160);
    const el = $(`#st-m-${kind}`); el.textContent = Number(el.textContent) + 1;  // instant feedback
  }
  SETUP.busy = true;
  try { renderAudit(await send("POST", "/api/audit/tap", { kind, undo })); } finally { SETUP.busy = false; }
}
async function auditStop() {
  const btn = $("#st-audit-stop");
  btn.disabled = true;
  for (let i = 6; i > 0; i--) { btn.textContent = `Son sayımlar bekleniyor… ${i}`; await sleep(1000); }
  try {
    const out = await send("POST", "/api/audit/stop", {});
    renderAudit({ active: false });
    $("#st-audit-result").innerHTML = auditCard(out.result);
    setupAudits();
  } finally { btn.disabled = false; btn.textContent = "Testi bitir"; }
}
function auditCard(r) {
  const line = (k, name) => {
    const x = r[k];
    return `${name}: <b>${x.manual}</b> elle · <b>${x.system}</b> sistem · ${x.matched} eşleşti` +
      (x.missed ? ` · ${x.missed} kaçırıldı` : "") + (x.extra ? ` · ${x.extra} fazla` : "");
  };
  const ok = r.overall == null ? "—" : `%${r.overall}`;
  return `<div class="audit-res"><div class="big">${ok}</div><div class="lines">Kişi kişi eşleşen sayım doğruluğu<br>${line("in", "Giren")}<br>${line("out", "Çıkan")}</div></div>`;
}
async function setupAudits() {
  const rows = await api("/api/audits").catch(() => []);
  $("#st-audits").innerHTML = rows.length
    ? `<thead><tr><th>Test</th><th class="num">Giren<br><span class="muted">elle / sistem</span></th><th class="num">Çıkan<br><span class="muted">elle / sistem</span></th><th class="num">Doğruluk</th></tr></thead><tbody>` +
      rows.map((a) => {
        const r = a.result || { in: {}, out: {} };
        return `<tr><td>${trDate(a.day)} ${timeOf(a.started_at)} <span class="muted">· ${fmtDur(a.ended_at - a.started_at)}</span>` +
          `${a.note ? `<br><span class="muted small">${esc(a.note)}</span>` : ""}</td>` +
          `<td class="num">${r.in.manual ?? "—"} / ${r.in.system ?? "—"}</td><td class="num">${r.out.manual ?? "—"} / ${r.out.system ?? "—"}</td>` +
          `<td class="num"><b>${r.overall == null ? "—" : "%" + r.overall}</b></td></tr>`;
      }).join("") + "</tbody>"
    : `<tbody><tr><td class="muted">Henüz doğruluk testi yapılmadı.</td></tr></tbody>`;
}

/* ------------------------------------------------------ customer profile */
async function loadProfile() {
  $("#pf-off").classList.toggle("hidden", !!S.info.demographics);
  const d = await api("/api/demographics", rangeOf("pf-range"));
  const pct = (v) => (v == null ? "—" : `%${v}`);
  $("#pf-tiles").innerHTML = [
    // a share of a handful of visits says little: flag it rather than hide it
    tile("Kadın ziyaretçi", pct(d.female_pct), d.with_gender
      ? `${d.female} kadın · ${d.male} erkek${d.with_gender < 20 ? " · az veri, yorumlamayın" : ""}` : "tahmin yok"),
    tile("Medyan yaş", d.median_age ?? "—", "tahmini"),
    tile("En kalabalık yaş grubu", d.top_band ?? "—"),
    tile("Tahmin yapılan ziyaret", pct(d.coverage_gender), d.visits ? `cinsiyet ${d.with_gender}/${d.visits} · yaş %${d.coverage_age ?? 0}` : "ziyaret yok"),
  ].join("");
  const female = css("--s2"), male = css("--s1");
  chart("ch-pf-age", {
    type: "bar",
    data: {
      labels: d.bands.map((b) => b.band),
      datasets: [barDs("Kadın", d.bands.map((b) => b.F), female), barDs("Erkek", d.bands.map((b) => b.M), male),
                 barDs("Cinsiyet belirsiz", d.bands.map((b) => b.unknown), css("--neutral"))],
    },
    options: { scales: { x: { stacked: true, grid: { display: false } }, y: { stacked: true, ticks: { precision: 0 } } } },
  });
  const byH = Object.fromEntries(d.by_hour.map((r) => [r.hour, r]));
  const rows = openHours()(Array.from({ length: 24 }, (_, h) => {
    const r = byH[h] || { F: 0, M: 0 };
    return { hour: h, F: r.F, M: r.M, entries: r.F + r.M };
  }));
  chart("ch-pf-hour", {
    type: "bar",
    data: {
      labels: rows.map((r) => hh(r.hour)),
      datasets: [barDs("Kadın", rows.map((r) => r.F || 0), female), barDs("Erkek", rows.map((r) => r.M || 0), male)],
    },
    options: { scales: { x: { stacked: true, grid: { display: false } }, y: { stacked: true, ticks: { precision: 0 } } } },
  });
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
  const demo = !!S.info.demographics;  // estimated gender / age of the visit, when the feature is on
  const prof = (v) => (v.is_staff ? "" : [v.gender === "F" ? "Kadın" : v.gender === "M" ? "Erkek" : "", v.age != null ? `~${Math.round(v.age)}` : ""]
    .filter(Boolean).join(" · ") || '<span class="muted">belirsiz</span>');
  $("#tb-visits").innerHTML = `<thead><tr><th>Takip no</th><th>Başlangıç</th><th class="num">Süre</th><th>Tür</th>${demo ? "<th>Profil (tahmini)</th>" : ""}<th>Giriş</th><th>Çıkış</th><th class="num">Sıra bekleme</th><th class="num">Hizmet</th><th>Bölgeler</th></tr></thead><tbody>` +
    (rows.map((v) => `<tr><td class="muted">${esc(v.uid.split("-")[1])}</td><td>${timeOf(v.started_at)}</td><td class="num">${fmtDur(v.duration_s)}</td><td>${v.is_staff ? '<span class="pill staff">Personel</span>' : '<span class="pill">Müşteri</span>'}</td>${demo ? `<td>${prof(v)}</td>` : ""}<td>${yes(v.entered)}</td><td>${yes(v.exited)}</td><td class="num">${v.abandoned ? `${fmtDur(v.queue_wait_s)} (vazgeçti)` : fmtDur(v.queue_wait_s)}</td><td class="num">${fmtDur(v.service_s)}</td><td>${Object.entries(v.zones).map(([k, s]) => `${esc(names[k] || k)} ${Math.round(s)}sn`).join(", ")}</td></tr>`).join("")
      || `<tr><td colspan="${demo ? 10 : 9}" class="muted">Kayıt yok.</td></tr>`) + "</tbody>";
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
    const prefix = { shelf: "raf", queue: "sira", service: "tezgah", staff: "personel", entrance: "kapi", ignore: "yoksay" }[type];
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
  "demographics.enabled": "Yaş ve cinsiyet tahmini (cihazda yapılır, görüntü saklanmaz)",
  "demographics.show_live": "Tahmini canlı görünümde kişi etiketinde göster",
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
  for (const seg of ["sh-range", "hm-range", "pf-range"]) {
    if (askedDays) $$(`#${seg} button`).forEach((x) => x.classList.toggle("active", x.dataset.days === askedDays));
    $$(`#${seg} button`).forEach((b) => b.addEventListener("click", () => {
      $$(`#${seg} button`).forEach((x) => x.classList.toggle("active", x === b));
      showTab(S.tab);
    }));
  }
  $$("#st-mount button").forEach((b) => b.addEventListener("click", () => {
    $$("#st-mount button").forEach((x) => x.classList.toggle("active", x === b));
    SETUP.mount = b.dataset.m;
    setupDoor();
  }));
  $("#st-door-preset").addEventListener("click", () => setupApplyDoor(SETUP.door.preset));
  $("#st-door-learnt").addEventListener("click", () => SETUP.door.learnt?.zone && setupApplyDoor(SETUP.door.learnt.zone));
  $("#st-coat-measure").addEventListener("click", setupCoat);
  $("#st-coat-apply").addEventListener("click", async () => {
    await send("POST", "/api/setup/coat", { threshold: SETUP.coatThr });
    toast("Önlük eşiği kaydedildi");
    $("#st-coat-apply").classList.add("hidden");
  });
  $("#st-audit-start").addEventListener("click", async () => {
    renderAudit(await send("POST", "/api/audit/start"));
    $("#st-audit-result").innerHTML = "";
    startAuditPoll();
  });
  $$("#st-audit-pad .tap").forEach((b) => b.addEventListener("pointerdown", (e) => { e.preventDefault(); auditTap(b.dataset.k); }));
  $("#st-audit-undo-in").addEventListener("click", () => auditTap("in", true));
  $("#st-audit-undo-out").addEventListener("click", () => auditTap("out", true));
  $("#st-audit-stop").addEventListener("click", auditStop);
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
