"""Synthetic imbalance-price world for the live demo. NOT real data and NOT the real model: every number is generated here.

The world has a daily spot-price shape driven by synthetic wind and consumption, a hidden "stress" regime that raises the
chance of up-regulation and price jumps, and smooth regimes of up and down regulation.

The "forecaster" is a toy that peeks at the simulated truth and blurs it, so the demo can show forecasts turning into
reality. Its errors behave like a real forecast's: they grow with the horizon, they are the SAME random draw for a given
delivery time at every origin (so forecasts are revised smoothly towards reality rather than jumping), the p10-p90 band is
sized to the error so it holds about 80 % of outcomes, and spikes are anticipated with a probability, not a certainty.
"""
import numpy as np
import pandas as pd

Q = pd.Timedelta(minutes=15)
DRIVERS = ["sp_mean_4", "rt_calc_imb_last", "spot", "wind_fc_chg", "up_share_12", "rt_freq_dev60", "sp_mean_12", "cons_fc"]
# tuning of the toy forecaster
Z = 1.2816            # 80 % interval half-width in sigmas


def _ar(rng, n, phi, sd, x0=0.0):
    x = np.empty(n); x[0] = x0
    for i in range(1, n):
        x[i] = phi * x[i - 1] + rng.normal(0, sd)
    return x


def build_world(start="2027-02-01T00:00:00Z", days=14, seed=7):
    rng = np.random.default_rng(seed)
    n = days * 96 + 48
    t = pd.date_range(pd.Timestamp(start), periods=n, freq="15min", tz="UTC")
    loc = t.tz_convert("Europe/Helsinki")
    hod = (loc.hour + loc.minute / 60).to_numpy()
    wind = np.empty(n); wind[0] = 2500
    for i in range(1, n):
        wind[i] = np.clip(wind[i - 1] + 0.012 * (2600 - wind[i - 1]) + rng.normal(0, 55), 150, 5600)
    cons = 8300 + 1300 * np.exp(-((hod - 8) / 2.6) ** 2) + 1500 * np.exp(-((hod - 18) / 2.8) ** 2) - 700 * np.exp(-((hod - 3) / 3) ** 2) + rng.normal(0, 60, n)
    solar = np.clip(900 * np.exp(-((hod - 12.5) / 3.2) ** 2) * (0.6 + 0.4 * np.sin(np.arange(n) / 400)), 0, None)
    spot = np.clip(62 + 26 * (cons - 8500) / 1500 - 24 * (wind - 2600) / 1500 + rng.normal(0, 3, n), -4, 320)
    spot = pd.Series(spot).rolling(3, center=True, min_periods=1).mean().to_numpy()
    # hidden stress episodes (a few hours each) and smooth regimes of up / down regulation
    stress = np.zeros(n); s = 0.0
    for i in range(n):
        s = 0.97 * s + (rng.random() < 0.008) * rng.uniform(0.9, 1.8) + rng.normal(0, 0.015)
        stress[i] = max(0.0, s)
    lat = _ar(rng, n, 0.975, 0.28)
    drive = lat * 1.1 - 0.55 + 1.5 * stress
    up = drive + rng.normal(0, 0.12, n) > 0
    m = _ar(rng, n, 0.9, 0.12)                                    # smooth log-magnitude of the spread
    sp_up = 14 + np.exp(2.75 + 0.85 * stress + m) * (1 + rng.normal(0, 0.035, n))
    sp_dn = -(5 + np.exp(2.05 + 0.5 * m) * (1 + rng.normal(0, 0.035, n)))
    spread = np.where(up, sp_up, sp_dn)
    drop = (~up) & (lat < -0.7) & (rng.random(n) < 0.05)           # occasional negative spikes when the grid is long on power
    spread = np.where(drop, -(45 + np.exp(rng.normal(3.5, 0.4))), spread)
    imb = spot + spread
    w = pd.DataFrame({"t": t, "spot": spot, "imbalance": imb, "direction": np.where(up, 1, -1), "wind_fc": wind, "cons_fc": cons,
                      "solar_fc": solar, "stress": stress, "p_up_true": 1 / (1 + np.exp(-drive * 2.5))}).set_index("t")
    w["spread"] = w["imbalance"] - w["spot"]
    # truth-derived signals the toy forecaster is allowed to see (blurred) and one fixed noise draw per delivery time
    sm = lambda x, k: pd.Series(x).rolling(k, center=True, min_periods=1).mean().to_numpy()
    w["sp_smooth"] = sm(w["spread"], 3)
    w["hi_s"] = sm((w["spread"] > 50).astype(float), 7)
    w["lo_s"] = sm((w["spread"] < -50).astype(float), 7)
    w["up_s"] = sm((w["direction"] > 0).astype(float), 7)
    for k, phi in (("e_p50", 0.9), ("e_pu", 0.92), ("e_hi", 0.8), ("e_lo", 0.8)):   # unit-variance, time-smooth error draws
        w[k] = _ar(rng, n, phi, np.sqrt(1 - phi ** 2))
    return w


