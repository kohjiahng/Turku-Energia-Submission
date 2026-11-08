# Reproduce the Turku Energia forecast

This folder retrains FARMERS’ LSTGBM (Long Short-Term Gradient Boosting Model) and generates the official test-period CSV. It uses the organizer's original starter package, which must be available separately. No API key, network access during the run, or pre-trained model is required.

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

The runner writes `reproduced/forecast_backtest.csv`, runs the organizer's validator, and checks its SHA-256 against the reference export. Keep the licensed starter data and the generated forecast in the private challenge workspace.

## Code map

- `reproduce.py` is the entry point. It builds the input grid, trains LSTGBM, and verifies the export.
- `features.py` and `featsets.py` define the starter feature calculations and the feature set for each horizon.
- `model.py` fits the direct LightGBM regressors and classifiers. `mixture.py` fits and combines the direction-conditional spread distributions.
- `production.py` assembles the final forecast. `make_submission.py` builds eligible training labels and formats the CSV.
- [Sources and licences](SOURCES_AND_LICENCES.md) identifies the data, services, and software used.
