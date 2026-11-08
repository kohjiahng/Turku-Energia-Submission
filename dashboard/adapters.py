"""Loading / validation of forecast CSVs (schema 1.0) and realised data into canonical frames.

Everything is cached by file mtime. The rest of the dashboard only sees:
  Run      -> .df (forecast rows sorted by origin, step; extra cols: spot, imbalance, direction, ...)
  load_actuals -> DataFrame indexed by delivery_start_utc [imbalance, spot, direction, wind_fc, cons_fc, solar_fc]
"""
import json
import os
import threading

import numpy as np
import pandas as pd

REQUIRED = ["forecast_origin_utc", "delivery_start_utc", "step", "p10", "p50", "p90",
            "p_up", "p_down", "p_none", "p_spike_high", "p_spike_low"]
OPTIONAL = ["data_cutoff_utc", "top_drivers"]
NUMERIC = ["step", "p10", "p50", "p90", "p_up", "p_down", "p_none", "p_spike_high", "p_spike_low"]
SCHEMA_VERSION = "1.0"
Q = pd.Timedelta(minutes=15)


class ContractError(ValueError):
    pass


def load_config(path):
    path = os.path.abspath(path)
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    cfg["_base"] = os.path.dirname(path)
    return cfg


def resolve(cfg, p):
    return p if os.path.isabs(p) else os.path.normpath(os.path.join(cfg["_base"], p))


def _mtime(p):
    try:
        return os.path.getmtime(p)
    except OSError:
        return None


def parse_forecast_csv(path):
    """Read + validate a schema-1.0 forecast CSV. Unknown columns are kept and ignored; optional ones may be absent."""
    df = pd.read_csv(path)
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise ContractError("forecast file %s misses required columns: %s" % (os.path.basename(path), ", ".join(missing)))
    for c in ("forecast_origin_utc", "delivery_start_utc"):
        df[c] = pd.to_datetime(df[c], utc=True, errors="coerce")
    if "data_cutoff_utc" in df.columns:
        df["data_cutoff_utc"] = pd.to_datetime(df["data_cutoff_utc"], utc=True, errors="coerce")
    else:
        df["data_cutoff_utc"] = pd.NaT
    if "top_drivers" not in df.columns:
        df["top_drivers"] = ""
    df["top_drivers"] = df["top_drivers"].fillna("").astype(str)
    for c in NUMERIC:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["forecast_origin_utc", "delivery_start_utc", "p50"]).copy()
    df["step"] = df["step"].fillna(0).astype(int)
    df = df.sort_values(["forecast_origin_utc", "step"], kind="stable").reset_index(drop=True)
    # repair mild monotonicity violations instead of crashing
    df["p10"] = np.minimum(df["p10"].fillna(df["p50"]), df["p50"])
    df["p90"] = np.maximum(df["p90"].fillna(df["p50"]), df["p50"])
    for c in ("p_up", "p_down", "p_none", "p_spike_high", "p_spike_low"):
        df[c] = df[c].fillna(0.0).clip(0, 1)
    return df


_actuals_cache = {}
_lock = threading.Lock()


def load_actuals(cfg):
    """Canonical realised frame indexed by delivery_start_utc (UTC)."""
    a = cfg["actuals"]
    paths = [resolve(cfg, a[k]) for k in ("inputs_parquet", "spot_csv", "actuals_csv") if a.get(k)]
    key = tuple((p, _mtime(p)) for p in paths)
    with _lock:
        hit = _actuals_cache.get("v")
        if hit and hit[0] == key:
            return hit[1]
    ser = a["series"]
    cols = {}
    long_parts = []
    if a.get("inputs_parquet") and os.path.exists(resolve(cfg, a["inputs_parquet"])):
        want = [v for k, v in ser.items() if k != "spot"]
        d = pd.read_parquet(resolve(cfg, a["inputs_parquet"]), columns=["series_id", "timestamp_utc", "value"],
                            filters=[("series_id", "in", want)])
        long_parts.append(d)
    if a.get("spot_csv") and os.path.exists(resolve(cfg, a["spot_csv"])):
        s = pd.read_csv(resolve(cfg, a["spot_csv"]), usecols=["series_id", "timestamp_utc", "value"])
        s["timestamp_utc"] = pd.to_datetime(s["timestamp_utc"], utc=True)
        long_parts.append(s)
    if long_parts:
        d = pd.concat(long_parts, ignore_index=True)
        d["timestamp_utc"] = pd.to_datetime(d["timestamp_utc"], utc=True)
        w = d.pivot_table(index="timestamp_utc", columns="series_id", values="value", aggfunc="last")
        for k, sid in ser.items():
            if sid in w.columns:
                cols[k] = w[sid]
    out = pd.DataFrame(cols)
    for k in ("imbalance", "spot", "direction", "wind_fc", "cons_fc", "solar_fc"):
        if k not in out.columns:
            out[k] = np.nan
    # fallback / gap fill from actuals_validation.csv
    ap = resolve(cfg, a["actuals_csv"]) if a.get("actuals_csv") else None
    if ap and os.path.exists(ap):
        v = pd.read_csv(ap)
        v["delivery_start_utc"] = pd.to_datetime(v["delivery_start_utc"], utc=True)
        v = v.set_index("delivery_start_utc")
        vv = pd.DataFrame({"imbalance": v["imbalance_price"], "spot": v["spot_price"],
                           "direction": v["direction"].map({"up": 1, "down": -1, "none": 0})})
        out = out.combine_first(vv)
    out.index.name = "delivery_start_utc"
    out = out.sort_index()
    out = out[["imbalance", "spot", "direction", "wind_fc", "cons_fc", "solar_fc"]]
    with _lock:
        _actuals_cache["v"] = (key, out)
    return out


