"""Build starter-data features from values available at each forecast origin."""

from pathlib import Path

import numpy as np
import pandas as pd

Q = pd.Timedelta(minutes=15)
STEPS = 40
BACK_Q = 96 * 8          # Retain the reference run’s eight-day input window.
FWD_Q = STEPS + 24       # future window (published forecasts / spot), +24 for centred spot context
SPREAD_SPIKE = 50.0


def load_long(package: Path) -> pd.DataFrame:
    inp = pd.read_parquet(package / "data" / "inputs.parquet")
    spot = pd.read_csv(package / "data" / "spot_nordpool.csv")
    long = pd.concat([inp, spot], ignore_index=True)
    for c in ("timestamp_utc", "first_usable_origin_utc"):
        long[c] = pd.to_datetime(long[c], utc=True)
    return long


class Store:
    """Values and their first_usable_origin_utc on a common quarter-hour grid."""

    def __init__(self, long: pd.DataFrame):
        values = long.pivot(
            index="timestamp_utc", columns="series_id", values="value"
        ).sort_index()
        availability = long.pivot(
            index="timestamp_utc", columns="series_id", values="first_usable_origin_utc"
        )
        self.grid = pd.date_range(values.index.min(), values.index.max(), freq=Q)
        self.columns = list(values.columns)
        self.values = values.reindex(self.grid).to_numpy(float)
        availability = availability.reindex(index=self.grid, columns=self.columns)
        self.available_ns = availability.apply(
            lambda column: column.dt.tz_convert("UTC")
            .dt.tz_localize(None)
            .astype("datetime64[ns]")
            .astype("int64")
            .where(column.notna(), np.iinfo("int64").max)
        ).to_numpy("int64")

    def at(self, origin: pd.Timestamp) -> pd.DataFrame:
        """Mask each value until the organizer's recorded availability time."""
        position = self.grid.get_loc(origin)
        start = max(0, position - BACK_Q)
        stop = min(len(self.grid), position + FWD_Q + 1)
        origin_ns = origin.value
        values = np.where(
            self.available_ns[start:stop] <= origin_ns, self.values[start:stop], np.nan
        )
        return pd.DataFrame(values, index=self.grid[start:stop], columns=self.columns)


def _tail(ser: pd.Series, end: pd.Timestamp, n: int) -> pd.Series:
    return ser.loc[end - (n - 1) * Q: end]


