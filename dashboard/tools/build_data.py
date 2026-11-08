#!/usr/bin/env python
"""Build the dashboard's forecast files and factor shares with the final submission's model code (final_submission/*.py).

    OMP_NUM_THREADS=4 .venv/bin/python -W ignore final_submission/dashboard/tools/build_data.py [validation|test|all] [--starter-package DIR]

For each run the model is trained exactly as in reproduce.py (labels frozen at the first forecast origin of the run: the first
validation origin for `validation`, the first test origin for `test`) and then writes, into data/:

  forecast_<run>.csv           the organiser-format forecast (p10/p50/p90, direction and spike probabilities, top drivers)
  driver_shares_<run>.parquet  per (origin, feature): mean share over the near steps (1-8) and far steps (9-40) of the model's
                               influence = SHAP contributions of the p50 model and the up-regulation model, each normalised to sum to 1,
                               then averaged (the definition behind the `top_drivers` column). Only shares are stored, no prices.

The test forecast is checked against the reference hash of reproduce.py. Needs the starter package (default: <repo>/starter_package_v1).
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
FINAL = HERE.parents[1]            # final_submission/
sys.path.insert(0, str(FINAL))
from features import Store, build_rows, load_long  # noqa: E402
from featsets import FAR, RT_SMALL                 # noqa: E402
from make_submission import CFG, forecast, training_rows  # noqa: E402
from production import fit_production              # noqa: E402
import reproduce as R                              # noqa: E402  (TRAINING_START, REFERENCE_SHA)

DATA = HERE.parent / "data"


def shares(m, X):
    s = 0
    for name in ("q50", "up"):
        a = np.abs(m[name].predict(X, pred_contrib=True)[:, :-1])
        s = s + a / a.sum(1, keepdims=True).clip(1e-9)
    return s / 2


def driver_shares(mh, rows):
    out = []
    for part, mask in (("near", rows["step"] <= mh["K"]), ("far", rows["step"] > mh["K"])):
        m = mh[part]
        r = rows[mask.to_numpy()]
        sh = pd.DataFrame(shares(m, r[m["feats"]]), columns=m["feats"])
        sh["forecast_origin_utc"] = r["forecast_origin_utc"].to_numpy()
        g = sh.groupby("forecast_origin_utc").mean().stack().rename(part).reset_index().rename(columns={"level_1": "feature"})
        out.append(g.set_index(["forecast_origin_utc", "feature"]))
    return pd.concat(out, axis=1).fillna(0.0).reset_index()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", nargs="?", default="all", choices=["validation", "test", "all"])
    ap.add_argument("--starter-package", type=Path, default=FINAL.parent / "starter_package_v1")
    a = ap.parse_args()
    pkg = a.starter_package.resolve()
    test_origins = pd.to_datetime(pd.read_csv(pkg / "forecast_origins.csv").forecast_origin_utc, utc=True)
    origins = pd.date_range(R.TRAINING_START, test_origins.max(), freq="h")
    cache = pd.read_parquet(FINAL / "fingrid_features.parquet", columns=["forecast_origin_utc", *RT_SMALL])
    cache["forecast_origin_utc"] = pd.to_datetime(cache.forecast_origin_utc, utc=True)
    if not pd.DatetimeIndex(cache.forecast_origin_utc).equals(origins):
        raise SystemExit("The Fingrid cache does not match the hourly forecast grid.")
    keys = ["forecast_origin_utc", "delivery_start_utc", "data_cutoff_utc"]
    print("building features ...", flush=True)
    rows = build_rows(Store(load_long(pkg)), origins)[keys + FAR].merge(cache, on="forecast_origin_utc", how="left", validate="many_to_one")
    for mode in (["validation", "test"] if a.mode == "all" else [a.mode]):
        tdir = pkg / "baseline_output" / "validation" if mode == "validation" else pkg
        tmpl = pd.read_csv(tdir / "sample_submission.csv")
        want = pd.to_datetime(tmpl["forecast_origin_utc"], utc=True).unique()
        freeze = pd.to_datetime(pd.read_csv(tdir / "forecast_origins.csv").forecast_origin_utc, utc=True).min()
        train = training_rows(rows, pkg, freeze)
        print(mode, "training on", len(train), "rows (labels before %s)" % freeze, flush=True)
        model = fit_production(train, CFG)
        test = rows[rows["forecast_origin_utc"].isin(want)].reset_index(drop=True)
        fc = forecast(model, test, tmpl)
        dest = DATA / f"forecast_{mode}.csv"
        fc.to_csv(dest, index=False)
        print("wrote", dest, fc.shape, flush=True)
        if mode == "test":
            import hashlib
            h = hashlib.sha256(dest.read_bytes()).hexdigest()
            print("test forecast matches the reference hash:", h == R.REFERENCE_SHA, flush=True)
        res = driver_shares(model["base"], test)
        dest = DATA / f"driver_shares_{mode}.parquet"
        res.to_parquet(dest, index=False)
        tot = 8 / 40 * res["near"] + 32 / 40 * res["far"]
        top = tot.groupby(res["feature"]).sum().sort_values(ascending=False)
        print("wrote", dest, res.shape, "| top:", ", ".join("%s %.1f%%" % (k, 100 * v / top.sum()) for k, v in top.head(5).items()), flush=True)


if __name__ == "__main__":
    main()
