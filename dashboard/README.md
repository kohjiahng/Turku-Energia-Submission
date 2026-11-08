# Radar dashboard

A browser dashboard for the imbalance-price forecast: forecast time slider, spike risk, alerts, metrics against the official baseline, and a simulated live feed. Python standard library HTTP server plus pandas and numpy; the page is plain ES modules with SVG charts (no build step, no libraries).

## Run

From the repository root:

```sh
.venv/bin/python -W ignore final_submission/dashboard/serve.py --port 8770
```

Open http://127.0.0.1:8770 (it redirects to `/v/5_radar/`). Stop with Ctrl+C. The data is licensed for the hackathon only, so do not publish screenshots.

Inputs, set in `config.json` (paths relative to this folder): the forecast files `data/forecast_{validation,test}.csv`, built with the final submission's model code (see below), and the starter package data in `starter_package_v1/data/` (licensed, not copied here). Explore supports `?run=validation|test&origin=<UTC ISO>` deep links.

## Files

| File | Role |
|---|---|
| `serve.py` | Entry point. Routes `/`, `/v/5_radar/`, `/shared/`, the API and the `/live/` prefix |
| `fetch_data.py` | Optional start-up download of the data bundle (`RADAR_DATA_URL`) |
| `server.py`, `adapters.py`, `alerts.py`, `i18n.py`, `drivers.py`, `drivers_glossary.json`, `config.json` | Forecast API (`/api/meta`, `timeline`, `forecast`, `alerts`): loads forecasts and actuals, evaluates alert rules, explains drivers |
| `track_api.py` | `/api/track`: 15-minute actual, spot, direction and latest-forecast arrays for the chart |
| `metrics_api.py` | `/api/metrics` (scorer-compatible metrics) and `/api/driver_shares` |
| `data/forecast_*.csv`, `data/driver_shares_*.parquet`, `tools/build_data.py` | Forecasts and factor shares, produced by `tools/build_data.py` with the model code in `final_submission/` (a few minutes; needs the starter package) |
| `demo/fake_world.py`, `demo/synth_feed.py` | Simulated Live tab (output in git-ignored `demo/out/`) |
| `shared/common.js`, `shared/live.js` | API client, session and stepping, time formatting; the `LiveChart` class |
| `variants/5_radar/index.html` | The page |
| `test_track.py` | Tests for `/api/track` (`.venv/bin/python -W ignore final_submission/dashboard/test_track.py`) |

## The page

- **Controls:** day tiles, hour pills, arrow keys (Shift for days), Play and a 1×/2×/4× speed button, an Examples picker, theme toggle. Four cards: price jump risk, price drop risk, grid needs in the next 2 hours, next quarter.
- **Sliding forecast-time chart:** a fixed vertical line marks the forecast time and time scrolls beneath it. Drag the chart (snaps to hourly forecasts) or click the history side to jump. The forecast fan and spike strip morph between hours.
  - Left of the line: the actual price (dashed where not yet known at forecast time) and the **latest forecast**, the one issued in each quarter's own hour (15 min to 1 h lead), so it looks more accurate than the 10 h forecast on the right.
  - Right of the line: p10 to p90 band, p50, spot, and the actual price only when **Future** is on.
  - Spike strip along the bottom: the bigger of jump and drop chance per quarter (orange jump, blue drop, darker is likelier); left of the line, spikes that happened.
  - Legend toggles: Actual, Predicted, Spot, Future (choices remembered in localStorage; `?hide=actual,best,spot`).
- **Zoom:** 0.5× (about 36 h) to 8× (about 2 h) around the fixed line. **− / 1× / +** buttons, keys `+` `-` `0`, Ctrl/Cmd + scroll or pinch. `?zoom=3`.
- **Top-alert banner** above the chart: time window, what it is, what changed, one thing to check.
  - Colour: green minimal imbalance, yellow imbalance expected, red high-price or low-price risk.
  - Order: severity, then rule (spike risk, revision, regulation, conflict, uncertainty, expert review), then spike chance. **‹ n of N ›** steps through alerts. Hover marks the alert window on the chart; click pins it.
  - Spike alerts show the peak time and rise since the last update. The check line says "short position" (high-price risk) or "long position" (low-price risk); the wording is ours.
  - **Minimal imbalance expected** shows when the mean |p50 − spot| over the next 2 hours is under 10 €/MWh and there is no spike, revision or conflict alert. Change it with `?noimb=<EUR/MWh>` (constant `NOIMB`).
