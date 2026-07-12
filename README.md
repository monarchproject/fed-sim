# Fed Chair Simulator - Render Package
### Final deep-audit scoring/data cleanup

- Removed achievement score bonuses; achievements are cosmetic only.
- Replaced bucket-based monthly scoring with a shared continuous dual-mandate loss function for both player and real Fed.
- Removed diagnostic USMPD probability buckets from `fed_funds_futures_pricing.csv`; diagnostic rows cannot masquerade as FedWatch probabilities.
- Added Chart.js annotation plugin so chart reference lines render.


## Deep strict-real-data audit changes

This build uses a bundled official CSV macro snapshot before attempting live FRED. Earlier builds missed `2025-10`, which could hide an FOMC decision; this package includes a continuous offline CSV panel and flags incomplete macro rows with `macroDataComplete=false` instead of silently inventing fresh data.

Official target-rate refresh uses `DFEDTAR` for the discontinued single target-rate series and `DFEDTARU` / `DFEDTARL` for the post-2008 target range. Monthly `FEDFUNDS` averages are never used as real FOMC decisions.

Market pricing is stricter: diagnostic USMPD/event-study rows do not count as market pricing and do not block live direct-futures fetches. If pre-meeting EFFR, same-month ZQ decomposition, or a direct user-provided market row is unavailable, the market benchmark is shown as `N/A`. The app no longer decomposes a single futures average into fake FedWatch-style probability buckets.

QE/QT and forward guidance are available in every month. They are separate channels; rate buttons remain FOMC-only. Macro/scoring randomness has been removed so identical decisions produce the same score-relevant path.

See `DEEP_AUDIT_REPORT.md` for the full audit list.

This package is ready for local use, GitHub, and Render deployment.


## Dynamic policy preview fix

The old decision preview was a static IRF multiplication, so the same button could always show the same effect and unstable small-sample VAR regimes could show absurd values such as a +100bp hike causing roughly -12% growth.

The preview now runs an 18-month counterfactual path against a no-action baseline using the current player state, current historical month, selected rate move, QE/QT stock-flow state, guidance tone, FCI coefficients, and the stabilized IRF channel. It is a dynamic path preview, not a fixed constant.

## Deploy on Render

Use these settings if Render asks for them manually:

```text
Runtime: Python 3
Build Command: python -m pip install --upgrade pip && python -m pip install -r requirements.txt
Start Command: python -m gunicorn main:app --bind 0.0.0.0:$PORT --timeout 120 --workers 1
Environment Variable: PYTHON_VERSION = 3.11.11
Health Check Path: /api/health
```

Do not type `Build Command:` or `Start Command:` inside the field. Only paste the command itself.

## Required files in repo root

```text
main.py
index.html
requirements.txt
render.yaml
Procfile
.python-version
runtime.txt
macro_dataset_v3.json
irf_v3.json
fed_funds_futures_pricing.csv
```

`data/processed/` is only a runtime cache folder. It can be empty. The `.gitkeep` files keep the folder visible in GitHub.

## If you see `ModuleNotFoundError: No module named 'flask'`

That means dependencies were not installed before the app started. Check:

1. `requirements.txt` is in the repo root.
2. Render Build Command is exactly:

```text
python -m pip install --upgrade pip && python -m pip install -r requirements.txt
```

3. Render Start Command is exactly:

```text
python -m gunicorn main:app --bind 0.0.0.0:$PORT --timeout 120 --workers 1
```

4. Render logs must show `Successfully installed Flask` before startup.
5. Render logs must show Python `3.11.11`, not Python `3.14.x`.

## Local Windows launch

Double-click:

```text
RUN_FED_GAME.bat
```

or:

```text
RUN_LOCAL_WINDOWS_PY311.bat
```

The launcher intentionally uses Python 3.11. This matters because Python 3.14 can force pandas/statsmodels to build from source on Windows, which fails if Visual Studio build tools are missing.

If you see a pandas Meson or Visual Studio error, install Python 3.11.11, then run the launcher again:

```text
https://www.python.org/downloads/release/python-31111/
```

The launcher also rebuilds an old `.venv` if it was accidentally created with Python 3.14.

## Real data vs simulation

Real/data-based files:

- `macro_dataset_v3.json` - macro data cache from FRED/public official-series sources.
- `irf_v3.json` - model impulse-response cache.
- `fed_funds_futures_pricing.csv` - San Francisco Fed USMPD event-study diagnostics. It is kept for audit and actual-move diagnostics, but it is not used as a direct market-pricing benchmark in strict mode.
- `USMPD_source_SF_Fed.xlsx` - source workbook kept for audit.

Gameplay simulation:

- player policy path
- credibility score
- approval score
- advisor comments
- market reaction cards
- achievements

The UI labels these as official macro/target-rate data, direct market-pricing data, unavailable data, or simulated player/game mechanics.

## Real decision and market-pricing methodology

