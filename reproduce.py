"""Rebuild the LSTGBM forecast from organizer inputs and the frozen Fingrid features."""
import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

import pandas as pd

from features import Store, build_rows, load_long
from featsets import FAR, RT_SMALL
from make_submission import CFG, forecast, training_rows
from production import fit_production

ROOT = Path(__file__).resolve().parent
TRAINING_START = "2026-06-02T00:00Z"
TRAINING_ROWS = 119_660
REFERENCE_SHA = "9a4549944f1235009691892643f214d19ee35e72ab25250c9b485befeff96929"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--starter-package", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    package = args.starter_package.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)

    origin_file = package / "forecast_origins.csv"
    test_origins = pd.to_datetime(pd.read_csv(origin_file).forecast_origin_utc, utc=True)
    origins = pd.date_range(TRAINING_START, test_origins.max(), freq="h")
    freeze = test_origins.min()

    cache = pd.read_parquet(
        ROOT / "fingrid_features.parquet", columns=["forecast_origin_utc", *RT_SMALL]
    )
    cache["forecast_origin_utc"] = pd.to_datetime(cache.forecast_origin_utc, utc=True)
    if not pd.DatetimeIndex(cache.forecast_origin_utc).equals(origins):
        raise ValueError("The Fingrid cache does not match the hourly forecast grid.")

    store = Store(load_long(package))
    keys = ["forecast_origin_utc", "delivery_start_utc", "data_cutoff_utc"]
    rows = build_rows(store, origins)[keys + FAR].merge(
        cache, on="forecast_origin_utc", how="left", validate="many_to_one"
    )
    train = training_rows(rows, package, freeze)
    if len(train) != TRAINING_ROWS:
        raise ValueError(
            f"Expected {TRAINING_ROWS} training rows, got {len(train)}. Check the starter data."
        )
    print(f"Training on {len(train)} rows", flush=True)
    model = fit_production(train, CFG)
    test = rows.loc[rows.forecast_origin_utc.isin(test_origins)].reset_index(drop=True)
    template = pd.read_csv(package / "sample_submission.csv")
    export = output / "forecast_backtest.csv"
    forecast(model, test, template).to_csv(export, index=False)

    subprocess.run(
        [
            sys.executable,
            str(package / "validate_submission.py"),
            "--backtest", str(export),
            "--origins", str(origin_file),
        ],
        check=True,
    )
    digest = hashlib.sha256(export.read_bytes()).hexdigest()
    print(f"Forecast SHA-256: {digest}", flush=True)
    if digest != REFERENCE_SHA:
        raise SystemExit(
            "The output passed schema validation, but differs from the reference forecast."
        )
    print("Reference forecast reproduced byte for byte.")


if __name__ == "__main__":
    main()
