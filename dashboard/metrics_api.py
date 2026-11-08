"""/api/metrics: scorer-compatible quality metrics for a run (display only; nothing is fed back).

Definitions follow starter_package_v1/score.py (MAE of p50, pinball of p10/p90, coverage, spike Brier, 3-class direction Brier),
plus extras: bias, RMSE, naive baselines (spot as forecast, persistence), skill, interval width and one-sided miss rates,
spike recall/precision, direction accuracy, and breakdowns by forecast horizon, by local day and reliability curves.
Optional query: start, end (UTC ISO, delivery_start_utc in [start, end)) to restrict the scope, e.g. to one local day.
"""
import os
from pathlib import Path

import numpy as np
import pandas as pd

import adapters
import server


def _r(x, nd=3):
    return server.num(x, nd)


def _reliability(p, y, bins):
    """Fixed-width probability bins: predicted mean vs observed frequency (empty bins skipped)."""
    edges = np.linspace(0, 1, bins + 1)
    ix = np.clip(np.digitize(p, edges[1:-1]), 0, bins - 1)
    out = []
    for b in range(bins):
        m = ix == b
        n = int(m.sum())
        if n:
            out.append({"bin": b, "p_mean": _r(p[m].mean(), 4), "obs_freq": _r(y[m].mean(), 4), "n": n})
    return out


def _pinball(y, yhat, q):
    d = y - yhat
    return float(np.mean(np.maximum(q * d, (q - 1) * d)))


def _score_limit(r, cfg):
    path = cfg["actuals"].get("actuals_csv")
    if r.name != "validation" or not path:
        return None
    v = pd.read_csv(adapters.resolve(cfg, path), usecols=["delivery_start_utc"])
    return pd.to_datetime(v["delivery_start_utc"], utc=True).max()


BASELINE_CSV = Path(__file__).resolve().parents[2] / "starter_package_v1" / "baseline_output" / "validation" / "forecast_backtest.csv"


def _baseline(r, d, spike_eur, cfg):
    """Official organiser baseline scored on exactly the rows in d (validation run only; None otherwise)."""
    data_dir = cfg.get("metrics_data_dir")
    path = Path(data_dir) / "baseline_validation.csv" if data_dir else BASELINE_CSV
    if r.name != "validation" or not path.exists():
        return None
    b = pd.read_csv(path, usecols=["forecast_origin_utc", "delivery_start_utc", "p10", "p50", "p90", "p_up", "p_down", "p_none", "p_spike_high", "p_spike_low"])
    for c in ("forecast_origin_utc", "delivery_start_utc"):
        b[c] = pd.to_datetime(b[c], utc=True)
    keys = d[["forecast_origin_utc", "delivery_start_utc", "imbalance", "spot", "direction"]].copy()
    for c in ("forecast_origin_utc", "delivery_start_utc"):
        keys[c] = pd.to_datetime(keys[c], utc=True)
    m = keys.merge(b, on=["forecast_origin_utc", "delivery_start_utc"], how="inner")
    if len(m) < 20:
        return None
    y, sp = m["imbalance"].to_numpy(), m["spot"].to_numpy()
    p10, p50, p90 = (m[c].to_numpy() for c in ("p10", "p50", "p90"))
    hi, lo = (y > sp + spike_eur).astype(float), (y < sp - spike_eur).astype(float)
    out = {"mae_p50": _r(np.abs(p50 - y).mean(), 2),
           "pinball_mean": _r((_pinball(y, p10, 0.1) + _pinball(y, p90, 0.9)) / 2, 3),
           "coverage": _r(((y >= p10) & (y <= p90)).mean(), 3),
           "brier_spike_mean": _r((np.mean((m["p_spike_high"].to_numpy() - hi) ** 2) + np.mean((m["p_spike_low"].to_numpy() - lo) ** 2)) / 2, 4),
           "width_mean": _r((p90 - p10).mean(), 1), "rmse_p50": _r(np.sqrt(((p50 - y) ** 2).mean()), 2), "bias_p50": _r((p50 - y).mean(), 2)}
    dn = m[m["direction"].notna()]
    if len(dn):
        yu, yd, yn = ((dn["direction"] == k).astype(float).to_numpy() for k in (1, -1, 0))
        out["brier_direction"] = _r(np.mean((dn["p_up"].to_numpy() - yu) ** 2 + (dn["p_down"].to_numpy() - yd) ** 2 + (dn["p_none"].to_numpy() - yn) ** 2), 4)
    return out


