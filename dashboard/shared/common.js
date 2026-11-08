// Shared client library for the Radar dashboard (no dependencies; talks to the JSON API in server.py).
// ?snap=<ms>: hold the load event so headless screenshot tools capture the rendered page
if (/[?&]snap=/.test(location.search)) { const sc = document.createElement("script"); sc.src = "/__wait.js?ms=" + new URLSearchParams(location.search).get("snap"); document.head.appendChild(sc); }
export const TZ = "Europe/Helsinki";
export const COL = { fc: "#007A5A", real: "#111111", spot: "#C2185B", up: "#D55E00", down: "#0072B2" };

let BASE = "";   // API prefix: "" for the main app, "/live" for the simulated Live app
let EXTRA = {};   // fixed query parameters added to every call (the Live tab's session id)
export const setApiBase = (b, extra = {}) => { BASE = b; EXTRA = extra; };
export async function api(path, params = {}) {
  const q = new URLSearchParams(Object.entries({ ...params, ...EXTRA }).filter(([, v]) => v != null && v !== ""));
  let r;
  try { r = await fetch(`${BASE}/api/${path}?${q}`); } catch (e) { throw new Error("Cannot reach the server. Is it running?"); }
  let b = null; try { b = await r.json(); } catch {}
  if (!r.ok) throw new Error((b && b.error) || "HTTP " + r.status);
  return b;
}

const dtf = (o) => new Intl.DateTimeFormat("en-GB", { timeZone: TZ, ...o });
const F = {
  hm: dtf({ hour: "2-digit", minute: "2-digit", hour12: false }),
  h: dtf({ hour: "2-digit", hour12: false }),
  day: dtf({ weekday: "short", day: "numeric", month: "short" }),
  dayLong: dtf({ weekday: "long", day: "numeric", month: "long" }),
  key: dtf({ year: "numeric", month: "2-digit", day: "2-digit" }),
};
export const fmt = {
  hm: (t) => F.hm.format(new Date(t)),
  h: (t) => F.h.format(new Date(t)).replace(/^24/, "00"),
  day: (t) => F.day.format(new Date(t)),
  dayLong: (t) => F.dayLong.format(new Date(t)),
  dayKey: (t) => F.key.format(new Date(t)),
  stamp: (t) => `${F.day.format(new Date(t))} ${F.hm.format(new Date(t))}`,
  eur: (x) => (x == null ? "–" : Math.round(x).toString().replace("-", "−")),
  pct: (p) => (p == null ? "–" : Math.round(p * 100) + "%"),
  range: (a, b) => `${fmt.hm(a)}–${fmt.hm(b)}`,
};
export const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
export const $ = (id) => document.getElementById(id);

// Level of the current forecast from alerts: 0 calm, 1 watch, 2 act
export function level(alerts) {
  if (!alerts || !alerts.alerts || !alerts.alerts.length) return 0;
  return alerts.alerts.some((a) => a.severity === "act") ? 2 : 1;
}
export const sevLevel = (s) => (s >= 2 ? 2 : s === 1 ? 1 : 0);
// Replace {t:ISO} tokens used in server alert messages
export const cleanMsg = (m) => esc(m).replace(/\{t:([^}]+)\}/g, (_, t) => fmt.hm(t));

