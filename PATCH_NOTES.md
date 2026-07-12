# Deep strict-real-data audit patch
### Final deep-audit scoring/data cleanup

- Removed achievement score bonuses; achievements are cosmetic only.
- Replaced bucket-based monthly scoring with a shared continuous dual-mandate loss function for both player and real Fed.
- Removed diagnostic USMPD probability buckets from `fed_funds_futures_pricing.csv`; diagnostic rows cannot masquerade as FedWatch probabilities.
- Added Chart.js annotation plugin so chart reference lines render.

- Corrected official target-rate live refresh from `FEDTAR` to `DFEDTAR`; `DFEDTARU` / `DFEDTARL` remain the post-2008 target-range sources.
- Added strict monthly cache validation; incomplete cached macro panels such as the bundled file missing `2025-10` are rejected instead of silently skipping FOMC months.
- Fixed broken market-pricing CSV builder by replacing undefined `ERA_FOMC_MONTHS` with `FOMC_DATES_BY_ERA`.
- Diagnostic USMPD rows no longer block live direct-futures market-pricing fetches.
- Removed monthly `FEDFUNDS` fallback and next-month ZQ proxy from strict market-pricing mode.
- Removed fabricated probability buckets from live futures-average pricing.
- QE/QT and forward guidance now remain usable in non-FOMC months; only rate buttons are meeting-only.
- Removed zero-bps tone shocks from the rate-shock pipeline; tone now enters only the expectations channel.
- `nextMonth()` now waits for market-pricing fetch completion on FOMC months before logging/reaction calculations.
- Fixed FCI math to use observed 2Y plus player-vs-real policy gap and to include the dollar term in the neutral FCI.
- Removed random noise from macro/scoring state; randomness remains only for quote selection.
- Removed silent synthetic model fallback in strict mode.

# Fed Chair Simulator Patch Notes

## Strict real-data rebuild patch
- Replaced fake `realDecision` logic based on monthly average FEDFUNDS with official target-rate decision logic. Runtime refresh now uses FRED `DFEDTAR`, `DFEDTARL`, and `DFEDTARU`.
- Sanitized old bundled cache rows so non-25bp artifacts become unavailable instead of showing wrong Fed decisions.
- Updated bundled Powell/Bernanke actual moves from the official target-move table; examples: Jun 2022 = +75bp, Sep 2024 = -50bp, Mar 2020 = -100bp for the mapped emergency decision.
- Removed 2Y/FEDFUNDS proxy fallback from market pricing. If direct fed-funds-futures pricing is unavailable, the UI shows N/A.
- Marked the San Francisco Fed USMPD CSV as event-study diagnostic data, not direct FedWatch/ZQ pricing.
- Removed the earlier shortcut guardrails. QE/QT is available every month and is no longer rejected or directly penalized.
- Rebuilt balance-sheet scoring again: QE/QT is no longer converted into a shadow-rate impulse and no longer nets 1-for-1 against hikes/cuts. It now moves a separate balance-sheet stock/flow state affecting term premium, liquidity, and FCI.
- Removed direct event `scoreBonus` scoring and removed `scoreBonus` fields from crisis choices. Event choices affect macro/approval states; score is earned through subsequent outcomes.
- Removed the arbitrary policy-rate upper cap. The funds rate is floored at zero but no longer capped at 22% or 25%.

## Fixed stall / freeze bugs
- Fixed the reaction-engine `ReferenceError: zUnempChange is not defined` that could stop the month-advance flow after enough months.
- Added a UI fail-safe so non-critical reaction/rendering errors re-enable the Next button instead of trapping the game.
- Wrapped stakeholder/comment reaction calls so a quote/reaction bug cannot stop the simulation engine.
- Added a fallback button inside crisis event modals: `CONTINUE WITHOUT SPECIAL RESPONSE`.
- Changed chart updates to `update('none')` to reduce UI lag during long terms.

## Fixed data / freshness issues
- Fixed `/api/refresh`: it now forces a live FRED rebuild instead of deleting runtime cache and silently falling back to the bundled dataset.
- Added strict JSON-safe cache writing so `NaN` values are converted to valid JSON nulls.
- Cleaned the bundled `macro_dataset_v3.json` and `irf_v3.json` so they parse as strict JSON.
- Added detailed `/api/health` output: rows, latestDate, bundled/runtime cache flags, cacheVersion.
- Added response metadata to `/api/era/<era>`: startDate, endDate, monthCount, meetingCount, eventCount.
- Updated missing Powell-era FOMC meeting months for 2025 and 2026. This fixes the issue where recent Powell gameplay had too many dead months with no rate decision.

