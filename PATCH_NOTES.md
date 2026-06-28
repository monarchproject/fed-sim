# Fed Chair Simulator Patch Notes

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
