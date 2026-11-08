"""Publication-gated training labels and the organizer's forecast CSV format."""

import numpy as np
import pandas as pd

import production

CFG = {**production.CFG, "params": {**production.CFG["params"], "n_jobs": 4}}
KEYS = ["forecast_origin_utc", "delivery_start_utc", "step"]
OUTPUT_COLUMNS = [
    *KEYS,
    "p10", "p50", "p90",
    "p_up", "p_down", "p_none",
    "p_spike_high", "p_spike_low",
    "data_cutoff_utc", "top_drivers",
]


def _rank_drivers(models, inputs, count):
    """Give equal weight to absolute contributions from both direct models."""
    contributions = []
    for head in ("q50", "up"):
        # The last column is the model's baseline contribution.
        values = np.abs(models[head].predict(inputs, pred_contrib=True)[:, :-1])
        contributions.append(values / values.sum(1, keepdims=True).clip(1e-9))
    top = np.argsort(-sum(contributions), axis=1)[:, :count]
    names = np.array(models["feats"])
    return [";".join(names[row]) for row in top]


def drivers(models, rows, count=3):
    """Rank features using the direct median and direction models for each horizon."""
    output = np.empty(len(rows), dtype=object)
    near = (rows["step"] <= models["K"]).to_numpy()
    for mask, model in ((~near, models["far"]), (near, models["near"])):
        if mask.any():
            output[mask] = _rank_drivers(model, rows.loc[mask, model["feats"]], count)
    return list(output)


def forecast(models, rows, template):
    """Predict and align the result with the organizer's submission grid."""
    predictions = production.predict_production(models, rows)
    output = pd.concat(
        [
            rows[["forecast_origin_utc", "step", "data_cutoff_utc"]],
            predictions.drop(columns="spot"),
        ],
        axis=1,
    )
    output["top_drivers"] = drivers(models["base"], rows)
    for column in ("forecast_origin_utc", "data_cutoff_utc"):
        output[column] = pd.to_datetime(output[column], utc=True).dt.strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    aligned = template[KEYS].merge(
        output, on=["forecast_origin_utc", "step"], how="left", validate="1:1"
    )
    if aligned[["p10", "p50", "p90", "p_up"]].isna().any().any():
        raise ValueError("The submission template contains rows without a forecast.")
    return aligned[OUTPUT_COLUMNS]


def training_rows(df, package, freeze):
    """Keep deliveries and labels available before the test-period training freeze."""
    ids = ["fi_imbalance_price", "fi_spot_price", "fi_mfrr_direction"]
    cols = ["series_id", "timestamp_utc", "first_usable_origin_utc", "value"]
    market = pd.read_parquet(
        package / "data/inputs.parquet",
        columns=cols,
        filters=[("series_id", "in", ids), ("timestamp_utc", "<", freeze)],
    )
    spot = pd.read_csv(package / "data/spot_nordpool.csv", usecols=cols)
    data = pd.concat([market, spot], ignore_index=True)
    for field in ("timestamp_utc", "first_usable_origin_utc"):
        data[field] = pd.to_datetime(data[field], utc=True)
    available = data.loc[data.timestamp_utc.lt(freeze) & data.first_usable_origin_utc.le(freeze)]
    train = df.loc[df.forecast_origin_utc.lt(freeze) & df.delivery_start_utc.lt(freeze)]
    labels = available.pivot(index="timestamp_utc", columns="series_id", values="value")
    labels = labels.reindex(train.delivery_start_utc)[ids]
    eligible = np.isfinite(labels).all(axis=1).to_numpy()
    train = train.loc[eligible].reset_index(drop=True)
    train[["y_price", "y_spot", "y_dir"]] = labels.loc[eligible].to_numpy()
    train["y_spread"] = train.y_price - train.y_spot
    train["y_hi"] = train.y_spread > 50
    train["y_lo"] = train.y_spread < -50
    return train

