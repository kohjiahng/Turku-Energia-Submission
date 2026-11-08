// LiveChart: scrolling "forecast time" chart. A fixed vertical line marks the forecast origin; time scrolls under it.
// Left of the line: actual + latest forecast (track). Right: the 10 h forecast fan, spot, optional actual.
// One large price chart with a spike-chance strip along its bottom edge. The forecast morphs with a tween when the origin changes.
import { fmt, tip, esc } from "./common.js";

const Q = 9e5, HR = 3.6e6, PL = 44, PR = 10;
const lerp = (a, b, t) => a + (b - a) * t, ease = (t) => 1 - Math.pow(1 - t, 3);
const fin = Number.isFinite;
let uid = 0;

function niceTicks(lo, hi) {
  if (hi - lo < 1e-9) { hi += 1; lo -= 1; }
  const raw = (hi - lo) / 5, mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((x) => x >= raw), out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(6));
  return out;
}
function pathLine(pts) {
  let d = "", pen = false;
  for (const p of pts) { if (!fin(p.y)) { pen = false; continue; } d += (pen ? "L" : "M") + p.x.toFixed(1) + "," + p.y.toFixed(1); pen = true; }
  return d;
}
function pathBand(pts) {  // pts: {x, lo, hi} in pixels; split into runs at gaps
  let d = "", run = [];
  const flush = () => { if (run.length > 1) d += "M" + run.map((p) => p.x.toFixed(1) + "," + p.hi.toFixed(1)).join("L") + "L" + run.slice().reverse().map((p) => p.x.toFixed(1) + "," + p.lo.toFixed(1)).join("L") + "Z"; run = []; };
  for (const p of pts) { if (fin(p.lo) && fin(p.hi)) run.push(p); else flush(); }
  flush(); return d;
}
const entriesFrom = (f) => f.rows.map((r) => ({ t: +new Date(r.t), p10: r.p10, p50: r.p50, p90: r.p90, hi: r.p_spike_high, lo: r.p_spike_low, pu: r.p_up }));

// Interpolate two states (aligned by delivery time). Entries in only one state shrink to their p50 / zero spike chance.
function mix(from, to, e) {
  const m = new Map(), keys = new Set();
  from.e.forEach((x) => { m.set(x.t, [x, null]); keys.add(x.t); });
  to.e.forEach((x) => { const v = m.get(x.t); if (v) v[1] = x; else m.set(x.t, [null, x]); keys.add(x.t); });
  const out = [];
  for (const t of [...keys].sort((a, b) => a - b)) {
    const [a, b] = m.get(t);
    if (a && b) out.push({ t, p10: lerp(a.p10, b.p10, e), p50: lerp(a.p50, b.p50, e), p90: lerp(a.p90, b.p90, e), hi: lerp(a.hi, b.hi, e), lo: lerp(a.lo, b.lo, e), pu: lerp(a.pu, b.pu, e) });
    else { const x = a || b, k = a ? 1 - e : e; out.push({ t, p10: x.p50 + (x.p10 - x.p50) * k, p50: x.p50, p90: x.p50 + (x.p90 - x.p50) * k, hi: x.hi * k, lo: x.lo * k, pu: x.pu }); }
  }
  return { T: lerp(from.T, to.T, e), e: out };
}