def compute(r, cfg, start=None, end=None):
    df = r.df
    d = df[df["imbalance"].notna() & df["spot"].notna()]
    lim = _score_limit(r, cfg)
    if lim is not None:   # like the official scorer: the validation run is scored only where actuals_validation.csv has outcomes
        d = d[d["delivery_start_utc"] <= lim]
    if start is not None:
        d = d[d["delivery_start_utc"] >= start]
    if end is not None:
        d = d[d["delivery_start_utc"] < end]
    out = {"run": r.name, "n_rows": int(len(d)), "n_quarters": int(d["delivery_start_utc"].nunique()) if len(d) else 0,
           "n_origins": int(d["forecast_origin_utc"].nunique()) if len(d) else 0}
    if len(d) < 20:
        out["empty"] = True
        return out
    spike_eur = cfg.get("spike_threshold_eur", 50)
    thr = cfg.get("hindsight", {}).get("spike_call_threshold", 0.3)
    y, spot, p10, p50, p90 = (d[c].to_numpy() for c in ("imbalance", "spot", "p10", "p50", "p90"))
    err = p50 - y
    pers = d["persist"].to_numpy(); okp = np.isfinite(pers)
    hi = (y > spot + spike_eur).astype(float); lo = (y < spot - spike_eur).astype(float)
    ph, pl = d["p_spike_high"].to_numpy(), d["p_spike_low"].to_numpy()
    b_hi, b_lo = float(np.mean((ph - hi) ** 2)), float(np.mean((pl - lo) ** 2))
    cl_hi, cl_lo = float(hi.mean() * (1 - hi.mean())), float(lo.mean() * (1 - lo.mean()))
    h = {
        "mae_p50": _r(np.abs(err).mean(), 2), "rmse_p50": _r(np.sqrt((err ** 2).mean()), 2), "bias_p50": _r(err.mean(), 2),
        "median_abs_err": _r(np.median(np.abs(err)), 2),
        "mae_spot": _r(np.abs(spot - y).mean(), 2), "mae_persistence": _r(np.abs(pers[okp] - y[okp]).mean(), 2) if okp.any() else None,
        "skill_vs_spot": _r(1 - np.abs(err).mean() / np.abs(spot - y).mean(), 3),
        "pinball_p10": _r(_pinball(y, p10, 0.1), 3), "pinball_p90": _r(_pinball(y, p90, 0.9), 3),
        "pinball_mean": _r((_pinball(y, p10, 0.1) + _pinball(y, p90, 0.9)) / 2, 3),
        "coverage": _r(((y >= p10) & (y <= p90)).mean(), 3), "miss_below": _r((y < p10).mean(), 3), "miss_above": _r((y > p90).mean(), 3),
        "width_mean": _r((p90 - p10).mean(), 1),
        "spike_high_rate": _r(hi.mean(), 3), "spike_low_rate": _r(lo.mean(), 3),
        "brier_high": _r(b_hi, 4), "brier_low": _r(b_lo, 4), "brier_spike_mean": _r((b_hi + b_lo) / 2, 4),
        "brier_high_climatology": _r(cl_hi, 4), "brier_low_climatology": _r(cl_lo, 4),
        "recall_high": _r(((ph >= thr) & (hi == 1)).sum() / max(hi.sum(), 1), 3), "precision_high": _r(((ph >= thr) & (hi == 1)).sum() / max((ph >= thr).sum(), 1), 3),
        "recall_low": _r(((pl >= thr) & (lo == 1)).sum() / max(lo.sum(), 1), 3), "precision_low": _r(((pl >= thr) & (lo == 1)).sum() / max((pl >= thr).sum(), 1), 3),
        "spike_call_threshold": thr,
    }
    dn = d[d["direction"].notna()]
    if len(dn):
        yu, yd, yn = ((dn["direction"] == k).astype(float).to_numpy() for k in (1, -1, 0))
        pu, pdn, pn = dn["p_up"].to_numpy(), dn["p_down"].to_numpy(), dn["p_none"].to_numpy()
        fr = np.array([yu.mean(), yd.mean(), yn.mean()])
        h["brier_direction"] = _r(np.mean((pu - yu) ** 2 + (pdn - yd) ** 2 + (pn - yn) ** 2), 4)
        h["brier_direction_climatology"] = _r(np.mean((fr[0] - yu) ** 2 + (fr[1] - yd) ** 2 + (fr[2] - yn) ** 2), 4)
        m = (yu + yd) > 0
        h["direction_accuracy"] = _r(((pu[m] >= pdn[m]) == (yu[m] == 1)).mean(), 3)
        h["up_share"] = _r(yu.mean(), 3)
    h["baseline"] = _baseline(r, d, spike_eur, cfg)
    out["headline"] = h

    def grp(frame):
        yy, ss, a, b, c = (frame[k].to_numpy() for k in ("imbalance", "spot", "p50", "p10", "p90"))
        sh = (yy > ss + spike_eur).astype(float)
        return {"n": int(len(frame)), "mae": _r(np.abs(a - yy).mean(), 2), "mae_spot": _r(np.abs(ss - yy).mean(), 2),
                "coverage": _r(((yy >= b) & (yy <= c)).mean(), 3), "width": _r((c - b).mean(), 1), "bias": _r((a - yy).mean(), 2),
                "brier_high": _r(((frame["p_spike_high"].to_numpy() - sh) ** 2).mean(), 4), "spikes_high": int(sh.sum())}
    out["by_horizon"] = [dict(hour=int(k), **grp(g)) for k, g in d.groupby("horizon_h")]
    tz = cfg.get("timezone", "Europe/Helsinki")
    day = d["delivery_start_utc"].dt.tz_convert(tz).dt.strftime("%Y-%m-%d")
    out["by_day"] = []
    for k, g in d.groupby(day):
        first = g["delivery_start_utc"].min()
        out["by_day"].append(dict(day=k, t=server.iso(first), **grp(g)))
    pp = np.concatenate([ph, pl]); yy2 = np.concatenate([hi, lo])
    out["reliability"] = {"spike": _reliability(pp, yy2, 10),
                          "p_up": _reliability(dn["p_up"].to_numpy(), (dn["direction"] == 1).astype(float).to_numpy(), 10) if len(dn) else []}
    return out


