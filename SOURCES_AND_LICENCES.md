# FARMERS · LSTGBM sources and licences

Turku Energia imbalance price challenge. This inventory covers the submitted LSTGBM forecast, its reproducibility package, and the evaluation report.

## Data sources

| Source | What LSTGBM uses | Licence or usage rights |
|---|---|---|
| Fingrid Open Data, organizer starter package | Historical Finnish imbalance prices, mFRR balancing direction and reserve prices, measured consumption and cross-border flows, and day-ahead consumption, wind and solar forecasts. | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), as stated in the organizer’s starter package. The team derives lagged values, rolling summaries, and forecast errors. |
| ENTSO-E Transparency Platform, organizer starter package | SE1 and SE3 imbalance prices and Finland–Estonia transfer capacity. | CC BY 4.0 for the free-reuse series supplied by the organizer. The organizer converted them to UTC and a 15-minute grid. |
| Nord Pool, organizer starter package | Published Finnish day-ahead spot prices for the forecast anchor, historical spread labels, and validation spike thresholds. | Licensed to Turku Energia for hackathon use only. The organizer prohibits redistribution and public repository publication and requires deletion after the event. This applies to `spot_nordpool.csv` and the `spot_price` column in `actuals_validation.csv`. The judge uses the organizer’s copy. |
| Organizer-supplied calendar and forecast grid | Finnish hour, weekday, holidays, daylight saving, and forecast lead times. | Supplied for the challenge. The starter documentation states no separate licence for these fields. |
| Fingrid Open Data, six additional feeds outside the starter package | [Calculated imbalance (397)](https://data.fingrid.fi/datasets/397), [frequency (177)](https://data.fingrid.fi/en/datasets/177), [cumulative surplus/deficit (186)](https://data.fingrid.fi/en/datasets/186), [wind generation (181)](https://data.fingrid.fi/en/datasets/181), [consumption (193)](https://data.fingrid.fi/en/datasets/193), and [net imports (194)](https://data.fingrid.fi/en/datasets/194). | CC BY 4.0, attribution to Fingrid. The submission includes 13 derived features in `fingrid_features.parquet`. They include lagged readings, rolling summaries, and wind and consumption errors relative to the starter forecasts. The cache contains no Nord Pool data. |

## External services and ready-made models

| Service or model | Use | Terms |
|---|---|---|
| Fingrid Open Data API | Source of the six additional series used to prepare the feature cache. The submitted runner uses the bundled cache and runs offline after dependencies are installed. | Fingrid Open Data terms and the dataset licences above. |
| Google Docs | Editing and PDF export of the evaluation report. | Applicable [Google service terms](https://policies.google.com/terms). |
| OpenAI Codex and image generation | Assisted with code, report drafting, and report illustrations. | Applicable [OpenAI service terms](https://openai.com/policies/row-terms-of-use/). |
| Ready-made forecasting models | None. FARMERS fits the LightGBM trees and the spot-price correction from scratch on the supplied historical inputs and the derived Fingrid features. | LightGBM is a training library. Its software licence is listed below. |

## Software components

The forecasting package pins the following versions in `requirements.txt`. Library licences below refer to the named projects. Installed distributions retain their bundled notices for third-party code.

| Component | Version | Licence | Role |
|---|---|---|---|
| Python | 3.13 | PSF licence | Runtime |
| LightGBM | 4.7.0 | MIT | Boosted trees for quantiles, balancing direction, and spike probabilities |
| scikit-learn | 1.9.1 | BSD 3-Clause | Median regression for the spot-price correction |
| NumPy | 2.5.3 | BSD 3-Clause, with bundled component notices | Numerical arrays and calculations |
| pandas | 3.0.6 | BSD 3-Clause | Time-series features and CSV output |
| SciPy | 1.18.1 | BSD 3-Clause, with bundled component notices | Numerical routines and regression solver support |
| PyArrow | 26.0.1 | Apache 2.0 | Parquet input and cache reading |
| joblib | 1.6.0 | BSD 3-Clause | scikit-learn dependency |
| threadpoolctl | 3.7.0 | BSD 3-Clause | scikit-learn dependency for native thread pools |
| LLVM OpenMP runtime (`libomp`, macOS) | Platform-installed | Apache 2.0 with LLVM exceptions | LightGBM threading on macOS |
| Organizer validator and scorer | Validator 0.1.0, scorer 0.1.1 | Supplied in the challenge starter package. No separate licence is stated in the starter documentation. | Format verification and validation scoring |

FARMERS supplies the LSTGBM source code for judging and reproducibility. The run instructions are in [README.md](README.md).