export class LiveChart {
  constructor(o) {
    this.o = Object.assign({ backH: 8, fwdH: 10, duration: 380, th: { hi: 0.38, lo: 0.42 }, colour: () => "#888", freeze: null }, o);
    this.id = "lc" + ++uid; this.tip = tip(o.wrap);
    this.track = null; this.state = null; this.tw = null; this.reveal = false; this.pick = null; this.hover = null;
    this.alerts = []; this.blocks = []; this.vis = { actual: true, unknown: true, latest: true, best: true, range: true, spot: true, flag: true, strip: true }; this.y = null; this.zoom = 1; this.zt = 1; this.raf = 0; this.fc = null; this.cut = 0; this.last = null;
    this.reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
    this.bind(o.price);
    new ResizeObserver(() => this.kick()).observe(o.price);
  }
  setTrack(tr) {
    const f = (a) => Float64Array.from(a, (v) => (v == null ? NaN : v));
    this.track = { t0: +new Date(tr.t0), n: tr.n, actual: f(tr.actual), spot: f(tr.spot), dir: f(tr.dir), f10: f(tr.f10), f50: f(tr.f50), f90: f(tr.f90) };
    this.kick();
  }
  setForecast(fc) {
    const to = { T: +new Date(fc.origin), e: entriesFrom(fc) };
    this.fc = fc; this.cut = +new Date(fc.data_cutoff);
    if (!this.state || this.reduced || this.o.duration === 0) { this.state = to; this.tw = null; }
    else {
      const cur = this.cur(performance.now());
      this.tw = { t0: performance.now(), from: { T: cur.T, e: cur.e }, to, fixed: this.o.freeze };
    }
    this.kick();
  }
  setAlerts(a) { this.alerts = (a && a.alerts) || []; }
  setVis(k, b) { this.vis[k] = b; this.kick(); }
  setReveal(b) { this.reveal = b; this.kick(); }
  setHighlight(t0, t1) { this.hl = t0 == null ? null : { t0, t1 }; this.kick(); }
  setPick(t) { this.pick = t; this.kick(); }
  // Horizontal zoom: the forecast-time line stays put, the window around it shrinks (z > 1) or grows (z < 1).
  setZoom(z, instant) { this.zt = Math.max(0.5, Math.min(8, z)); if (instant || this.reduced) this.zoom = this.zt; if (this.o.onZoom) this.o.onZoom(this.zt); this.kick(); }
  zoomBy(f) { this.setZoom(this.zt * f); }
  kick() { if (!this.raf) this.raf = requestAnimationFrame((t) => this.frame(t)); }
  cur(now) {
    if (!this.tw) return { T: this.state.T, e: this.state.e, p: 1, from: null, ease: 1 };
    const tw = this.tw, p = tw.fixed != null ? tw.fixed : Math.min(1, (now - tw.t0) / this.o.duration), e = tw.fixed != null ? tw.fixed : ease(p), m = mix(tw.from, tw.to, e);
    return { T: m.T, e: m.e, p, from: tw.from, ease: e };
  }
  frame(now) {
    this.raf = 0;
    if (!this.state) return;
    const c = this.cur(now), unsettled = this.render(c);
    if (this.tw && this.tw.fixed == null && c.p >= 1) { this.state = this.tw.to; this.tw = null; this.kick(); return; }
    if ((this.tw && this.tw.fixed == null) || unsettled) this.kick();
  }
  geom(W) {
    const z = this.zoom, backH = this.o.backH / z, fwdH = this.o.fwdH / z, spanH = backH + fwdH + 0.25 / z, pw = W - PL - PR;
    return { pw, total: spanH * HR, xNow: PL + (pw * backH) / spanH, pxHr: pw / spanH, backH, fwdH };
  }

