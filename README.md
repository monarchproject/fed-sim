# Fed Chair Simulator - Render Package

This package is ready for GitHub + Render deployment.

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
- `fed_funds_futures_pricing.csv` - real public-data reconstruction of pre-FOMC market pricing using the San Francisco Fed U.S. Monetary Policy Event-Study Database.
- `USMPD_source_SF_Fed.xlsx` - source workbook kept for audit.

Gameplay simulation:

- player policy path
- credibility score
- approval score
- advisor comments
- market reaction cards
- achievements

The UI labels these as real macro data, real market-pricing data, or simulated player/game mechanics.

## Market pricing methodology

The bundled CSV reconstructs pre-FOMC market-implied policy pricing using:

```text
market_implied_bps = actual_fed_move_bps - announcement_surprise_bps
```

The surprise data comes from the San Francisco Fed U.S. Monetary Policy Event-Study Database. The app converts the implied move into approximate 25 bp probability buckets for gameplay display.

This is a real sourced reconstruction, not a licensed CME FedWatch archive. If a meeting is not covered by real public data, the app should show unavailable rather than inventing a row.

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
