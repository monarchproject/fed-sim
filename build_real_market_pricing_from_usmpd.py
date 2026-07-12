"""Rebuild fed_funds_futures_pricing.csv from the included SF Fed USMPD workbook.

This creates a diagnostic FOMC event-study dataset for the Fed simulator.
It uses the statement-window MP1 surprise in USMPD and official FOMC
target-rate changes embedded below. It is not direct FedWatch/ZQ pricing and
therefore must not be used as the market-pricing benchmark.

Formula:
    market_implied_bps = actual_fed_move_bps - announcement_surprise_bps

No probability distribution is generated here. Direct probability rows must come
from real fed-funds-futures/options/FedWatch-style data supplied separately.
"""
from __future__ import annotations

import ast
import csv
import json
import math
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
USMPD_FILE = ROOT / "USMPD_source_SF_Fed.xlsx"
MAIN_FILE = ROOT / "main.py"
OUT_FILE = ROOT / "fed_funds_futures_pricing.csv"

# Official target upper after selected FOMC decisions; update this table when new
# meetings become available in USMPD/FRED.
POST_TARGET_UPPER = {
    "2008-01-30": 3.00, "2008-03-18": 2.25, "2008-04-30": 2.00,
    "2008-06-25": 2.00, "2008-08-05": 2.00, "2008-09-16": 2.00,
    "2008-10-29": 1.00, "2008-12-16": 0.25,
    "2009-01-28": 0.25, "2009-03-18": 0.25, "2009-04-29": 0.25,
    "2009-06-24": 0.25, "2009-08-12": 0.25, "2009-09-23": 0.25,
    "2009-11-04": 0.25, "2009-12-16": 0.25,
    "2015-12-16": 0.50, "2016-12-14": 0.75, "2017-03-15": 1.00,
    "2017-06-14": 1.25, "2017-12-13": 1.50, "2018-03-21": 1.75,
    "2018-06-13": 2.00, "2018-09-26": 2.25, "2018-12-19": 2.50,
    "2019-07-31": 2.25, "2019-09-18": 2.00, "2019-10-30": 1.75,
    "2020-03-03": 1.25, "2020-03-15": 0.25,
    "2022-03-16": 0.50, "2022-05-04": 1.00,
    "2022-06-15": 1.75, "2022-07-27": 2.50, "2022-09-21": 3.25,
    "2022-11-02": 4.00, "2022-12-14": 4.50, "2023-02-01": 4.75,
    "2023-03-22": 5.00, "2023-05-03": 5.25, "2023-07-26": 5.50,
    "2024-09-18": 5.00, "2024-11-07": 4.75, "2024-12-18": 4.50,
    "2025-09-17": 4.25, "2025-10-29": 4.00, "2025-12-10": 3.75,
}
START_TARGET_UPPER = 3.50

def target_upper_before(meeting_date: str) -> float:
    """Official target upper immediately before a meeting date.

    This walks the full target timeline, including meetings outside the playable
    chair eras. It prevents the old bug where Powell 2020 inherited the 2014
    ZLB target because Yellen-era target changes were not emitted as rows.
    """
    md = pd.Timestamp(meeting_date)
    current = START_TARGET_UPPER
    for date_key, upper in sorted(POST_TARGET_UPPER.items(), key=lambda kv: kv[0]):
        if pd.Timestamp(date_key) < md:
            current = float(upper)
        else:
            break
    return current

def load_mapping() -> tuple[dict[str, set[str]], dict[str, str]]:
    text = MAIN_FILE.read_text(encoding="utf-8")
    dates_block = re.search(r"FOMC_DATES_BY_ERA\s*=\s*(\{.*?\n\})", text, re.S).group(1)
    exact_block = re.search(r"FOMC_EXACT_DATES\s*=\s*(\{.*?\n\})", text, re.S).group(1)
    by_era = ast.literal_eval(dates_block)
    exact = ast.literal_eval(exact_block)
    return by_era, exact

def main() -> None:
    if not USMPD_FILE.exists():
        raise FileNotFoundError(f"Missing {USMPD_FILE}")
    by_era, exact_dates = load_mapping()
    df = pd.read_excel(USMPD_FILE, sheet_name="Statements")
    df["date_key"] = pd.to_datetime(df["Date"]).dt.strftime("%Y-%m-%d")
    statement = df.set_index("date_key")

    rows = []
    for era, months in by_era.items():
        for ym in sorted(months):
            md = exact_dates.get(ym)
            if not md or md not in statement.index:
                continue
            s = statement.loc[md]
            pre_upper = target_upper_before(md)
            post_upper = POST_TARGET_UPPER.get(md, pre_upper)
            actual_move_bps = (post_upper - pre_upper) * 100.0
            mp1 = float(s.get("MP1", 0) or 0)
            surprise_bps = mp1 * 100.0
            market_bps = actual_move_bps - surprise_bps
            rows.append({
                "era": era,
                "ym": ym,
                "meeting_date": md,
                "asof_date": (pd.Timestamp(md) - pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
                "status": "diagnostic",
                "quality": "sf_fed_usmpd_event_study_not_direct_futures",
                "source": "San Francisco Fed USMPD event-study surprise + official target move; diagnostic only, not direct FedWatch/ZQ pricing",
                "contract": "Fed funds futures / MP1",
                "price": "",
                "implied_avg_rate": "",
                "pre_meeting_rate": round(pre_upper, 4),
                "expected_post_rate": round(pre_upper + market_bps / 100.0, 4),
                "market_implied_bps": round(market_bps, 1),
                "outcomes_json": "",
                "reason": "",
                "actual_move_bps": round(actual_move_bps, 1),
                "announcement_surprise_bps": round(surprise_bps, 2),
                "post_target_upper": round(post_upper, 4),
                "mp1_pct_points": round(mp1, 6),
                "mp2_pct_points": round(float(s.get("MP2", 0) or 0), 6),
                "sp500_30min_pct": round(float(s.get("SP500", 0) or 0), 6),
                "ust2y_30min_pctpt": round(float(s.get("UST2Y", 0) or 0), 6),
            })

    fieldnames = list(rows[0])
    with OUT_FILE.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows to {OUT_FILE}")

if __name__ == "__main__":
    main()