def cfg_q(cfg):
    return cfg.get("alerts", {}).get("uncertainty", {}).get("hist_quantile", 0.95)


class Run:
    """One named forecast run, joined with actuals; reloads when the file mtime changes."""

    def __init__(self, name, cfg):
        self.name = name
        self.cfg = cfg
        self.spec = cfg["runs"][name]
        self.path = resolve(cfg, self.spec["forecast_csv"])
        self._mt = None
        self._amt = None
        self.df = None
        self.error = None
        self.origins = np.array([], dtype="datetime64[ns]")
        self.starts = np.array([], dtype=int)
        self.ends = np.array([], dtype=int)
        self.version = 0

    def _actuals_stamp(self):
        a = self.cfg["actuals"]
        return tuple(_mtime(resolve(self.cfg, a[k])) for k in ("inputs_parquet", "spot_csv", "actuals_csv") if a.get(k))

    def ensure(self, force=False):
        mt, amt = _mtime(self.path), self._actuals_stamp()
        if not force and self.df is not None and mt == self._mt and amt == self._amt:
            return self
        if mt is None:
            self.df, self.error = None, "forecast file not found: %s" % self.path
            self._mt = mt
            raise ContractError(self.error)
        try:
            df = parse_forecast_csv(self.path)
            act = load_actuals(self.cfg)
            idx = act.index
            for c in ("imbalance", "spot", "direction", "wind_fc", "cons_fc", "solar_fc"):
                df[c] = act[c].reindex(df["delivery_start_utc"]).to_numpy()
            # persistence reference: latest realised price known at the data cutoff
            cutoff = df["data_cutoff_utc"].fillna(df["forecast_origin_utc"] - pd.Timedelta(hours=1))
            last_known = (cutoff - Q)
            df["persist"] = act["imbalance"].reindex(last_known).to_numpy()
            df["cutoff_eff"] = cutoff
            df["horizon_h"] = np.clip((df["step"].to_numpy() - 1) // 4 + 1, 1, 10)
            df["deliv_end"] = df["delivery_start_utc"] + Q
            o = df["forecast_origin_utc"].dt.tz_localize(None).to_numpy()
            u, first = np.unique(o, return_index=True)
            self.origins = u
            self.starts = first
            self.ends = np.append(first[1:], len(df))
            w = (df["p90"] - df["p10"]).to_numpy()
            self.mean_width = np.add.reduceat(w, first) / (self.ends - self.starts)
            self.width_q = float(np.quantile(w, cfg_q(self.cfg)))
            self.df = df
            self.error = None
            self.version += 1
        except Exception as e:  # keep serving old data if a regenerate is half-written
            self.error = str(e)
            if self.df is None:
                raise
        self._mt, self._amt = mt, amt
        return self

    def origin_index(self, ts):
        """Index of the latest origin <= ts (or None)."""
        t = np.datetime64(pd.Timestamp(ts).tz_convert("UTC").tz_localize(None), "ns")
        i = int(np.searchsorted(self.origins, t, side="right")) - 1
        return i if i >= 0 else None

    def frame(self, i):
        return self.df.iloc[self.starts[i]:self.ends[i]]

    def prev_frame(self, i):
        """Frame of the previous hourly origin (i-1) if it is exactly one hour earlier."""
        if i <= 0:
            return None
        if self.origins[i] - self.origins[i - 1] != np.timedelta64(1, "h"):
            return None
        return self.frame(i - 1)

    def origin_ts(self, i):
        return pd.Timestamp(self.origins[i]).tz_localize("UTC")

    def typical_width(self, i, n=168):
        """Median of per-origin mean widths over the previous n origins (None if no history)."""
        lo = max(0, i - n)
        if i - lo < 6:
            return None
        return float(np.median(self.mean_width[lo:i]))