def origin_features(window: pd.DataFrame, origin: pd.Timestamp):
    """Return shared origin features, per-quarter features, and the data cutoff."""
    spot = window["fi_spot_price"]
    spread = window["fi_imbalance_price"] - spot
    direction = window["fi_mfrr_direction"]
    last = spread.last_valid_index()
    local_origin = origin.tz_convert("Europe/Helsinki")
    history = {"o_hour": local_origin.hour, "o_weekday": local_origin.weekday()}

    if last is not None:
        for quarters in (4, 12, 96):
            recent_spread = _tail(spread, last, quarters)
            recent_direction = _tail(direction, last, quarters)
            history[f"sp_mean_{quarters}"] = recent_spread.mean()
            history[f"up_share_{quarters}"] = (
                (recent_direction == 1).sum() / max(recent_direction.notna().sum(), 1)
            )
            if quarters >= 12:
                history[f"sp_absmean_{quarters}"] = recent_spread.abs().mean()
        last_day = _tail(spread, last, 96)
        history["hi_share_96"] = (last_day > SPREAD_SPIKE).mean()
        history["lo_share_96"] = (last_day < -SPREAD_SPIKE).mean()
        history["last_spread"] = spread.loc[last]
        history["last_dir"] = direction.get(last, np.nan)
        history["last_up_minus_spot"] = (window["fi_mfrr_up_price"] - spot).get(last, np.nan)
        history["last_dn_minus_spot"] = (window["fi_mfrr_down_price"] - spot).get(last, np.nan)
        history["spot_mean96_past"] = _tail(spot, last, 96).mean()

    for area in ("se1", "se3"):
        gap = window[f"{area}_imbalance_price_long"] - spot
        latest = gap.last_valid_index()
        history[f"{area}_gap_last"] = gap.loc[latest] if latest is not None else np.nan
        if area == "se1":
            history["se1_gap_mean4"] = (
                _tail(gap, latest, 4).mean() if latest is not None else np.nan
            )

    latest_consumption = window["fi_consumption"].last_valid_index()
    if latest_consumption is not None:
        measured = window["fi_consumption"].loc[latest_consumption]
        expected = window["fi_consumption_fc_da"].get(latest_consumption, np.nan)
        history["cons_err_last"] = measured - expected
        history["se1_flow_last"] = window["fi_se1_flow"].get(latest_consumption, np.nan)

    delivery = pd.date_range(origin, periods=STEPS, freq=Q)
    wind_forecast = window["fi_wind_fc_da"]
    residual_load = (
        window["fi_consumption_fc_da"] - wind_forecast - window["fi_solar_fc_da"]
    )
    # Six hours on either side, limited to spot values available at this origin.
    spot_context = spot.rolling(49, center=True, min_periods=24).mean()
    horizon = pd.DataFrame(
        {
            "step": np.arange(1, STEPS + 1),
            "gap_q": (delivery - last) / Q if last is not None else np.nan,
            "spot": spot.reindex(delivery).to_numpy(),
            "spot_vs_ctx": (spot - spot_context).reindex(delivery).to_numpy(),
            "spot_vs_past": (
                spot.reindex(delivery).to_numpy() - history.get("spot_mean96_past", np.nan)
            ),
            "cons_fc": window["fi_consumption_fc_da"].reindex(delivery).to_numpy(),
            "wind_fc": wind_forecast.reindex(delivery).to_numpy(),
            "solar_fc": window["fi_solar_fc_da"].reindex(delivery).to_numpy(),
            "res_load_fc": residual_load.reindex(delivery).to_numpy(),
            "wind_fc_chg": (
                wind_forecast.reindex(delivery).to_numpy() - wind_forecast.get(origin, np.nan)
            ),
            "res_load_chg": (
                residual_load.reindex(delivery).to_numpy() - residual_load.get(origin, np.nan)
            ),
            "fi_ee_cap": window["fi_ee_capacity_da"].reindex(delivery).to_numpy(),
            "ee_fi_cap": window["ee_fi_capacity_da"].reindex(delivery).to_numpy(),
            "hour": window["cal_hour_local"].reindex(delivery).to_numpy(),
            "weekday": window["cal_weekday_local"].reindex(delivery).to_numpy(),
            "holiday": window["cal_is_holiday_fi"].reindex(delivery).to_numpy(),
            "dst": window["cal_is_dst"].reindex(delivery).to_numpy(),
        },
        index=delivery,
    )
    for c, src in (("spot_h", "spot"), ("res_load_h", "res_load_fc"), ("wind_h", "wind_fc")):
        horizon[f"{c}_mean"] = horizon[src].mean()
        horizon[f"{c}_max"] = horizon[src].max()
        horizon[f"{c}_min"] = horizon[src].min()
    cutoff = (last + Q) if last is not None else pd.NaT  # end of the latest realised quarter used
    return history, horizon, cutoff


def build_rows(store: Store, origins) -> pd.DataFrame:
    """Build one row per issue hour and delivery quarter."""
    parts = []
    for origin in origins:
        history, horizon, cutoff = origin_features(store.at(origin), origin)
        horizon = horizon.assign(**history)
        horizon.insert(0, "forecast_origin_utc", origin)
        horizon.insert(1, "delivery_start_utc", horizon.index)
        horizon["data_cutoff_utc"] = cutoff
        parts.append(horizon.reset_index(drop=True))
    return pd.concat(parts, ignore_index=True)