// Session: loads runs/timeline, steps through origins, notifies the page.
export class Session {
  constructor(onData) { this.onData = onData; this.seq = 0; this.idx = 0; this.timer = null; this.listeners = []; this.cache = new Map(); }
  async init() {
    const want = new URLSearchParams(location.search).get("run");
    // when the page names its run, ask for the timeline in parallel with the run list (one round trip less)
    const [meta, tl] = await Promise.all([api("meta"), want ? api("timeline", { run: want, demo: 1 }).catch(() => null) : null]);
    this.meta = meta; if (tl) this.pre = { run: want, tl };
    const ok = this.meta.runs.filter((r) => r.ok);
    if (!ok.length) throw new Error("No forecast file could be loaded.");
    this.run = (ok.find((r) => r.name === want) || ok[ok.length - 1]).name;
    this.runs = ok;
    await this.setRun(this.run, new URLSearchParams(location.search).get("origin"));
  }
  async setRun(name, origin) {
    this.stop(); this.run = name; this.cache.clear();
    const pre = this.pre && this.pre.run === name ? this.pre.tl : null; this.pre = null;
    this.tl = pre || await api("timeline", { run: name, demo: 1 });
    this.n = this.tl.times.length;
    const i = origin ? this.tl.times.indexOf(origin) : -1;
    await this.go(i >= 0 ? i : Math.floor(this.n / 2));
  }
  async go(i) {
    i = Math.max(0, Math.min(this.n - 1, i)); this.idx = i;
    const my = ++this.seq, origin = this.tl.times[i];
    this.listeners.forEach((f) => f());
    try {
      const [f, a] = await this.fetchAt(origin);
      if (my === this.seq) this.onData(f, a);
    } catch (e) { if (my === this.seq) this.onData(null, null, e); }
    for (const j of [i - 2, i - 1, i + 1, i + 2]) if (j >= 0 && j < this.n) this.fetchAt(this.tl.times[j]).catch(() => {});
  }
  fetchAt(origin) {   // cached per run+origin so scrubbing back and forth is instant
    const k = this.run + "|" + origin;
    let p = this.cache.get(k);
    if (!p) { p = Promise.all([api("forecast", { run: this.run, origin }), api("alerts", { run: this.run, origin })]); p.catch(() => this.cache.delete(k)); this.cache.set(k, p); }
    return p;
  }
  // live mode: re-read the run list; when the run has more origins, extend the timeline. Returns {wasLast} or null.
  async refresh() {
    const m = await api("meta"), r = m.runs.find((x) => x.name === this.run);
    if (!r || !r.ok || r.n_origins === this.n) return null;
    const wasLast = this.idx === this.n - 1;
    this.tl = await api("timeline", { run: this.run, demo: 1 });
    this.n = this.tl.times.length; this.meta = m; this.cache.clear();
    return { wasLast };
  }
  get origin() { return this.tl.times[this.idx]; }
  goOrigin(o) { const i = this.tl.times.indexOf(o); if (i >= 0) return this.go(i); }
  toggle(ms = 600) {
    if (this.timer) return this.stop();
    this.timer = setInterval(() => { if (this.idx >= this.n - 1) return this.stop(); this.go(this.idx + 1); }, ms);
    this.listeners.forEach((f) => f());
  }
  stop() { if (this.timer) { clearInterval(this.timer); this.timer = null; this.listeners.forEach((f) => f()); } }
  get playing() { return !!this.timer; }
}

// Fill a <select> with runs and wire it
export function runSelect(sel, s) {
  sel.innerHTML = s.runs.map((r) => `<option value="${esc(r.name)}">${esc(r.label)}</option>`).join("");
  sel.value = s.run; sel.onchange = () => s.setRun(sel.value);
}
export function demoSelect(sel, s, placeholder = "Jump to…") {
  const d = s.tl.demo || [];
  sel.innerHTML = `<option value="">${esc(placeholder)}</option>` + d.map((x) => `<option value="${esc(x.origin)}">${esc(x.label)}</option>`).join("");
  sel.onchange = () => { if (sel.value) s.goOrigin(sel.value); sel.value = ""; };
}
export function keys(s) {
  addEventListener("keydown", (e) => {
    if (/input|select|textarea/i.test(e.target.tagName) && e.key !== " ") return;
    if (e.key === "ArrowLeft") s.go(s.idx - (e.shiftKey ? 24 : 1));
    else if (e.key === "ArrowRight") s.go(s.idx + (e.shiftKey ? 24 : 1));
    else if (e.key === " " && !/button|select/i.test(e.target.tagName)) { e.preventDefault(); s.toggle(); }
  });
}