  render(c) {
    const o = this.o, W = Math.max(280, o.price.clientWidth || 600), G = this.geom(W), T = c.T, tr = this.track;
    const X = (t) => G.xNow + (t + Q / 2 - T) / G.total * G.pw, Xb = (t) => G.xNow + (t - T) / G.total * G.pw;
    const tmin = T - G.backH * HR, tmax = T + (G.fwdH + 0.25 / this.zoom) * HR, cw = G.pw / G.total * Q;
    const cl = "url(#" + this.id + "L)", cr = "url(#" + this.id + "R)";
    this.last = { c, W, G };
    // visible track quarters
    const past = [];
    if (tr) {
      const k0 = Math.max(0, Math.floor((tmin - tr.t0) / Q)), k1 = Math.min(tr.n - 1, Math.ceil((tmax - tr.t0) / Q));
      for (let k = k0; k <= k1; k++) past.push({ k, t: tr.t0 + k * Q });
    }
    // y-domain from what is visible
    let lo = Infinity, hi = -Infinity;
    const eat = (v) => { if (fin(v)) { if (v < lo) lo = v; if (v > hi) hi = v; } };
    const V = this.vis, known = (q) => q.t + Q <= this.cut;
    past.forEach((q) => { if (V.spot) eat(tr.spot[q.k]); if (q.t < T) { if (known(q) ? V.actual : V.unknown) eat(tr.actual[q.k]); if (V.latest) { eat(tr.f10[q.k]); eat(tr.f90[q.k]); eat(tr.f50[q.k]); } } else if (this.reveal && V.actual) eat(tr.actual[q.k]); });
    c.e.forEach((e) => { if (V.range) { eat(e.p10); eat(e.p90); } if (V.best) eat(e.p50); });
    if (!fin(lo)) { lo = 0; hi = 100; }
    const pad = (hi - lo) * 0.08 + 1; lo -= pad; hi += pad;
    let unsettled = false;
    if (Math.abs(this.zoom - this.zt) > 0.002) { this.zoom = lerp(this.zoom, this.zt, 0.25); unsettled = true; } else this.zoom = this.zt;
    if (!this.y) this.y = { lo, hi };
    else { const sp = this.y.hi - this.y.lo; if (Math.abs(this.y.lo - lo) + Math.abs(this.y.hi - hi) > sp * 0.004) { this.y.lo = lerp(this.y.lo, lo, 0.22); this.y.hi = lerp(this.y.hi, hi, 0.22); unsettled = true; } else this.y = { lo, hi }; }
    const Hp = Math.max(400, Math.min(540, Math.round(W * 0.6))), tp = 10, bp = 50, yb = Hp - bp + 22, Y = (v) => Hp - bp - (v - this.y.lo) / (this.y.hi - this.y.lo) * (Hp - tp - bp);
    const nowLabel = fmt.hm(Math.round(T / HR) * HR);

    // ---------- price panel ----------
    let s = `<svg viewBox="0 0 ${W} ${Hp}" width="${W}" height="${Hp}" role="img" aria-label="Price forecast with actual" style="display:block;overflow:hidden;touch-action:pan-y;user-select:none">`;
    s += `<defs><clipPath id="${this.id}L"><rect x="${PL}" y="0" width="${G.xNow - PL}" height="${Hp}"/></clipPath><clipPath id="${this.id}R"><rect x="${G.xNow}" y="0" width="${W - PR - G.xNow}" height="${Hp}"/></clipPath></defs>`;
    for (const v of niceTicks(this.y.lo, this.y.hi)) s += `<line x1="${PL}" x2="${W - PR}" y1="${Y(v).toFixed(1)}" y2="${Y(v).toFixed(1)}" class="gl${Math.abs(v) < 1e-9 ? " gz" : ""}"/><text x="${PL - 6}" y="${(Y(v) + 3.5).toFixed(1)}" text-anchor="end" class="ax">${fmt.eur(v)}</text>`;
    const every = [1, 2, 3, 6].find((k) => G.pxHr * k >= 54) || 6;
    for (let t = Math.ceil(tmin / HR) * HR; t <= tmax; t += HR) {
      const lh = +fmt.h(t); if (lh % every) continue;
      const x = Xb(t).toFixed(1), mid = lh === 0;
      s += `<line x1="${x}" x2="${x}" y1="${tp}" y2="${Hp - bp}" class="gl${mid ? " gm" : ""}"/><text x="${x}" y="${Hp - 6}" text-anchor="middle" class="ax">${mid ? fmt.day(t).split(" ").slice(0, 2).join(" ") : fmt.h(t)}</text>`;
    }
    // the future side gets a faint tint so past / forecast read at a glance
    s += `<rect x="${G.xNow}" y="${tp}" width="${W - PR - G.xNow}" height="${Hp - bp - tp}" style="fill:var(--fg)" opacity=".035"/>`;
    // banner alert window: soft shading across the plot, outline on the spike strip
    if (this.hl) s += `<rect x="${(X(this.hl.t0) - cw / 2).toFixed(1)}" y="${tp}" width="${((this.hl.t1 - this.hl.t0) / Q * cw).toFixed(1)}" height="${Hp - bp - tp}" style="fill:var(--fg)" opacity=".1" clip-path="${cr}"/>`;
    // flagged quarters shaded behind the cone
    if (V.flag) c.e.forEach((e) => { const h = e.hi >= o.th.hi, l = e.lo >= o.th.lo; if (!h && !l) return; const up = h && (!l || e.hi - o.th.hi >= e.lo - o.th.lo), p = up ? e.hi : e.lo;
      s += `<rect x="${(X(e.t) - cw / 2).toFixed(1)}" y="${tp}" width="${cw.toFixed(1)}" height="${Hp - bp + 4 + 14 - tp}" style="fill:var(${up ? "--up" : "--down"})" opacity="${(0.04 + 0.2 * p).toFixed(3)}" clip-path="${cr}"/>`; });
    // latest-forecast band (past) and cone band (future)
    if (tr && V.latest) s += `<path d="${pathBand(past.filter((q) => q.t < T).map((q) => ({ x: X(q.t), lo: Y(tr.f10[q.k]), hi: Y(tr.f90[q.k]) })))}" style="fill:var(--fc)" opacity=".13" clip-path="${cl}"/>`;
    if (V.range) s += `<path d="${pathBand(c.e.map((e) => ({ x: X(e.t), lo: Y(e.p10), hi: Y(e.p90) })))}" style="fill:var(--fc)" opacity=".2" clip-path="${cr}"/>`;
    // spot across the window
    if (tr && V.spot) s += `<path d="${pathLine(past.map((q) => ({ x: X(q.t), y: fin(tr.spot[q.k]) ? Y(tr.spot[q.k]) : NaN })))}" fill="none" style="stroke:var(--spot)" stroke-width="1.5" stroke-dasharray="4 3"/>`;
    // ghost of the previous forecast while morphing
    if (V.best && c.from && c.ease < 1) s += `<path d="${pathLine(c.from.e.map((e) => ({ x: X(e.t), y: Y(e.p50) })))}" fill="none" style="stroke:var(--mute)" stroke-width="1.2" stroke-dasharray="2 3" opacity="${(0.7 * (1 - c.ease)).toFixed(2)}" clip-path="${cr}"/>`;
    // latest forecast (past) and forecast p50 (future)
    if (tr && V.latest) s += `<path d="${pathLine(past.filter((q) => q.t < T).map((q) => ({ x: X(q.t), y: fin(tr.f50[q.k]) ? Y(tr.f50[q.k]) : NaN })))}" fill="none" style="stroke:var(--fc)" stroke-width="2" stroke-linejoin="round" clip-path="${cl}"/>`;
    if (V.best) s += `<path d="${pathLine(c.e.map((e) => ({ x: X(e.t), y: Y(e.p50) })))}" fill="none" style="stroke:var(--fc)" stroke-width="2.6" stroke-linejoin="round" clip-path="${cr}"/>`;
    // actual: known (solid) and not-yet-known at the forecast time (dashed); right side only when revealed
    if (tr) {
      const ac = (q) => (fin(tr.actual[q.k]) ? Y(tr.actual[q.k]) : NaN);
      const L = past.filter((q) => q.t < T), kn = L.filter(known), un = L.filter((q) => !known(q));
      if (V.actual) s += `<path d="${pathLine(kn.map((q) => ({ x: X(q.t), y: ac(q) })))}" fill="none" style="stroke:var(--fg)" stroke-width="1.7" clip-path="${cl}"/>`;
      if (un.length && V.unknown) { const lk = kn[kn.length - 1], seg = (lk ? [lk] : []).concat(un); s += `<path d="${pathLine(seg.map((q) => ({ x: X(q.t), y: ac(q) })))}" fill="none" style="stroke:var(--fg)" stroke-width="1.7" stroke-dasharray="3 3" clip-path="${cl}"/>`; }
      if (this.reveal && V.actual) { const R = past.filter((q) => q.t >= T); s += `<path d="${pathLine(R.map((q) => ({ x: X(q.t), y: ac(q) })))}" fill="none" style="stroke:var(--fg)" stroke-width="1.7" clip-path="${cr}"/>`; R.forEach((q) => { if (fin(tr.actual[q.k])) s += `<circle cx="${X(q.t).toFixed(1)}" cy="${ac(q).toFixed(1)}" r="2" style="fill:var(--fg)" clip-path="${cr}"/>`; }); }
    }
    if (this.pick != null) { const x = X(this.pick).toFixed(1); s += `<line x1="${x}" x2="${x}" y1="${tp}" y2="${Hp - bp}" stroke="#888" stroke-dasharray="3 3"/>`; }
    // spike-chance strip along the bottom of the plot: one row showing the bigger of jump / drop per quarter.
    // Colour = side (orange jump, blue drop), darker = likelier. Left of the line: spikes that happened (|actual - spot| > 50),
    // paler where not yet known. With "show what happened" on, forecast quarters that really spiked get an outline.
    if (V.strip) {
      const y1 = Hp - bp + 4, hs = 14, wq = Math.max(1, cw - 0.5), dim = (q) => q.t + Q > this.cut, op = (v) => (0.12 + 0.88 * Math.min(1, v / 0.6)).toFixed(2);
      s += `<text x="${PL - 6}" y="${y1 + 11}" text-anchor="end" class="ax">spike</text><rect x="${PL}" y="${y1}" width="${W - PR - PL}" height="${hs}" style="fill:var(--line)" opacity=".45"/>`;
      const real = (q) => { const a = tr.actual[q.k], sp = tr.spot[q.k]; return fin(a) && fin(sp) ? a - sp : NaN; };
      if (tr) past.filter((q) => q.t < T).forEach((q) => { const d = real(q); if (!fin(d) || Math.abs(d) <= 50) return;
        s += `<rect x="${(X(q.t) - cw / 2).toFixed(1)}" y="${y1}" width="${wq.toFixed(1)}" height="${hs}" style="fill:var(${d > 0 ? "--up" : "--down"})" opacity="${dim(q) ? 0.45 : 1}" clip-path="${cl}"/>`; });
      c.e.forEach((e) => { const up = e.hi >= e.lo, v = Math.max(e.hi, e.lo);
        s += `<rect x="${(X(e.t) - cw / 2).toFixed(1)}" y="${y1}" width="${wq.toFixed(1)}" height="${hs}" style="fill:var(${up ? "--up" : "--down"})" opacity="${op(v)}" clip-path="${cr}"/>`; });
      if (this.reveal && tr) past.filter((q) => q.t >= T).forEach((q) => { const d = real(q); if (!fin(d) || Math.abs(d) <= 50) return;
        s += `<rect x="${(X(q.t) - cw / 2).toFixed(1)}" y="${y1}" width="${wq.toFixed(1)}" height="${hs}" fill="none" style="stroke:var(--fg)" stroke-width="1.5" clip-path="${cr}"/>`; });
    }
    if (this.hl && V.strip) s += `<rect x="${(X(this.hl.t0) - cw / 2).toFixed(1)}" y="${Hp - bp + 3}" width="${((this.hl.t1 - this.hl.t0) / Q * cw).toFixed(1)}" height="16" fill="none" style="stroke:var(--fg)" stroke-width="2" rx="2" clip-path="${cr}"/>`;
    // alert badges: one per run of consecutive flagged quarters, centred above the strip (hover or tap for details)
    this.blocks = [];
    if (V.strip) {
      const fl = (e) => e.hi >= o.th.hi || e.lo >= o.th.lo; let cur = null;
      c.e.forEach((e, i) => { if (fl(e)) { if (cur && e.t - cur.t1 <= Q * 1.01) { cur.t1 = e.t; } else { cur = { t0: e.t, t1: e.t, hi: 0, lo: 0, hiT: e.t, loT: e.t }; this.blocks.push(cur); }
        if (e.hi > cur.hi) { cur.hi = e.hi; cur.hiT = e.t; } if (e.lo > cur.lo) { cur.lo = e.lo; cur.loT = e.t; } } else cur = null; });
      this.blocks.forEach((bk, i) => { const x = (X(bk.t0) + X(bk.t1)) / 2, y = Hp - bp - 12, jump = bk.hi - o.th.hi >= bk.lo - o.th.lo;
        s += `<g data-blk="${i}" style="cursor:help"><circle cx="${x.toFixed(1)}" cy="${y}" r="11" fill="transparent"/><circle cx="${x.toFixed(1)}" cy="${y}" r="8" style="fill:var(${jump ? "--up" : "--down"});stroke:var(--card)" stroke-width="1.5"/><text x="${x.toFixed(1)}" y="${y + 4}" text-anchor="middle" fill="#fff" font-size="12" font-weight="800" pointer-events="none">!</text></g>`; });
    }
    // side labels next to the chip
    s += `<text x="${G.xNow - 46}" y="${tp + 6}" text-anchor="end" style="fill:var(--mute)" font-size="11" font-weight="700" letter-spacing=".08em">◀ PAST</text><text x="${G.xNow + 46}" y="${tp + 6}" style="fill:var(--mute)" font-size="11" font-weight="700" letter-spacing=".08em">FORECAST ▶</text>`;
    // the forecast-time line, handle and chip
    s += `<line x1="${G.xNow}" x2="${G.xNow}" y1="${tp}" y2="${yb}" style="stroke:var(--fg)" stroke-width="1.6"/><g style="cursor:ew-resize"><rect x="${G.xNow - 36}" y="${tp - 8}" width="72" height="20" rx="10" style="fill:var(--fg)"/><text x="${G.xNow}" y="${tp + 6}" text-anchor="middle" style="fill:var(--bg)" font-size="11" font-weight="700">${nowLabel}</text></g>`;
    if (this.hover) { const x = this.hover.x; s += `<line x1="${x}" x2="${x}" y1="${tp}" y2="${yb}" class="xh"/>`; }
    o.price.style.minHeight = Hp + "px";   // keep the layout height while the SVG is replaced, so the page never shifts
    o.price.innerHTML = s + "</svg>";
    return unsettled;
  }