_cache = {}


def route(app):
    def api_metrics(q):
        r = app.run(q.get("run", [next(iter(app.runs))])[0])
        start = server.parse_ts(q["start"][0], "start") if q.get("start") else None
        end = server.parse_ts(q["end"][0], "end") if q.get("end") else None
        key = (r.name, r.version, start, end)
        if key not in _cache:
            if len(_cache) > 64:
                _cache.clear()
            _cache[key] = compute(r, app.cfg, start, end)
        return _cache[key]
    return api_metrics


# ---------------------------------------------------------------- how much each factor affected the forecasts
HERE = os.path.dirname(os.path.abspath(__file__))
GROUPS = [
    ("spot", "Spot price and its shape", lambda f: f.startswith("spot")),
    ("history", "Recent imbalance behaviour", lambda f: f.startswith(("last_", "sp_", "up_share", "hi_share", "lo_share"))),
    ("dayahead", "Wind, solar and load forecasts", lambda f: f.startswith(("wind_", "solar_", "cons_fc", "res_load"))),
    ("realtime", "Real-time grid signals", lambda f: f.startswith("rt_")),
    ("neighbours", "Neighbouring areas and flows", lambda f: f.startswith(("se1_", "se3_", "fi_ee", "ee_fi", "cons_err"))),
    ("calendar", "Calendar and timing", lambda f: f in ("hour", "weekday", "holiday", "dst", "o_hour", "o_weekday", "step", "gap_q")),
]


def group_of(f):
    for key, _, test in GROUPS:
        if test(f):
            return key
    return "other"


_shares = {}


def _load_shares(run, data_dir):
    path = os.path.join(data_dir, "driver_shares_%s.parquet" % run)
    if not os.path.exists(path):
        return None
    mt = os.path.getmtime(path)
    if _shares.get(path, (None,))[0] != mt:
        d = pd.read_parquet(path)
        d["forecast_origin_utc"] = pd.to_datetime(d["forecast_origin_utc"], utc=True)
        _shares[path] = (mt, d)
    return _shares[path][1]


def driver_shares(app, run, start=None, end=None):
    d = _load_shares(run, app.cfg.get("metrics_data_dir", os.path.join(HERE, "data")))
    if d is None:
        return {"run": run, "available": False}
    if start is not None:
        d = d[d["forecast_origin_utc"] >= start]
    if end is not None:
        d = d[d["forecast_origin_utc"] < end]
    n = int(d["forecast_origin_utc"].nunique())
    if n == 0:
        return {"run": run, "available": False}
    per = d.groupby("feature")[["near", "far"]].sum() / n            # mean share over origins; near = steps 1-8, far = steps 9-40
    per["all"] = (8 * per["near"] + 32 * per["far"]) / 40
    per = per[per["all"] > 0]
    tot = per[["all", "near", "far"]].sum()
    per["group"] = [group_of(f) for f in per.index]
    grp = per.groupby("group")[["all", "near", "far"]].sum()
    labels = {k: lab for k, lab, _ in GROUPS}
    groups = [{"key": k, "label": labels.get(k, "Other"), "share": _r(r["all"] / tot["all"], 4), "near": _r(r["near"] / tot["near"], 4), "far": _r(r["far"] / tot["far"], 4)}
              for k, r in grp.sort_values("all", ascending=False).iterrows()]
    top = per.sort_values("all", ascending=False).head(15)
    desc = {x["name"]: x for x in app.glossary.describe_many(list(top.index), len(top))}
    feats = [{"name": f, "label": desc.get(f, {}).get("label", f), "explain": desc.get(f, {}).get("explain", ""), "group": r["group"],
              "share": _r(r["all"] / tot["all"], 4), "near": _r(r["near"] / tot["near"], 4), "far": _r(r["far"] / tot["far"], 4)} for f, r in top.iterrows()]
    return {"run": run, "available": True, "n_origins": n, "groups": groups, "features": feats}


def route_shares(app):
    def api_driver_shares(q):
        r = app.run(q.get("run", [next(iter(app.runs))])[0])
        start = server.parse_ts(q["start"][0], "start") if q.get("start") else None
        end = server.parse_ts(q["end"][0], "end") if q.get("end") else None
        return driver_shares(app, r.name, start, end)
    return api_driver_shares
