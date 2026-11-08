"""Assemble LSTGBM's direction mixture, direct estimates, and spot correction."""

import numpy as np
import pandas as pd
from sklearn.linear_model import QuantileRegressor

from featsets import FAR, NEAR, NEAR_MAX_STEP
from mixture import fit_mixture_h, mixture_outputs, predict_quantiles_h
from model import P_NONE, fit_h, predict_h

INTERVAL_SCALE = 1.25
DIRECT_BLEND = 0.5
CFG = dict(n_q=200, n_c=200, params=dict(num_leaves=4, min_child_samples=500))
SPOT_OFFSETS = (-2, -1, 1, 2)
CORRECTION_SAMPLE_SIZE = 60_000


def neighbour_diffs(rows):
    """Compare spot prices across nearby delivery quarters at the same issue time."""
    origins = rows["forecast_origin_utc"]
    steps = rows["step"]
    spot = rows["spot"].to_numpy()
    by_step = pd.Series(spot, index=pd.MultiIndex.from_arrays([origins, steps]))
    differences = np.zeros((len(rows), len(SPOT_OFFSETS)))
    for column, offset in enumerate(SPOT_OFFSETS):
        neighbour_keys = pd.MultiIndex.from_arrays([origins, steps + offset])
        neighbour_spot = by_step.reindex(neighbour_keys).to_numpy()
        # The first and last steps can lack a neighbour within the forecast.
        differences[:, column] = np.nan_to_num(neighbour_spot - spot, nan=0.0)
    return differences


def fit_production(train, cfg=CFG):
    """Fit both forecast horizons and the residual spot-price correction."""
    models = {
        "base": fit_h(train, FAR, NEAR, NEAR_MAX_STEP, **cfg),
        "mix": fit_mixture_h(
            train, FAR, NEAR, NEAR_MAX_STEP, n_trees=cfg["n_q"], params=cfg["params"]
        ),
    }
    # Build the neighbourhood on the full grid before sampling residuals.
    differences = neighbour_diffs(train)
    sample = np.sort(
        np.random.default_rng(0).choice(
            len(train), size=min(CORRECTION_SAMPLE_SIZE, len(train)), replace=False
        )
    )
    residual = train["y_price"].to_numpy()[sample] - _predict_core(
        models, train.iloc[sample]
    )["p50"].to_numpy()
    correction = QuantileRegressor(
        quantile=0.5, alpha=0.0, fit_intercept=False, solver="highs"
    ).fit(differences[sample], residual)
    models["spot_corr"] = correction.coef_
    return models


def predict_production(models, rows):
    """Shift the price quantiles together, leaving the event probabilities intact."""
    output = _predict_core(models, rows)
    shift = neighbour_diffs(rows) @ models["spot_corr"]
    for column in ("p10", "p50", "p90"):
        output[column] = output[column] + shift
    return output


def _predict_core(models, rows):
    """Mix direction-specific spreads and blend their tails with direct predictions."""
    scale, blend = INTERVAL_SCALE, DIRECT_BLEND
    direct = predict_h(models["base"], rows)
    base = direct.copy()
    base["p10"] = direct["p50"] - scale * (direct["p50"] - direct["p10"])
    base["p90"] = direct["p50"] + scale * (direct["p90"] - direct["p50"])

    up_given_direction = (base["p_up"] / (1 - P_NONE)).to_numpy()
    spreads, high, low = mixture_outputs(
        predict_quantiles_h(models["mix"], rows), up_given_direction
    )
    spot = base["spot"].to_numpy()
    median = spot + spreads[0.5]
    lower = median - scale * (spreads[0.5] - spreads[0.1])
    upper = median + scale * (spreads[0.9] - spreads[0.5])

    output = base.copy()
    output["p50"] = median
    output["p10"] = np.minimum((1 - blend) * base["p10"] + blend * lower, median)
    output["p90"] = np.maximum((1 - blend) * base["p90"] + blend * upper, median)
    output["p_spike_high"] = (1 - blend) * base["p_spike_high"] + blend * high
    output["p_spike_low"] = (1 - blend) * base["p_spike_low"] + blend * low
    return output
