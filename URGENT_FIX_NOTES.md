# Urgent stability fix

This build fixes the user-visible NaN breakage shown after selecting a rate move.

Root cause found:

- `state.player.yieldSpread = clamp(...)` had a missing comma in `applyPolicy()`.
- The call was effectively `clamp(value - 3.5, 4.5)` instead of `clamp(value, -3.5, 4.5)`.
- Since the custom `clamp()` expected three arguments, the upper bound became `undefined`, returning `NaN`.
- Yield-spread NaN then contaminated FCI, expectations, projection, growth, unemployment, inflation, charts, and scoreboard.

Fixes:

1. Corrected the broken yield-spread clamp call.
2. Replaced the fragile `clamp()` helper with a NaN-safe version.
3. Added state/model sanitizers so one missing field cannot destroy the whole game state.
4. Fixed metric rendering so it shows `N/A` instead of `NaN%` if a source value is actually unavailable.
5. Guarded dynamic projection formatting so preview cannot print `NaNpp`.

No fake Fed decisions or proxy market pricing were added.
