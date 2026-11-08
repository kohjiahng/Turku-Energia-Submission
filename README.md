# Reproduce the Turku Energia forecast

This folder retrains FARMERS’ LSTGBM (Long Short-Term Gradient Boosting Model) and generates the official test-period CSV. It uses the organizer's original starter package, which must be available separately. No API key, network access during the run, or pre-trained model is required.

## Deliverables

| Deliverable | Path |
| --- | --- |
| Forecast for the test period (official CSV, 26,920 rows, 5 Oct to 1 Nov 2026) | [`submission.csv`](submission.csv) |
| Evaluation report | [`FARMERS Turku Energia Evaluation Report.pdf`](<FARMERS Turku Energia Evaluation Report.pdf>) |
| Presentation deck | [`FARMERS - LSTGBM Final.pptx`](<FARMERS - LSTGBM Final.pptx>) |
| Model source code | [`reproduce.py`](reproduce.py), [`features.py`](features.py), [`featsets.py`](featsets.py), [`model.py`](model.py), [`mixture.py`](mixture.py), [`production.py`](production.py), [`make_submission.py`](make_submission.py) |
| Pinned dependencies | [`requirements.txt`](requirements.txt) |
| Frozen Fingrid feature cache (13 real-time features, CC BY 4.0) | [`fingrid_features.parquet`](fingrid_features.parquet) |
| Sources and licences | [`SOURCES_AND_LICENCES.md`](SOURCES_AND_LICENCES.md) |
| Dashboard (Radar), code and README | [`dashboard/`](dashboard/), start with [`dashboard/README.md`](dashboard/README.md) |
| Dashboard page | [`dashboard/variants/5_radar/index.html`](dashboard/variants/5_radar/index.html), served by [`dashboard/serve.py`](dashboard/serve.py) |
| Dashboard data: validation and test forecasts, factor shares | [`dashboard/data/`](dashboard/data/) (`forecast_validation.csv`, `forecast_test.csv`, `driver_shares_validation.parquet`, `driver_shares_test.parquet`), rebuilt by [`dashboard/tools/build_data.py`](dashboard/tools/build_data.py) |
| Render deployment of the dashboard | `render.yaml` in the repository root, steps in [`dashboard/README.md`](dashboard/README.md#deploy-on-render) |

Not included, because of the Nord Pool licence: the organizer's starter package data (`inputs.parquet`, `spot_nordpool.csv`, `actuals_validation.csv`). The dashboard needs them in `starter_package_v1/data/` (or in `RADAR_DATA_DIR` when deployed).

Run the dashboard locally from the repository root: `python final_submission/dashboard/serve.py --port 8770`, then open <http://127.0.0.1:8770>.

Use Python 3.13 and install the pinned dependencies:

```sh
python -m pip install -r requirements.txt
```

On macOS, LightGBM also needs the OpenMP runtime (`libomp`). From this folder, point the runner at the unzipped starter package. The output directory must be new.

```sh
python reproduce.py --starter-package /path/to/starter_package_v1 --output reproduced
```

The starter package supplies `data/inputs.parquet`, `data/spot_nordpool.csv`, `forecast_origins.csv`, `sample_submission.csv`, and `validate_submission.py`. The runner rebuilds the starter features using each row's `first_usable_origin_utc` and freezes training labels at the first official test origin.

`fingrid_features.parquet` supplies the 13 extra features used by the first-two-hour model, one row per forecast origin. It is the exact cache used for the final export, restricted to features from Fingrid Open Data datasets 397, 177, 186, 181, 193, and 194 (CC BY 4.0). The wind and consumption errors also use their Fingrid day-ahead series. The archived feature calculation admits measurements whose end time plus 30 minutes is at or before the forecast origin. The cache includes no Nord Pool spot values, starter labels, or model predictions.

`submission.csv` in this folder is the test-period forecast produced by this runner (pinned dependencies; it passes the organizer's validator). The byte-for-byte hash check can fail on another machine or Python build because of floating-point noise: two runs on Linux with different pandas versions differed by at most 6e-14 EUR/MWh in the price quantiles and by exactly zero in every probability, driver list and data cut-off.

The runner writes `reproduced/forecast_backtest.csv`, runs the organizer's validator, and checks its SHA-256 against the reference export. Keep the licensed starter data and the generated forecast in the private challenge workspace.

## Code map

- `reproduce.py` is the entry point. It builds the input grid, trains LSTGBM, and verifies the export.
- `features.py` and `featsets.py` define the starter feature calculations and the feature set for each horizon.
- `model.py` fits the direct LightGBM regressors and classifiers. `mixture.py` fits and combines the direction-conditional spread distributions.
- `production.py` assembles the final forecast. `make_submission.py` builds eligible training labels and formats the CSV.
- [Sources and licences](SOURCES_AND_LICENCES.md) identifies the data, services, and software used.
