# Deep Audit Report - Fed Chair Simulator

This audit focused on logical correctness, hidden assumptions, hardcoded shortcuts, stale data hazards, and user-visible bugs.

## Critical issues fixed

1. **Wrong official target-rate FRED series**
   - Bug: the backend requested `FEDTAR`, but the FRED discontinued target-rate series id is `DFEDTAR`.
   - Impact: live refresh could not reconstruct older official Fed decisions, causing `Real Fed` to show `N/A` even when official target data exists.
   - Fix: runtime refresh now fetches `DFEDTAR`, `DFEDTARL`, and `DFEDTARU`; old `FEDTAR` is accepted only for backward compatibility with old caches.

2. **Bundled cache skipped an entire month**
   - Bug: `macro_dataset_v3.json` is missing `2025-10`.
   - Impact: the simulation silently jumps over October 2025 and hides the official October 2025 FOMC decision.
   - Fix: strict cache validation now rejects any runtime/bundled macro cache with missing monthly rows and forces a live FRED rebuild rather than silently using incomplete data.

3. **Market-pricing CSV builder was broken**
   - Bug: `_iter_meeting_months_for_csv()` referenced undefined `ERA_FOMC_MONTHS`.
   - Impact: `/api/market-pricing-csv/build` failed.
   - Fix: replaced with `FOMC_DATES_BY_ERA`.

4. **Diagnostic USMPD rows blocked real market-pricing fetches**
   - Bug: bundled diagnostic rows were returned as `unavailable` immediately, blocking a live direct-futures fetch.
   - Impact: direct market pricing could stay unavailable even when live data might be fetchable.
   - Fix: non-strict diagnostic CSV rows are ignored for market pricing; they may still carry official actual-move metadata for real Fed decisions.

5. **Monthly FEDFUNDS fallback still existed inside strict market pricing**
   - Bug: if EFFR failed, the market-pricing path used monthly `FEDFUNDS` as a pre-meeting rate fallback.
   - Impact: this reintroduced a proxy into strict mode.
   - Fix: removed fallback. If pre-meeting EFFR cannot be fetched, market pricing is `N/A`.

6. **Near-month-end ZQ proxy removed**
   - Bug: if a meeting was near month-end, code used next-month ZQ as a proxy.
   - Impact: that is not the same contract decomposition and could misstate market-implied decision pricing.
   - Fix: strict mode refuses this case and returns `N/A`.

7. **Fake probability buckets removed from live futures average**
   - Bug: a single fed-funds-futures implied average was converted into adjacent 25 bp “probability” buckets.
   - Impact: futures average was displayed like a real FedWatch probability distribution.
   - Fix: live ZQ fetch now returns implied move only. Probability buckets display only if a direct user-provided source supplies them.

8. **QE/QT was still disabled outside FOMC months**
   - Bug: frontend disabled `.qe-btn` and `.tone-btn` on non-meeting months.
   - Impact: contradicted the intended design where balance-sheet policy can operate every month.
   - Fix: rate buttons are disabled outside FOMC months, but QE/QT and tone remain usable.

9. **Tone created zero-bps “policy shocks”**
   - Bug: changing tone without a rate move pushed a zero-size shock into the VAR shock pipeline.
   - Impact: lag gates could be reset even though no rate shock occurred.
   - Fix: only actual rate moves create rate shocks. Tone enters the expectations channel directly.

10. **Async market-pricing race condition**
    - Bug: clicking advance could log the decision before the market-pricing fetch finished.
    - Impact: decision ledger and market reaction could show `N/A` or zero surprise even when data arrived milliseconds later.
    - Fix: `nextMonth()` now awaits the market-pricing promise on FOMC months before applying/logging the decision.

11. **FCI shortcut fixed**
    - Bug: player 2Y yield used `player rate + 0.3pp`, and the neutral FCI omitted the dollar term.
    - Impact: FCI could move for reasons unrelated to the player's policy divergence.
    - Fix: player 2Y now starts from observed 2Y and shifts by player-vs-real funds-rate gap. Neutral FCI includes observed 2Y, spread, and dollar components.

