"""LightGBM models for the spread quantiles, direction and spike probabilities."""

import lightgbm as lgb
import numpy as np
import pandas as pd

P_NONE = 0.002  # 'none' direction is 0.2 % of quarter-hours: fixed small probability
BASE = dict(
    num_leaves=15,
    learning_rate=0.03,
    min_child_samples=200,
    subsample=0.7,
    subsample_freq=1,
    colsample_bytree=0.7,
    reg_lambda=10.0,
    verbose=-1,
    n_jobs=-1,
    random_state=0,
)


def fit_all(train, feats, n_q, n_c, params):
    """Fit the direct spread quantiles and three binary classifiers."""
    settings = {**BASE, **params}
    inputs = train[feats]
    models = {"feats": feats}
    for name, quantile in (("q10", 0.1), ("q50", 0.5), ("q90", 0.9)):
        models[name] = lgb.LGBMRegressor(
            objective="quantile", alpha=quantile, n_estimators=n_q, **settings
        ).fit(inputs, train["y_spread"])

    dominant = train.loc[train["y_dir"] != 0]
    models["up"] = lgb.LGBMClassifier(n_estimators=n_c, **settings).fit(
        dominant[feats], (dominant["y_dir"] == 1).astype(int)
    )
    for head, label in (("hi", "y_hi"), ("lo", "y_lo")):
        models[head] = lgb.LGBMClassifier(n_estimators=n_c, **settings).fit(
            inputs, train[label].astype(int)
        )
    return models


def predict(models, rows):
    inputs = rows[models["feats"]]
    spreads = np.sort(
        np.column_stack([models[head].predict(inputs) for head in ("q10", "q50", "q90")]),
        axis=1,
    )
    up = models["up"].predict_proba(inputs)[:, 1]
    high = models["hi"].predict_proba(inputs)[:, 1]
    low = models["lo"].predict_proba(inputs)[:, 1]
    # High and low spikes are mutually exclusive.
    spike_scale = np.clip(high + low, 1, None)
    spot = rows["spot"].to_numpy()
    return pd.DataFrame(
        {
            "spot": spot,
            "p10": spot + spreads[:, 0],
            "p50": spot + spreads[:, 1],
            "p90": spot + spreads[:, 2],
            "p_up": (1 - P_NONE) * up,
            "p_down": (1 - P_NONE) * (1 - up),
            "p_none": P_NONE,
            "p_spike_high": high / spike_scale,
            "p_spike_low": low / spike_scale,
        },
        index=rows.index,
    )


def fit_h(train, far_feats, near_feats, near_max_step, **kw):
    """Train the general model on all leads and the specialist on near-term leads."""
    return {
        "K": near_max_step,
        "far": fit_all(train, feats=far_feats, **kw),
        "near": fit_all(train[train["step"] <= near_max_step], feats=near_feats, **kw),
    }


def predict_h(mh, df):
    out = predict(mh["far"], df)
    mk = (df["step"] <= mh["K"]).to_numpy()
    if mk.any():
        out.loc[mk, :] = predict(mh["near"], df[mk]).to_numpy()
    return out