- **Metrics sidebar** (Explore only, header button): tiles coloured green or red against the official organiser baseline (validation) or simple references (test), charts by horizon, by day and spike reliability, and **What drove the forecasts** (share of influence by six factor groups). Scope: whole run, day of this forecast, or the 7 or 30 days ending at the end of that day.
  - Definitions follow `starter_package_v1/score.py`; the validation numbers follow the official scorer (MAE 41.57, pinball 11.77, coverage 0.813, spike Brier 0.0905, direction Brier 0.393 for the final model).
  - Coverage is green at 80 % or above. Test-run metrics use outcomes shipped in the starter inputs and are display only.
  - Factor shares are model attribution (SHAP of the p50 and up-regulation models), not causal. They come from the same models that produced the forecast files.
- **Live tab** (`?run=live`): a simulated world and a toy forecaster that is **not** the real model. Every page load is its own session with its own clock: the server starts a fresh feed at the current hour (5 days of history before it), one simulated hour passes every `--live-tick` seconds (default 6; `0` removes the tab), and reloading the page restarts it. Sessions are keyed by a random `sid` the page sends with every `/live/api/*` call, and are dropped after 5 idle minutes (at most 24 at once). The speed button speeds only your own feed. A "Simulated data" pill is shown.

Speed: responses are gzip-compressed, static files carry an ETag (the browser gets a 304 instead of re-downloading on tab switches), and the page asks for the run list and timeline in parallel. Switching Explore to Live is a full page load; to hide the cost of building the simulated session, the Explore page calls `/live/api/warm?sid=<id>` in the background and the Live tab link carries that `sid` (used once, then removed from the URL, so a reload still starts a fresh clock). The simulated world for the current hour is built once and shared by all sessions started in that hour.

Debug parameters: `?snap=<ms>` holds the load event for headless screenshots, `?freeze=0.5` renders a half-way frame, `?metrics=1`, `?mxday=1`, `?alert=1&pin=1`, `?selftest=1`, `?livetest=1`.

## Deploy on Render

Create a Render Blueprint from this repository and select the branch you want to deploy.
The root `render.yaml` configures a paid Python web service in Frankfurt with a 1 GB
persistent disk. The site has no login: anyone who has the URL can open it, so only share the URL with
authorised people (the organisers have permitted this use). It sends `noindex` headers and a `robots.txt` so search
engines skip it. To add a login again, set `RADAR_PASSWORD` in Render (browser login: user `radar`, that password).

**Without SSH:** zip the files listed below (flat, no folders), host the zip at a private https link that downloads
directly (for example a Dropbox link ending in `?dl=1`, or an expiring pre-signed link), and set `RADAR_DATA_URL` to it in
Render. On start the server downloads and extracts the bundle into `RADAR_DATA_DIR` when required files are missing
(`fetch_data.py`; `RADAR_DATA_REFRESH=1` forces a new download). The link is never logged. The bundle contains licensed Nord
Pool data, so keep the link private, remove it from Render's settings after the first successful start, and delete the file
at the host after the event.

With SSH, instead transfer these files to `/var/data/` on the service using
[Render's SCP instructions](https://render.com/docs/disks#scp):

- `inputs.parquet`, `spot_nordpool.csv`, and `actuals_validation.csv` from the organizer's starter package.
- `forecast_validation.csv`, `forecast_test.csv`, `driver_shares_validation.parquet`, and
  `driver_shares_test.parquet` from this dashboard's `data/` directory.
- If you have the official validation baseline forecast CSV, upload it as
  `baseline_validation.csv` to enable official baseline comparisons.

Restart the service after the initial upload. Open its HTTPS URL, sign in, and
check both Explore runs, the Metrics drawer, and the Live tab. Share the URL and
URL with the hackathon audience. Pushes to the connected branch automatically
deploy dashboard code. To update the data or forecast files, replace them on the
disk and restart the service. Remove the service and its data disk after the event.