// ---------- charts ----------
function nice(lo, hi) {
  if (hi - lo < 1e-9) { hi += 1; lo -= 1; }
  const span = hi - lo, raw = span / 5, mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((x) => x >= raw);
  return { lo: Math.floor(lo / step) * step, hi: Math.ceil(hi / step) * step, step };
}
// o: {h, pad, bands:[{pts:[{x,lo,hi}],color,opacity}], lines:[{pts:[{x,y}],color,w,dash,step}],
//     dots:[{pts:[{x,y}],color,r}], bars:[{pts:[{x,y}],color,w}], vlines:[{x,color,dash,label}],
//     hover:{xs:[ms], cb:(i)=>void}, yfmt, ymin, ymax, zero, grid:true, tickEvery, labels:true}
export function chart(host, o) {
  const W = Math.max(280, host.clientWidth || 600), H = o.h || 260;
  const p = Object.assign({ l: 44, r: 10, t: 10, b: 22 }, o.pad || {});
  const xsAll = [], ysAll = [];
  (o.bands || []).forEach((b) => b.pts.forEach((q) => { xsAll.push(q.x); ysAll.push(q.lo, q.hi); }));
  (o.lines || []).forEach((l) => l.pts.forEach((q) => { if (q.y != null) { xsAll.push(q.x); ysAll.push(q.y); } }));
  (o.dots || []).forEach((l) => l.pts.forEach((q) => { if (q.y != null) { xsAll.push(q.x); ysAll.push(q.y); } }));
  (o.bars || []).forEach((l) => l.pts.forEach((q) => { xsAll.push(q.x); ysAll.push(q.y, 0); }));
  const xmin = o.xmin ?? Math.min(...xsAll), xmax = o.xmax ?? Math.max(...xsAll);
  let y0 = o.ymin ?? Math.min(...ysAll), y1 = o.ymax ?? Math.max(...ysAll);
  if (o.zero) { y0 = Math.min(y0, 0); y1 = Math.max(y1, 0); }
  const ny = o.ymin != null && o.ymax != null ? { lo: y0, hi: y1, step: o.ystep || (y1 - y0) / 4 } : nice(y0, y1);
  const sx = (x) => p.l + ((x - xmin) / (xmax - xmin || 1)) * (W - p.l - p.r);
  const sy = (y) => H - p.b - ((y - ny.lo) / (ny.hi - ny.lo || 1)) * (H - p.t - p.b);
  const yfmt = o.yfmt || fmt.eur;
  let s = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="${esc(o.label || "chart")}" style="display:block;overflow:visible">`;
  for (let v = ny.lo; v <= ny.hi + 1e-9; v += ny.step) {
    s += `<line x1="${p.l}" x2="${W - p.r}" y1="${sy(v)}" y2="${sy(v)}" class="gl${Math.abs(v) < 1e-9 ? " gz" : ""}"/>`;
    s += `<text x="${p.l - 6}" y="${sy(v) + 3.5}" text-anchor="end" class="ax">${yfmt(v)}</text>`;
  }
  // x ticks at local whole hours
  const hrs = (xmax - xmin) / 3.6e6, every = o.tickEvery || [1, 2, 3, 6, 12, 24, 48].find((k) => (W - p.l - p.r) / (hrs / k) >= 54) || 48;
  const first = Math.ceil(xmin / 3.6e6) * 3.6e6;
  for (let t = first, n = 0; t <= xmax + 1; t += 3.6e6) {
    const lh = +fmt.h(t); if (lh % every) continue;
    const midnight = lh === 0;
    s += `<line x1="${sx(t)}" x2="${sx(t)}" y1="${p.t}" y2="${H - p.b}" class="gl${midnight ? " gm" : ""}" ${o.vgrid === false ? 'style="display:none"' : ""}/>`;
    if (!o.noX) s += `<text x="${sx(t)}" y="${H - 6}" text-anchor="middle" class="ax">${midnight ? fmt.day(t).split(" ").slice(0, 2).join(" ") : fmt.h(t)}</text>`;
    n++;
  }
  (o.bands || []).forEach((b) => {
    const up = b.pts.map((q) => `${sx(q.x)},${sy(q.hi)}`).join(" L"), dn = b.pts.slice().reverse().map((q) => `${sx(q.x)},${sy(q.lo)}`).join(" L");
    s += `<path d="M${up} L${dn} Z" fill="${b.color}" fill-opacity="${b.opacity ?? 0.2}" stroke="none"/>`;
  });
  (o.bars || []).forEach((b) => b.pts.forEach((q) => { const w = b.w || 4; s += `<rect x="${sx(q.x) - w / 2}" y="${Math.min(sy(q.y), sy(0))}" width="${w}" height="${Math.abs(sy(q.y) - sy(0))}" fill="${b.color}" opacity="${b.opacity ?? 0.85}"/>`; }));
  (o.lines || []).forEach((l) => {
    let d = "", pen = false, prev = null;
    l.pts.forEach((q) => {
      if (q.y == null) { pen = false; return; }
      if (l.step && prev != null && pen) d += `L${sx(q.x)},${sy(prev)}`;
      d += (pen ? "L" : "M") + `${sx(q.x)},${sy(q.y)}`; pen = true; prev = q.y;
    });
    s += `<path d="${d}" fill="none" stroke="${l.color}" stroke-width="${l.w || 2}" ${l.dash ? `stroke-dasharray="${l.dash}"` : ""} stroke-linejoin="round" stroke-linecap="round" ${l.cls ? `class="${l.cls}"` : ""}/>`;
  });
  (o.dots || []).forEach((l) => l.pts.forEach((q) => { if (q.y != null) s += `<circle cx="${sx(q.x)}" cy="${sy(q.y)}" r="${l.r || 2.5}" fill="${l.color}"/>`; }));
  (o.vlines || []).forEach((v) => {
    s += `<line x1="${sx(v.x)}" x2="${sx(v.x)}" y1="${p.t}" y2="${H - p.b}" stroke="${v.color || "#888"}" stroke-width="1.5" ${v.dash ? `stroke-dasharray="${v.dash}"` : ""}/>`;
    if (v.label) s += `<text x="${sx(v.x) + 4}" y="${p.t + 10}" class="ax" fill="${v.color || "#888"}">${esc(v.label)}</text>`;
  });
  s += `<line id="xh" y1="${p.t}" y2="${H - p.b}" class="xh" style="display:none"/><rect class="hit" x="${p.l}" y="${p.t}" width="${W - p.l - p.r}" height="${H - p.t - p.b}" fill="transparent"/></svg>`;
  host.innerHTML = s;
  const svg = host.firstChild, xh = svg.querySelector(".xh"), hit = svg.querySelector(".hit");
  if (o.hover) {
    const xs = o.hover.xs;
    const mv = (e) => {
      const r = svg.getBoundingClientRect(), px = (e.clientX - r.left) * (W / r.width);
      let bi = 0, bd = 1e18; xs.forEach((x, i) => { const d = Math.abs(sx(x) - px); if (d < bd) { bd = d; bi = i; } });
      xh.setAttribute("x1", sx(xs[bi])); xh.setAttribute("x2", sx(xs[bi])); xh.style.display = "";
      o.hover.cb(bi, e, sx(xs[bi]));
    };
    hit.addEventListener("pointermove", mv); hit.addEventListener("pointerdown", mv);
    hit.addEventListener("pointerleave", () => { xh.style.display = "none"; o.hover.leave && o.hover.leave(); });
  }
  return { sx, sy, W, H, p };
}