  // ---------- interaction ----------
  tipAt(x, ev) {
    if (!this.last || !this.track) return;
    const { c, G } = this.last, T = c.T, tr = this.track;
    const t = Math.round((T + (x - G.xNow) / G.pw * G.total - Q / 2 - tr.t0) / Q) * Q + tr.t0, k = Math.round((t - tr.t0) / Q);
    if (k < 0 || k >= tr.n) return this.tip.hide();
    const v = (a) => (fin(a) ? fmt.eur(a) : "–");
    let h = `<b>${fmt.hm(t)}</b> · spot ${v(tr.spot[k])}`;
    if (t < T) {
      const dn = tr.dir[k], rg = fin(dn) && dn !== 0 ? (dn > 0 ? "up" : "down") : "–";
      h += `<br>Actual ${v(tr.actual[k])}${t + Q > this.cut ? " (not yet known)" : ""} · regulation ${rg}<br>Latest forecast ${v(tr.f50[k])} (${v(tr.f10[k])} to ${v(tr.f90[k])})`;
    } else { const e = c.e.find((q) => q.t === t); if (!e) return this.tip.hide();
      h += `<br>Forecast ${fmt.eur(e.p50)} (${fmt.eur(e.p10)} to ${fmt.eur(e.p90)})<br>Spike: jump ${fmt.pct(e.hi)} · drop ${fmt.pct(e.lo)} · up ${fmt.pct(e.pu)}${this.reveal ? "<br>Actual " + v(tr.actual[k]) : ""}`;
    }
    this.tip.show(h, ev);
  }
  blockTip(i, ev) {
    const bk = this.blocks[i]; if (!bk) return;
    const end = bk.t1 + Q, near = this.alerts.filter((a) => +new Date(a.window.start) < end && +new Date(a.window.end) > bk.t0).slice(0, 3);
    let h = `<b>Spike alert ${fmt.hm(bk.t0)}–${fmt.hm(end)}</b> (${(() => { const m = Math.round((end - bk.t0) / 6e4), h = Math.floor(m / 60); return h ? h + " h" + (m % 60 ? " " + (m % 60) + " min" : "") : m + " min"; })()})<br>Jump up to ${fmt.pct(bk.hi)} at ${fmt.hm(bk.hiT)} (alert level ${fmt.pct(this.o.th.hi)})<br>Drop up to ${fmt.pct(bk.lo)} at ${fmt.hm(bk.loT)} (alert level ${fmt.pct(this.o.th.lo)})`;
    if (near.length) h += "<br>" + near.map((a) => "• " + esc(a.title)).join("<br>");
    this.tip.show(h, ev);
  }
  bind(host) {
    const o = this.o; let drag = null;
    host.style.cursor = "ew-resize";
    const rel = (e) => e.clientX - host.getBoundingClientRect().left;
    host.addEventListener("pointerdown", (e) => { drag = { x0: e.clientX, i0: o.idx(), moved: false, xr: rel(e), tg: e.target }; try { host.setPointerCapture(e.pointerId); } catch {} this.tip.hide(); });
    host.addEventListener("pointermove", (e) => {
      if (drag) {
        const dx = e.clientX - drag.x0; if (Math.abs(dx) > 4) drag.moved = true;
        if (drag.moved && this.last) { const i = drag.i0 - Math.round(dx / this.last.G.pxHr); if (i !== o.idx()) o.onScrub(i); }
        return;
      }
      const g = e.target.closest && e.target.closest("[data-blk]");
      this.hover = { x: rel(e) };
      if (g) this.blockTip(+g.dataset.blk, e); else this.tipAt(this.hover.x, e);
      this.kick();
    });
    const end = (e) => {
      if (!drag) return;
      const d = drag; drag = null;
      const gb = d.tg && d.tg.closest && d.tg.closest("[data-blk]");
      if (!d.moved && gb) { this.blockTip(+gb.dataset.blk, e); return; }
      if (!d.moved && this.last) {
        const { c, G } = this.last, t = c.T + (d.xr - G.xNow) / G.pw * G.total;
        if (d.xr > G.xNow) { const en = c.e.find((x) => t >= x.t && t < x.t + Q); if (en) o.onPick(this.pick === en.t ? null : en.t); }
        else if (G.xNow - d.xr > 20) o.onScrub(d.i0 + Math.round((t - c.T) / HR));
      }
    };
    host.addEventListener("wheel", (e) => { if (!(e.ctrlKey || e.metaKey)) return; e.preventDefault(); this.zoomBy(Math.exp(-e.deltaY * (e.deltaMode ? 0.05 : 0.0025))); }, { passive: false });  // Ctrl/Cmd + wheel, or trackpad pinch
    host.addEventListener("pointerup", end); host.addEventListener("pointercancel", () => { drag = null; });
    host.addEventListener("pointerleave", () => { if (!drag) { this.hover = null; this.tip.hide(); this.kick(); } });
  }
}