def forecast_rows(w, o, rng_seed=0):
    """40 quarter-hour rows for the hourly origin o (UTC Timestamp)."""
    cutoff = o - pd.Timedelta(hours=1)
    idx = w.index.get_indexer([o + k * Q for k in range(40)])
    idx = idx[idx >= 0]
    d = w.iloc[idx]
    step = np.arange(1, len(d) + 1)
    wgt = 0.97 - 0.0055 * step                                      # how much of the truth the forecaster "sees"
    sig = (2.2 + 0.75 * step ** 0.8) * (1 + 0.35 * d["stress"].to_numpy())          # error scale grows with the horizon
    prior = 4.0 + 14.0 * d["stress"].to_numpy()                      # climatological expected spread
    sp_hat = wgt * d["sp_smooth"].to_numpy() + (1 - wgt) * prior
    p50 = d["spot"].to_numpy() + sp_hat + sig * d["e_p50"].to_numpy() * 0.8
    wu = 0.86 - 0.006 * step                                        # direction is harder to see than the price level
    pu = np.clip(wu * (0.7 * d["up_s"].to_numpy() + 0.3 * d["p_up_true"].to_numpy()) + (1 - wu) * 0.36 + 0.09 * d["e_pu"].to_numpy() * (0.5 + step / 40), 0.03, 0.97)
    hi = np.clip(0.025 + wgt * 0.92 * d["hi_s"].to_numpy() + 0.03 * d["e_hi"].to_numpy() * (0.5 + step / 40) + (1 - wgt) * 0.12 * d["stress"].to_numpy(), 0.002, 0.93)
    lo = np.clip(0.012 + wgt * 0.9 * d["lo_s"].to_numpy() + 0.012 * d["e_lo"].to_numpy() * (0.5 + step / 40), 0.002, 0.7)
    lo_w = Z * sig * (0.75 + 0.35 * (1 - pu))
    hi_w = Z * sig * (1.0 + 0.9 * pu) + 6 * hi * 10
    fac = 1.0 - 0.17 * step / 40                                     # keeps the band close to 80 % coverage at every horizon
    p10, p90 = p50 - lo_w * fac, p50 + hi_w * fac
    rng = np.random.default_rng((rng_seed * 1_000_003 + int(o.value // 3_600_000_000_000)) % (2 ** 32))
    drivers = [";".join(rng.permutation(DRIVERS)[:3]) for _ in step]
    cols = ["forecast_origin_utc", "delivery_start_utc", "step", "p10", "p50", "p90", "p_up", "p_down", "p_none",
            "p_spike_high", "p_spike_low", "data_cutoff_utc", "top_drivers"]
    return pd.DataFrame({"forecast_origin_utc": o, "delivery_start_utc": d.index, "step": step, "p10": p10, "p50": p50, "p90": p90,
                         "p_up": pu, "p_down": 1 - pu - 0.002, "p_none": 0.002, "p_spike_high": hi, "p_spike_low": lo,
                         "data_cutoff_utc": cutoff, "top_drivers": drivers})[cols]


def to_iso(s):
    return pd.to_datetime(s, utc=True).dt.strftime("%Y-%m-%dT%H:%M:%SZ")