12. **Wrong growth concept used in multiple places**
    - Bug: player growth was monthly industrial-production momentum, but some Fed-score and mean-reversion code still used YoY `growth`.
    - Impact: recession/score/mean-reversion channels mixed incompatible growth units.
    - Fix: those paths now use `indpro_m ?? growth` consistently.

13. **Randomness removed from scoring/macro state**
    - Bug: random noise directly affected yield spread and approvals.
    - Impact: identical decisions could produce different score-relevant paths.
    - Fix: macro/scoring state is deterministic. Randomness remains only for cosmetic quote selection.

14. **Silent synthetic model fallback removed**
    - Bug: if model estimation failed, backend returned a hardcoded fallback IRF model.
    - Impact: app could look data-driven while using synthetic parameters.
    - Fix: strict mode raises an error instead of using synthetic fallback parameters.

## Remaining honest limitations

- QE/QT counterfactual effects are necessarily model-based because there is no official dataset for every alternate player balance-sheet path.
- Old Volcker months before official target-rate data availability remain `N/A` unless an official historical target table is supplied.
- The bundled macro cache is intentionally rejected because it is incomplete. A live FRED refresh is required for a clean strict-real-data run.
- Direct market pricing is only available where the direct futures source can be fetched or where the user supplies licensed/direct rows. The app no longer fabricates missing market pricing.

## Additional fixes from the final pass

19. **Achievement score bonus removed**
    - Bug: achievements still added fixed score points even after event score bonuses were removed.
    - Impact: score could improve because a label unlocked, not because the macro path improved.
    - Fix: achievements are now cosmetic/logging only. Score changes only through the monthly mandate loss function.

20. **Bucket-based monthly score replaced**
    - Bug: monthly scoring used branch bonuses such as narrow inflation/unemployment bands.
    - Impact: small threshold crossings created discontinuous score jumps and hidden incentives.
    - Fix: both the player and the real Fed now use the same continuous dual-mandate loss function, scaled by empirical standard deviations where available.

21. **Diagnostic USMPD probability buckets removed**
    - Bug: the USMPD diagnostic builder generated adjacent 25bp probability buckets from an event-study implied move.
    - Impact: diagnostic event-study rows could be mistaken for real FedWatch/ZQ probability distributions.
    - Fix: diagnostic CSV rows now leave `outcomes_json` blank. Direct probability distributions must come from real market-pricing data.

22. **Chart annotation plugin loaded**
    - Bug: chart configs included annotation lines, but the Chart.js annotation plugin was not loaded.
    - Impact: target/reference lines could silently fail to render.
    - Fix: added the Chart.js annotation plugin and safe registration.

## Follow-up audit: offline data and dynamic preview

### Issue: local data still fell through to live FRED
The code expected cache version `v6`, but the bundled model file was tagged `v5`. As a result, the local bundle was rejected and the server tried to fetch FRED data even when local data existed.

**Fix:** cache version now matches the bundled model. The backend first loads `data/official/macro_dataset_v3.csv`, then bundled JSON as fallback, and only then live FRED.

### Issue: Real Fed decisions were hidden when FRED was unavailable
The previous build required runtime FRED refresh for the cleanest target-rate overlay. That was not necessary: official target moves can be bundled in a transparent CSV.

**Fix:** added `data/official/official_fomc_decisions.csv` and overlay it on the offline monthly panel. The UI now receives `realDecisionAvailable=true` for rows where official target moves are present.

### Issue: static IRF preview produced absurd growth values
The preview directly multiplied raw regime IRFs by the selected bps. Some small-sample regime VARs had explosive growth/unemployment coefficients, so +100bp could show roughly -12% growth.

**Fix:**
- Added a stability guard to replace unstable small-sample regime channels with the full-sample IRF for that variable.
- Replaced the static display with a dynamic 18-month counterfactual simulation versus a no-action baseline.
- The displayed preview now depends on the current state, rate gap, QE/QT stock/flow, tone, FCI model, and future months in the selected era rather than being constant for a button.

### Remaining limitation
The local macro CSV is a frozen snapshot. Running `REFRESH_OFFICIAL_DATA_CSV.bat` on a machine with internet updates the official CSV snapshot intentionally. The app no longer tries to silently invent or proxy missing data at normal startup.
