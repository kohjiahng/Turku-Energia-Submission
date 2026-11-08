#!/usr/bin/env python
"""Forecast API used by the Radar dashboard (meta, timeline, forecast, alerts). Served by serve.py."""
import gzip
import json
import os
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import adapters  # noqa: E402
import alerts as alerts_mod  # noqa: E402
import i18n  # noqa: E402
from drivers import Glossary  # noqa: E402


def _json_default(o):
    """numpy / pandas scalars that slip into API payloads (e.g. alert evidence) must not crash the response."""
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, np.ndarray):
        return [_json_default(x) if isinstance(x, np.generic) else x for x in o.tolist()]
    if isinstance(o, pd.Timestamp):
        return (o.tz_localize("UTC") if o.tzinfo is None else o.tz_convert("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")
    raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")

ISO = "%Y-%m-%dT%H:%M:%SZ"


class ApiError(Exception):
    def __init__(self, status, msg):
        super().__init__(msg)
        self.status, self.msg = status, msg


def iso(ts):
    return None if ts is None or pd.isna(ts) else pd.Timestamp(ts).strftime(ISO)


def num(x, nd=3):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return round(x, nd) if np.isfinite(x) else None


def parse_ts(s, what="time"):
    try:
        ts = pd.Timestamp(s)
        return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
    except Exception:
        raise ApiError(400, "invalid %s: %r (use ISO 8601 UTC, e.g. 2026-10-05T14:00:00Z)" % (what, s))


class App:
    def __init__(self, cfg):
        self.cfg = cfg
        self.glossary = Glossary(adapters.resolve(cfg, cfg.get("glossary_path", "drivers_glossary.json")))
        self.runs = {n: adapters.Run(n, cfg) for n in cfg["runs"]}
        self.tl = {}          # run -> (version, timeline dict)
        self.alert_cache = {}  # run -> {origin_idx: alerts}
        self.build_locks = {}
        self.lock = threading.RLock()
        self._last_check = 0
        self.load_errors = {}

    # ---------------------------------------------------------------- state
    def reload(self, force=False):
        out = {}
        for n, r in self.runs.items():
            try:
                r.ensure(force=force)
                self.load_errors.pop(n, None)
                out[n] = {"ok": True, "version": r.version}
            except Exception as e:
                self.load_errors[n] = str(e)
                out[n] = {"ok": False, "error": str(e)}
        return out

    def run(self, name):
        if name not in self.runs:
            raise ApiError(404, "unknown run %r; available: %s" % (name, ", ".join(self.runs)))
        r = self.runs[name]
        now = time.time()
        if now - self._last_check > 1.0 or r.df is None:   # mtime stat at most 1/s
            self._last_check = now
            try:
                r.ensure()
                self.load_errors.pop(name, None)
            except Exception as e:
                self.load_errors[name] = str(e)
                if r.df is None:
                    raise ApiError(503, "run %r not loadable: %s" % (name, e))
        return r

    def origin_idx(self, r, origin):
        if origin in (None, "", "latest"):
            return len(r.origins) - 1
        i = r.origin_index(parse_ts(origin, "origin"))
        if i is None:
            raise ApiError(404, "no forecast origin at or before %s (first origin %s)" % (origin, iso(r.origin_ts(0))))
        return i

    def ctx(self, r, i):
        return {"glossary": self.glossary, "typical_width": r.typical_width(i), "width_q": r.width_q}

    def alerts_for(self, r, i):
        with self.lock:
            cache = self.alert_cache.setdefault(r.name, {"v": -1, "d": {}})
            if cache["v"] != r.version:
                cache["v"], cache["d"] = r.version, {}
            if i in cache["d"]:
                return cache["d"][i]
        al = alerts_mod.evaluate(r.frame(i), r.prev_frame(i), self.cfg["alerts"], self.ctx(r, i))
        with self.lock:
            cache["d"][i] = al
        return al

    # ---------------------------------------------------------------- timeline
    def timeline(self, r):
        with self.lock:
            hit = self.tl.get(r.name)
            if hit and hit[0] == r.version:
                return hit[1]
            bl = self.build_locks.setdefault(r.name, threading.Lock())
        with bl:   # one builder per run; other requests (forecast/alerts) are not blocked
            with self.lock:
                hit = self.tl.get(r.name)
                if hit and hit[0] == r.version:
                    return hit[1]
            df, st = r.df, r.starts
            ph = np.maximum.reduceat(df["p_spike_high"].to_numpy(), st)
            pl = np.maximum.reduceat(df["p_spike_low"].to_numpy(), st)
            pu = np.add.reduceat(df["p_up"].to_numpy(), st) / (r.ends - st)
            sev, na, score = [], [], []
            for i in range(len(r.origins)):
                al = self.alerts_for(r, i)
                sev.append(alerts_mod.max_severity(al))
                na.append(len([a for a in al if a["rule"] != "error" and a["severity"] != "info"]))
                score.append(sum({"info": 0, "watch": 1, "act": 3}[a["severity"]] for a in al if a["rule"] != "error"))
            rev = np.zeros(len(r.origins))
            tl = {"times": [iso(pd.Timestamp(o).tz_localize("UTC")) for o in r.origins],
                  "max_p_spike_high": np.round(ph, 3).tolist(), "max_p_spike_low": np.round(pl, 3).tolist(),
                  "mean_p_up": np.round(pu, 3).tolist(), "max_severity": sev, "n_alerts": na,
                  "mean_width": np.round(r.mean_width, 1).tolist(), "score": score}
            with self.lock:
                self.tl[r.name] = (r.version, tl)
            return tl

    def demo_picks(self, r):
        tl = self.timeline(r)
        times = tl["times"]
        if not times:
            return []
        sc = np.array(tl["score"], float)
        ph = np.array(tl["max_p_spike_high"])
        picks = []
        # spikiest day (local): day with the highest sum of top-3 origin scores; open at its best origin
        ts = pd.to_datetime(times, utc=True)
        day = ts.tz_convert(self.cfg.get("timezone", "Europe/Helsinki")).floor("D")
        s = pd.DataFrame({"day": day, "sc": sc, "ph": ph, "i": range(len(times))})
        dsum = s.groupby("day")["sc"].sum()
        if dsum.max() > 0:
            d = dsum.idxmax()
            x = s[s.day == d]
            i = int(x.loc[x.sc.idxmax(), "i"])
            picks.append({"id": "spikiest_day", "label": "Spikiest day", "origin": times[i],
                          "why": "Busiest alert day of the run (%s local)" % d.strftime("%d %b")})
        i = int(np.argmax(ph))
        picks.append({"id": "max_spike", "label": "Highest spike risk", "origin": times[i],
                      "why": "Peak spike probability %d%%" % round(ph[i] * 100)})
        w = np.array(tl["mean_width"])
        i = int(np.argmax(w))
        picks.append({"id": "max_uncertainty", "label": "Most uncertain hour", "origin": times[i],
                      "why": "Widest P10-P90 band"})
        q = np.flatnonzero(np.array(tl["max_severity"]) < 0)
        if q.size:
            i = int(q[len(q) // 2])
            picks.append({"id": "quiet", "label": "A quiet moment", "origin": times[i], "why": "Nothing needs attention"})
        return picks

    # ---------------------------------------------------------------- endpoints
    def api_meta(self, q):
        self.reload() if any(r.df is None for r in self.runs.values()) else None
        runs = []
        act = adapters.load_actuals(self.cfg)
        for n, r in self.runs.items():
            try:
                self.run(n)
            except ApiError:
                pass
            ok = r.df is not None
            e = {"name": n, "label": r.spec.get("label", n), "path": os.path.relpath(r.path, os.path.dirname(HERE)),
                 "ok": ok, "error": self.load_errors.get(n)}
            if ok:
                mt = os.path.getmtime(r.path)
                e.update(first_origin=iso(r.origin_ts(0)), last_origin=iso(r.origin_ts(len(r.origins) - 1)),
                         n_origins=int(len(r.origins)), version=r.version, mtime=iso(pd.Timestamp(mt, unit="s", tz="UTC")),
                         columns_extra=[c for c in r.df.columns if c not in adapters.REQUIRED + adapters.OPTIONAL
                                        + ["imbalance", "spot", "direction", "wind_fc", "cons_fc", "solar_fc", "persist",
                                           "cutoff_eff", "horizon_h", "deliv_end"]],
                         has_cutoff=bool(r.df["data_cutoff_utc"].notna().any()),
                         has_drivers=bool((r.df["top_drivers"] != "").any()))
            runs.append(e)
        last_real = act["imbalance"].dropna().index.max() if len(act) else None
        return {"schema_version": adapters.SCHEMA_VERSION, "runs": runs, "timezone": self.cfg.get("timezone", "Europe/Helsinki"),
                "actuals": {"first": iso(act.index.min()) if len(act) else None, "last": iso(last_real)},
                "alerts_config": self.cfg["alerts"], "spike_threshold_eur": self.cfg.get("spike_threshold_eur", 50),
                "hindsight_config": self.cfg.get("hindsight", {}),
                "server_time": iso(pd.Timestamp.utcnow())}

    def api_forecast(self, q):
        r = self.run(q.get("run", [next(iter(self.runs))])[0])
        i = self.origin_idx(r, q.get("origin", ["latest"])[0])
        cur = r.frame(i)
        prev = r.prev_frame(i)
        origin = r.origin_ts(i)
        cut = cur["cutoff_eff"].iloc[0]
        pmap = {}
        if prev is not None:
            pmap = dict(zip(prev["delivery_start_utc"], zip(prev["p10"], prev["p50"], prev["p90"])))
        rows = []
        for t in cur.itertuples(index=False):
            pv = pmap.get(t.delivery_start_utc)
            rows.append({"t": iso(t.delivery_start_utc), "step": int(t.step), "p10": num(t.p10, 2), "p50": num(t.p50, 2),
                         "p90": num(t.p90, 2), "p_up": num(t.p_up), "p_down": num(t.p_down), "p_none": num(t.p_none),
                         "p_spike_high": num(t.p_spike_high), "p_spike_low": num(t.p_spike_low),
                         "spot": num(t.spot, 2), "imbalance": num(t.imbalance, 2),
                         "direction": None if not np.isfinite(t.direction) else int(t.direction),
                         "wind_fc": num(t.wind_fc, 0), "cons_fc": num(t.cons_fc, 0), "solar_fc": num(t.solar_fc, 0),
                         "prev_p10": num(pv[0], 2) if pv else None, "prev_p50": num(pv[1], 2) if pv else None,
                         "prev_p90": num(pv[2], 2) if pv else None, "drivers": [x for x in t.top_drivers.split(";") if x]})
        # history: 3 h before the origin (realised only up to the data cutoff)
        act = adapters.load_actuals(self.cfg)
        h0 = origin - pd.Timedelta(hours=3)
        hist = act.loc[h0:origin - pd.Timedelta(minutes=15)]
        history = [{"t": iso(ix), "imbalance": num(x.imbalance, 2) if ix + adapters.Q <= cut else None, "spot": num(x.spot, 2),
                    "direction": None if (ix + adapters.Q > cut or not np.isfinite(x.direction)) else int(x.direction),
                    "wind_fc": num(x.wind_fc, 0), "cons_fc": num(x.cons_fc, 0), "solar_fc": num(x.solar_fc, 0)}
                   for ix, x in hist.iterrows()]
        al = self.alerts_for(r, i)
        agg = {}
        for rw in rows:
            for n in rw["drivers"]:
                agg[n] = agg.get(n, 0) + 1
        drivers = self.glossary.describe_many(sorted(agg, key=lambda n: -agg[n]), 6)
        return {"run": r.name, "origin": iso(origin), "origin_index": i, "n_origins": int(len(r.origins)),
                "prev_origin": iso(origin - pd.Timedelta(hours=1)) if prev is not None else None,
                "data_cutoff": iso(cut), "data_age_hours": num((origin - cut).total_seconds() / 3600, 2),
                "data_cutoff_given": bool(cur["data_cutoff_utc"].notna().any()),
                "realised_until": iso(act["imbalance"].dropna().index.max() + adapters.Q) if len(act) else None,
                "rows": rows, "history": history, "drivers": drivers, "kpis": self.kpis(r, i, cur, prev, rows),
                "quiet": not any(a["severity"] != "info" and a["rule"] != "error" for a in al),
                "n_alerts": len(al)}

    def kpis(self, r, i, cur, prev, rows):
        ph, pl = cur["p_spike_high"].to_numpy(), cur["p_spike_low"].to_numpy()
        a = int(np.argmax(ph)); b = int(np.argmax(pl))
        side_hi = ph[a] >= pl[b]
        j = a if side_hi else b
        w = (cur["p90"] - cur["p10"]).to_numpy()
        tw = r.typical_width(i)
        pu, pdn = cur["p_up"].to_numpy(), cur["p_down"].to_numpy()

        def reg(n):
            u, d_ = float(pu[:n].mean()), float(pdn[:n].mean())
            return {"dir": "up" if u > d_ else "down", "p_up": num(u), "p_down": num(d_)}
        k = {"max_spike": {"side": "high" if side_hi else "low", "p": num(ph[a] if side_hi else pl[b]), "t": rows[j]["t"],
                           "p_high": num(ph[a]), "t_high": rows[a]["t"], "p_low": num(pl[b]), "t_low": rows[b]["t"]},
             "regulation": {"next_2h": reg(8), "next_10h": reg(40)},
             "p50_range": {"min": num(cur["p50"].min(), 1), "max": num(cur["p50"].max(), 1),
                           "t_max": rows[int(np.argmax(cur["p50"].to_numpy()))]["t"]},
             "width": {"mean": num(w.mean(), 1), "typical": num(tw, 1), "ratio": num(w.mean() / tw, 2) if tw else None},
             "revision": None}
        if prev is not None:
            ci, pi = alerts_mod._overlap(cur, prev)
            if len(ci):
                d = cur["p50"].to_numpy()[ci] - prev["p50"].to_numpy()[pi]
                m = int(np.argmax(np.abs(d)))
                k["revision"] = {"max_shift": num(d[m], 1), "t": rows[int(ci[m])]["t"], "mean_shift": num(d.mean(), 1)}
        return k

    def api_alerts(self, q):
        r = self.run(q.get("run", [next(iter(self.runs))])[0])
        i = self.origin_idx(r, q.get("origin", ["latest"])[0])
        al = self.alerts_for(r, i)
        lang = q.get("lang", ["en"])[0]
        al = [i18n.translate_alert(a, lang) for a in al]
        return {"run": r.name, "origin": iso(r.origin_ts(i)), "alerts": al, "quiet": not any(a["severity"] != "info" and a["rule"] != "error" for a in al),
                "disclaimer": alerts_mod.DISCLAIMER}

    def api_timeline(self, q):
        r = self.run(q.get("run", [next(iter(self.runs))])[0])
        tl = self.timeline(r)
        times = tl["times"]
        lo, hi = 0, len(times)
        if "start" in q:
            lo = int(np.searchsorted(np.array(times), iso(parse_ts(q["start"][0], "start")), side="left"))
        if "end" in q:
            hi = int(np.searchsorted(np.array(times), iso(parse_ts(q["end"][0], "end")), side="right"))
        out = {k: v[lo:hi] for k, v in tl.items()}
        out["run"] = r.name
        if q.get("demo"):
            out["demo"] = self.demo_picks(r)
        return out


def make_handler(app):
    routes = {"/api/meta": app.api_meta, "/api/forecast": app.api_forecast, "/api/alerts": app.api_alerts,
              "/api/timeline": app.api_timeline}

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *a):
            if os.environ.get("DASH_VERBOSE"):
                sys.stderr.write("%s\n" % (fmt % a))

        def _send(self, status, body, ctype, extra=None):
            squeeze = len(body) > 800 and "gzip" in self.headers.get("Accept-Encoding", "") and (
                ctype.startswith("text/") or "json" in ctype or "javascript" in ctype)
            if squeeze:
                body = gzip.compress(body, 5)
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Vary", "Accept-Encoding")
            if squeeze:
                self.send_header("Content-Encoding", "gzip")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status, obj):
            self._send(status, json.dumps(obj, separators=(",", ":"), allow_nan=False, default=_json_default).encode(), "application/json; charset=utf-8")

        def do_GET(self):
            u = urlparse(self.path)
            if u.path.startswith("/api/"):
                fn = routes.get(u.path)
                if not fn:
                    return self._json(404, {"error": "unknown endpoint %s" % u.path, "status": 404})
                try:
                    return self._json(200, fn(parse_qs(u.query)))
                except ApiError as e:
                    return self._json(e.status, {"error": e.msg, "status": e.status})
                except Exception as e:
                    traceback.print_exc()
                    return self._json(500, {"error": "internal error: %s" % e, "status": 500})
            return self._json(404, {"error": "not found: %s" % u.path, "status": 404})
    return H
