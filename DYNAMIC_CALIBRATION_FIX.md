# Dynamic calibration fix

This build removes the economic hardcoded constants that were still driving scoring/QE preview.

## Removed/replaced

- `INFLATION_TARGET = 2.0` -> runtime inflation anchor estimated from the loaded era data / model calibration.
- `UNEMPLOYMENT_REFERENCE = 5.0` -> runtime unemployment anchor estimated from the loaded era data / model calibration.
- `POLICY_STEP_BPS = 25` -> median absolute official FOMC target move in the loaded era; fallback only if no official moves are loaded.
- `BALANCE_SHEET_STOCK_DECAY = 0.985` -> decay estimated from the median spacing of FOMC meetings in the loaded era.
- `BALANCE_SHEET_STOCK_SCALE_MONTHS = 12` -> derived from meeting spacing.
- `BS_TERM_PREMIUM_MAX_PP = 0.90` -> derived from empirical 2Y / yield-spread volatility in the loaded era.
- `BS_FCI_MAX_PP = 1.20` -> derived from empirical FCI volatility from the loaded era.
- fixed score multiplier -> monthly score is now based on where the player loss sits versus the real historical Fed-path loss distribution in that era.
- fixed state bounds like `inflation [-3,25]`, `growth [-15,14]`, `unemployment [2,16]` -> empirical era bounds with robust padding.

## Backend changes

- Expectations coefficients are no longer clipped to fixed ranges. Non-finite estimates fail strict mode instead of silently using synthetic constants.
- FCI coefficients are no longer clipped to fixed ranges.
- Okun coefficient is estimated from the panel instead of fixed at `0.5`.
- Taylor-like reaction anchors are estimated from the loaded sample instead of assuming `2% inflation` and `5% unemployment`.
- A `policyCalibration` block is exported with data-derived anchors, empirical bounds and series volatilities.

## What remains constant

There are still UI/layout constants, button labels, CSS values, and numerical safety guards such as `EPS`. Those are not economic model assumptions.