// Tooltip bubble pinned in a positioned container
export function tip(container) {
  const el = document.createElement("div"); el.className = "tip"; el.style.display = "none"; container.appendChild(el);
  return {
    show(html, e) { el.innerHTML = html; el.style.display = "block"; const r = container.getBoundingClientRect(); let x = e.clientX - r.left + 12, y = e.clientY - r.top + 12; if (x + el.offsetWidth > r.width - 4) x -= el.offsetWidth + 24; if (y + el.offsetHeight > r.height - 4) y = Math.max(2, e.clientY - r.top - el.offsetHeight - 12); el.style.left = Math.max(2, x) + "px"; el.style.top = y + "px"; },
    hide() { el.style.display = "none"; },
  };
}

// Convenient accessors on a forecast payload
export const rowsX = (f) => f.rows.map((r) => +new Date(r.t));
export const series = (f, k) => f.rows.map((r) => ({ x: +new Date(r.t), y: r[k] }));
export function summary(f) {
  const r = f.rows, avg = (a, k) => a.reduce((s, x) => s + x[k], 0) / a.length;
  const n2 = r.slice(0, 8), mx = (k) => r.reduce((b, x) => (x[k] > b[k] ? x : b), r[0]);
  const hi = mx("p_spike_high"), lo = mx("p_spike_low");
  const pu = avg(n2, "p_up");
  return {
    p50now: r[0].p50, p10now: r[0].p10, p90now: r[0].p90, spotNow: r[0].spot,
    pUp2h: pu, dir2h: pu >= 0.5 ? "up" : "down", dirP2h: Math.max(pu, 1 - pu),
    pUp10h: avg(r, "p_up"), hi, lo, top: hi.p_spike_high >= lo.p_spike_low ? { side: "high", p: hi.p_spike_high, t: hi.t } : { side: "low", p: lo.p_spike_low, t: lo.t },
    width: avg(r, "p90") - avg(r, "p10"), maxP50: mx("p50"), spikeN: r.filter((x) => x.p_spike_high >= 0.38 || x.p_spike_low >= 0.42).length,
  };
}
export function showError(host, e) { host.innerHTML = `<div class="err">Could not load this forecast: ${esc(e.message)}</div>`; }
