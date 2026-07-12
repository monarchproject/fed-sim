"""Generate fed_funds_futures_pricing.csv from real market data.

This script uses the same backend logic as the Fed Chair Simulator:
- 30-Day Fed Funds futures (ZQ) historical close from Yahoo Finance chart data
- Effective Federal Funds Rate (EFFR) from FRED

It never fabricates market pricing. Failed rows are skipped by default; use
--include-unavailable to write diagnostic rows with reasons.
"""

import argparse
from pathlib import Path
from main import build_market_pricing_csv_rows, save_market_pricing_csv


def main():
    parser = argparse.ArgumentParser(description="Build real pre-FOMC market-pricing CSV")
    parser.add_argument("--era", default="powell", help="Era to build: powell, bernanke, greenspan, volcker, or all")
    parser.add_argument("--start", default=None, help="Start month YYYY-MM")
    parser.add_argument("--end", default=None, help="End month YYYY-MM")
    parser.add_argument("--include-unavailable", action="store_true", help="Write unavailable rows with reasons")
    parser.add_argument("--include-future", action="store_true", help="Try future meetings too")
    parser.add_argument("--refresh", action="store_true", help="Clear market-pricing cache first")
    args = parser.parse_args()

    era_filter = None if args.era.lower() == "all" else args.era.lower()
    rows, counts = build_market_pricing_csv_rows(
        era_filter=era_filter,
        start_ym=args.start,
        end_ym=args.end,
        include_unavailable=args.include_unavailable,
        refresh=args.refresh,
        include_future=args.include_future,
    )
    root_path, runtime_path = save_market_pricing_csv(rows)
    print(f"Wrote {len(rows)} rows to {root_path}")
    print(f"Runtime copy: {runtime_path}")
    print(f"Status counts: {counts}")
    if len(rows) == 0:
        print("No rows were written. Check internet access, Yahoo/FRED availability, or use --include-unavailable for diagnostics.")


if __name__ == "__main__":
    main()
