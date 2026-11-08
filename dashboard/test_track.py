"""Tests for /api/track (run: .venv/bin/python -W ignore final_submission/dashboard/test_track.py)."""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import adapters, server, track_api

cfg = adapters.load_config(os.path.join(HERE, "config.json"))
app = server.App(cfg); app.reload()
for name in app.runs:
    tr = track_api.build(app, name)
    n = tr["n"]
    assert all(len(tr[k]) == n for k in ("actual", "spot", "dir", "f10", "f50", "f90")), "length mismatch"
    t0 = pd.Timestamp(tr["t0"]); idx = pd.date_range(t0, periods=n, freq="15min", tz="UTC")
    df = app.run(name).df
    # every quarter of an origin hour carries that origin's step 1-4 forecast and never a later origin's
    near = df[df["step"] <= 4]
    bad = 0
    f50 = pd.Series(tr["f50"], index=idx, dtype=float)
    for o in near["forecast_origin_utc"].drop_duplicates().iloc[::37]:
        rows = near[near["forecast_origin_utc"] == o]
        for d, p in zip(rows["delivery_start_utc"], rows["p50"]):
            if d in f50.index and abs(f50[d] - round(p, 2)) > 1e-6:
                bad += 1
    assert bad == 0, f"{bad} stitched values differ from the forecast of their own origin"
    first_origin = df["forecast_origin_utc"].min()
    assert f50[idx < first_origin].isna().all(), "forecast values before the first origin"
    assert f50[idx >= first_origin].notna().sum() >= 4 * df["forecast_origin_utc"].nunique() - 8
    assert (np.array(tr["f10"], dtype=float)[f50.notna().to_numpy()] <= np.array(tr["f90"], dtype=float)[f50.notna().to_numpy()]).all()
    assert set(x for x in tr["dir"] if x is not None) <= {-1, 0, 1}, "bad direction values"
    print(name, "ok:", n, "quarters,", int(f50.notna().sum()), "with a latest forecast")
print("all track tests passed")