## Deployment / local run
- Added `RUN_FED_GAME.bat` for Windows local launch.
- Backend now respects the `PORT` environment variable and binds to `0.0.0.0` when run directly.

## Validation performed
- Python syntax check passed for `main.py`.
- JavaScript syntax check passed for the extracted browser script.
- Flask test-client checks returned valid data for Volcker, Greenspan, Bernanke, and Powell eras.
- Headless JS simulation advanced through complete Volcker, Greenspan, Bernanke, and Powell terms without runtime stalls.

## 2026-07-08 strict model cleanup

- Removed all direct `inflationBonus`, `growthBonus`, unemployment, approval, and score effects from interactive crisis choices.
- Interactive events are now narrative/strategy markers only; they do not mechanically subtract or add inflation.
- Removed hardcoded historical event effect constants from the backend API payload.
- Historical event shocks are now computed in the frontend from observed real macro surprises in the loaded data: current monthly change minus trailing six-month average change, capped by empirical monthly-diff volatility.
- Removed direct QE/QT CPI drift. Balance-sheet policy now affects term premium/liquidity/FCI first; inflation can only move later through model transmission, not through a constant monthly add/subtract.
- Real Fed decision display is now gated to official target-rate sources only. Cached/sanitized FEDFUNDS-average artifacts are hidden as N/A.
- Bundled macro cache was sanitized so non-official realDecision rows are N/A; bundled official target move rows remain visible.

## Offline CSV data snapshot fix

User requested that official data be bundled as CSV instead of requiring a live FRED fetch at runtime.

Changes:
- Added `data/official/macro_dataset_v3.csv` as the primary runtime data source.
- Added `data/official/official_fomc_decisions.csv` for transparent Real Fed decision overlay.
- Added `data/official/DATA_MANIFEST.csv` describing each bundled CSV and source role.
- Backend now loads the official CSV snapshot before falling back to bundled JSON or live FRED.
- Added `download_fred_data_to_csv.py` and `REFRESH_OFFICIAL_DATA_CSV.bat` to refresh FRED CSV files intentionally, not during normal gameplay.
- Added `macroDataComplete` and `dataQualityNote` fields in API output.
- Filled the previously missing 2025-10 panel row using official target move and available official series; CPI/core CPI/unemployment are flagged as missing in FRED table data and carried from latest available prints, not treated as a fresh monthly release.

Runtime rule now:
1. Use local runtime cache if user explicitly refreshed.
2. Use bundled official CSV snapshot.
3. Use bundled JSON compatibility snapshot.
4. Only then attempt live FRED.

This means no silent internet dependency for normal startup.

## 2026-07-08 offline/dynamic-model correction

- Fixed a cache-version bug: backend expected `v6` while bundled `irf_v3.json` was `v5`, so the app skipped the local files and tried live FRED. It now loads the bundled offline CSV/model immediately.
- Bundled FRED-derived macro data as `data/official/macro_dataset_v3.csv`; normal startup no longer requires live internet.
- Bundled official target-move overlay as `data/official/official_fomc_decisions.csv` so the Real Fed decision column is visible offline.
- Trimmed decision/pricing CSV rows beyond the bundled macro-data end month (`2026-04`) so the package does not imply actual decisions for months not in the bundled macro panel.
- Replaced the static “Peak IRF effect” display with a dynamic 18-month counterfactual path preview versus a no-action baseline.
- The preview now changes with current inflation/unemployment/growth, player rate, balance-sheet stock, current regime, FCI coefficients, future historical months available in the loaded era, guidance tone, and QE/QT selection.
- Added an IRF stability guard: small-sample regime VAR channels with peak responses more than 2x the full-sample channel are replaced with full-sample estimates. This removes absurd outputs like +100bp showing ~-12% growth.
- Forward projection wording changed from fixed IRF peak to “dynamic peak path difference” to avoid implying a constant mechanical effect.


## Urgent NaN stability hotfix

- Fixed a broken `clamp()` call in the yield-spread update: it missed the lower-bound comma and passed only two arguments, causing player yield spread to become `NaN`.
- Added frontend state/model sanitizers so one missing model/csv field cannot contaminate inflation, unemployment, growth, rate, preview, charts, and score.
- Bumped cache version to reject stale `data/processed` runtime caches from earlier broken builds.
- No fake Fed decisions or proxy market-pricing rows were added.
