"""/api/track: one 15-minute track per run for the sliding-line chart.

Per delivery quarter: realised imbalance, spot and regulation direction (-1 down, 1 up; display only) and the "latest forecast" = the forecast issued in
the hour containing that quarter (steps 1-4 of the hourly origin), i.e. the freshest forecast that exists for it.
Arrays are aligned to t0 + k * 15 min; null where nothing exists.
"""
import numpy as np
import pandas as pd

import adapters
import server

Q = adapters.Q


def build(app, run_name, cache={}):
    r = app.run(run_name)
    key = (run_name, r.version, id(r.df))
    if key in cache:
        return cache[key]
    df = r.df
    near = df[df["step"] <= 4].sort_values(["delivery_start_utc", "forecast_origin_utc"])
    near = near.drop_duplicates("delivery_start_utc", keep="last").set_index("delivery_start_utc")
    # the forecast for a quarter must come from an origin at or before the quarter (never a later one)
    near = near[near["forecast_origin_utc"] <= near.index]
    act = adapters.load_actuals(app.cfg)
    first = df["delivery_start_utc"].min() - pd.Timedelta(hours=24)
    last = df["delivery_start_utc"].max()
    idx = pd.date_range(first, last, freq=Q, tz="UTC")

    def col(s):
        return [server.num(x, 2) for x in s.reindex(idx).to_numpy()]
    out = {"run": run_name, "t0": server.iso(idx[0]), "step_min": 15, "n": len(idx),
           "actual": col(act["imbalance"]), "spot": col(act["spot"]),
           "dir": [None if x is None else int(x) for x in col(act["direction"])],
           "f10": col(near["p10"]), "f50": col(near["p50"]), "f90": col(near["p90"])}
    cache.clear()
    cache[key] = out
    return out


def route(app):
    def api_track(q):
        name = q.get("run", [next(iter(app.runs))])[0]
        return build(app, name)
    return api_track