Real Fed decisions are derived from official target-rate data, not from monthly average FEDFUNDS. Runtime refresh uses FRED `DFEDTAR` for the discontinued single target rate and `DFEDTARL` / `DFEDTARU` for the post-2008 target range. Target changes are rounded to standard 25 bp policy increments, so fake monthly-average artifacts such as +127 bp or -16 bp are not shown as FOMC decisions.

Market pricing is strict: the app uses direct pre-FOMC fed-funds-futures pricing only. The bundled USMPD file is an event-study diagnostic, not a FedWatch archive. If no direct market row exists, the market benchmark displays `N/A`; the app does not create 2Y/FEDFUNDS proxies.

## Balance-sheet / QE-QT methodology

QE and QT remain available throughout the simulation, including non-FOMC months. They are not treated as real official Fed-decision data; they are part of the counterfactual game model. QE/QT is no longer converted into fed-funds basis points and no longer offsets rate moves 1-for-1. A hike plus QE is modeled as short-rate tightening plus long-end/liquidity easing. A cut plus QT is modeled as short-rate easing plus liquidity withdrawal.

Balance-sheet actions now move a separate stock/flow state. That state affects term premium, liquidity, and the financial-conditions channel. It does not directly add score, subtract score, or enter the Taylor-rule score as a fake funds-rate equivalent. Score comes from realized macro outcomes, financial-stability outcomes, and the actual funds-rate discipline versus the Taylor benchmark.

Interactive crisis choices no longer contain direct `scoreBonus` points. They are narrative/strategy markers only; macro movement comes from real-data shocks and the transmission model, and score is earned from outcomes.

The player policy rate has no arbitrary upper cap in the frontend. It is floored at zero.

## Latest package cleanup

- Consolidated all documentation into this single README.
- Removed extra `.md` files.
- Made Render commands use `python -m pip` and `python -m gunicorn` to avoid environment/path mismatches.
- Kept the creative cockpit UI, chart tabs, real market-pricing CSV, and source audit panel.


## Compact 4-column cockpit layout

- Code-level UI patch, not an image mockup.
- Wide desktop screens now use controls + compact chart studio + two intelligence columns.
- Macro charts are reduced and placed in a two-column chart grid.
- Right-side panels are wrapped into compact cards to reduce scrolling.
- Render start command remains: `python -m gunicorn main:app --bind 0.0.0.0:$PORT --timeout 120 --workers 1`.

## Latest cockpit fixes

- Real Fed decisions no longer use monthly average FEDFUNDS deltas; impossible +127 bp style artifacts are removed.
- Direct market-pricing rows are required for market benchmarks. USMPD event-study rows are diagnostics only.
- QE/QT is available every month. It is not rejected, hidden, or directly penalized by shortcut rules.
- Balance-sheet actions are scored only through modeled outcomes. QE/QT changes a separate balance-sheet stock/flow state that affects term premium, liquidity, and FCI; it is not converted into a shadow funds-rate or netted against hikes/cuts.
- Direct event `scoreBonus` rewards were removed; event choices are narrative/strategy markers only, and score comes from outcomes.
- The right intelligence panel now uses the empty space below Taylor Rule by allowing scorecard, approvals, achievements, and event log cards to fill the second column instead of forcing extra scrolling.

Render start command:

```text
python -m gunicorn main:app --bind 0.0.0.0:$PORT --timeout 120 --workers 1
```

### Strict realism cleanup

This build removes direct outcome bonuses from crisis choices. Event choices do not mechanically add/subtract inflation, growth, unemployment, approval, or score. Historical shocks are estimated from observed macro data movements, and real Fed decisions are displayed only when an official target-rate move is available.

## Offline official CSV data snapshot

The simulator now ships with official-data CSV snapshots under:

```text
data/official/macro_dataset_v3.csv
data/official/official_fomc_decisions.csv
data/official/DATA_MANIFEST.csv
```

The backend loads `data/official/macro_dataset_v3.csv` before attempting live FRED. Normal gameplay therefore does not require internet access. Live FRED refresh is only needed when you intentionally want a newer data snapshot.

To refresh the CSVs on an internet-connected machine:

```text
REFRESH_OFFICIAL_DATA_CSV.bat
```

or:

```bash
python download_fred_data_to_csv.py
```

The script downloads raw FRED CSVs into `data/official/fred_raw/`, rebuilds the processed macro panel CSV, and refreshes the model cache.

Important: some official FRED table rows can be missing (`.`). For example, the bundled 2025-10 row marks CPI/core CPI/unemployment as not complete and uses the latest official available macro prints while preserving the official FOMC target move. These rows are flagged with `macroDataComplete=false` and a `dataQualityNote`.


## Urgent NaN stability hotfix

- Fixed a broken `clamp()` call in the yield-spread update: it missed the lower-bound comma and passed only two arguments, causing player yield spread to become `NaN`.
- Added frontend state/model sanitizers so one missing model/csv field cannot contaminate inflation, unemployment, growth, rate, preview, charts, and score.
- Bumped cache version to reject stale `data/processed` runtime caches from earlier broken builds.
- No fake Fed decisions or proxy market-pricing rows were added.
