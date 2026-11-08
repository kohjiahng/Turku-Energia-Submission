"""Combine spread distributions conditional on up- and down-regulation."""

import lightgbm as lgb
import numpy as np

from model import BASE

LV = np.array([.01, .02, .05, .1, .2, .3, .4, .5, .6, .7, .8, .9, .95, .98, .99])


def fit_mixture(train, feats, n_trees, params):
    """Fit the same quantile grid separately for each dominant direction."""
    settings = {**BASE, **params}
    models = {"f": list(feats)}
    for name, direction in (("up", 1), ("dn", -1)):
        subset = train.loc[train["y_dir"] == direction]
        models[name] = [
            lgb.LGBMRegressor(
                objective="quantile", alpha=level, n_estimators=n_trees, **settings
            ).fit(subset[feats], subset["y_spread"])
            for level in LV
        ]
    return models


def predict_quantiles(m, df):
    X = df[m["f"]]
    return {k: np.sort(np.column_stack([q.predict(X) for q in m[k]]), 1) for k in ("up", "dn")}


def fit_mixture_h(train, far_feats, near_feats, near_k, **kw):
    """Use the same horizon split as the direct models."""
    return {
        "K": near_k,
        "far": fit_mixture(train, far_feats, **kw),
        "near": fit_mixture(train[train["step"] <= near_k], near_feats, **kw),
    }


def predict_quantiles_h(mm, df):
    Q = predict_quantiles(mm["far"], df)
    k = (df["step"] <= mm["K"]).to_numpy()
    if k.any():
        Qn = predict_quantiles(mm["near"], df[k])
        for d in Q:
            Q[d][k] = Qn[d]
    return Q


def _cdf(Q, s):
    """Interpolate the cumulative distribution between quantiles and extend its tails."""
    n = Q.shape[0]
    lv = np.concatenate([[0.], LV, [1.]])
    lo = Q[:, :1] - 2 * np.maximum(Q[:, 1:2] - Q[:, :1], 1.0)
    hi = Q[:, -1:] + 3 * np.maximum(Q[:, -1:] - Q[:, -2:-1], 1.0)
    X = np.hstack([lo, Q, hi])
    X = np.maximum.accumulate(X + np.arange(X.shape[1]) * 1e-6, axis=1)
    s = np.broadcast_to(np.asarray(s, float), (n,))
    idx = np.clip((X < s[:, None]).sum(1), 1, X.shape[1] - 1)
    r = np.arange(n)
    x0, x1 = X[r, idx - 1], X[r, idx]
    w = np.clip((s - x0) / (x1 - x0), 0, 1)
    return lv[idx - 1] + w * (lv[idx] - lv[idx - 1])


def _mix_quantile(Qu, Qd, pu, q, iters=50):
    lo = np.full(len(pu), -600.)
    hi = np.full(len(pu), 1500.)
    for _ in range(iters):
        mid = (lo + hi) / 2
        F = pu * _cdf(Qu, mid) + (1 - pu) * _cdf(Qd, mid)
        hi = np.where(F >= q, mid, hi)
        lo = np.where(F < q, mid, lo)
    return (lo + hi) / 2


def mixture_outputs(Q, pu, spike_margin=50.0):
    """Return mixture spread quantiles and the chances of crossing each spike threshold."""
    Qu, Qd = Q["up"], Q["dn"]
    sp = {q: _mix_quantile(Qu, Qd, pu, q) for q in (.1, .5, .9)}
    def mixture_cdf(spread):
        threshold = np.full(len(pu), spread)
        return pu * _cdf(Qu, threshold) + (1 - pu) * _cdf(Qd, threshold)

    return sp, 1 - mixture_cdf(spike_margin), mixture_cdf(-spike_margin)
