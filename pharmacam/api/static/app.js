/* PharmaCam dashboard — vanilla JS + Chart.js.
 * Runs against the device API, or in demo mode (GitHub Pages) against static
 * JSON files whose names come from apiKey() (mirrored in scripts/build_site.py). */
"use strict";

const DEMO = window.PHARMACAM_DEMO || null;
{
  const th = new URLSearchParams(location.search).get("theme");
  if (th === "light" || th === "dark") document.documentElement.dataset.theme = th;
}
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const S = { info: null, charts: {}, tab: "live", liveTimer: null, demoFrame: 0, fc: null, fcSel: 0, zones: [], zSel: -1 };
const ZONE_TYPES = {
  entrance: { name: "Giriş kapısı", color: "#ffc400" },
  queue: { name: "Sıra alanı", color: "#ff6347" },
  service: { name: "Tezgâh", color: "#ba55d3" },
  staff: { name: "Personel", color: "#2ecc71" },
  shelf: { name: "Raf", color: "#3498db" },
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
  await tickLive();
  S.liveTimer = setInterval(tickLive, 1000);
}
async function tickLive() {
  let lv;
  if (DEMO) {
    const n = DEMO.liveFrames || 1;
    S.demoFrame = (S.demoFrame + 1) % n;
    lv = await fetch(`${DEMO.base}live/live_${pad(S.demoFrame)}.json`).then((r) => r.json());
  } else {
    lv = await api("/api/live");
  }
  $("#lv-occ").textContent = lv.occupancy;
  $("#lv-queue").textContent = lv.queue_length;
  $("#lv-queue-sub").textContent = lv.queue_waits?.length ? `en uzun bekleme ${fmtDur(lv.queue_waits[0])}` : "sıra yok";
  $("#lv-staff").textContent = lv.staff_on_duty;
  $("#lv-in").textContent = lv.entries_today;
  $("#lv-out").textContent = lv.exits_today;
  $("#lv-in-sub").textContent = lv.staff_entries_today ? `+${lv.staff_entries_today} personel girişi` : "müşteri";
  $("#lv-fps").textContent = lv.camera_ok ? `${lv.fps} kare/sn işleniyor` : "kamera beklemede";
  const names = lv.zone_names || {};
  const zl = (S.info.zones || []).filter((z) => z.type !== "entrance");
  $("#zone-live").innerHTML = zl.map((z) => `<li><span class="sw" style="background:${ZONE_TYPES[z.type].color}"></span>${esc(names[z.id] || z.name)}<span class="muted small">${ZONE_TYPES[z.type].name}</span><span class="n">${lv.zone_counts?.[z.id] || 0}</span></li>`).join("");

  const img = $("#live-img"), off = $("#live-off");
  if (!S.info.live_view) {
    off.textContent = "Canlı görüntü ayarlardan kapatılmış.";
    off.classList.remove("hidden");
  } else if (!lv.camera_ok && !DEMO) {
    off.textContent = "Kamera bağlı değil veya başlatılıyor. Sayaçlar ve geçmiş veriler kullanılabilir.";
    off.classList.remove("hidden");
  } else {
    off.classList.add("hidden");
    const src = DEMO ? `${DEMO.base}live/frame_${pad(S.demoFrame)}.jpg` : `api/live.jpg?t=${Date.now()}`;
    if (!img._busy) {
      img._busy = true;
      const pre = new Image();
      pre.onload = () => { img.src = pre.src; img._busy = false; };
      pre.onerror = () => { img._busy = false; };
      pre.src = src;
    }
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
  if (DEMO && !new URLSearchParams(location.search).has("shot")) {
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
