"""
Download official FRED source series into CSV and rebuild the bundled offline dataset.

Run this only when you want to refresh the packaged data snapshot:
    py -3.11 download_fred_data_to_csv.py

The app itself does not need internet at runtime after this CSV snapshot exists.
"""
from pathlib import Path
from io import StringIO
import json
import math
import requests
import pandas as pd
import numpy as np

import main

ROOT = Path(__file__).parent
OFFICIAL_DIR = ROOT / "data" / "official"
RAW_DIR = OFFICIAL_DIR / "fred_raw"
OFFICIAL_DIR.mkdir(parents=True, exist_ok=True)
RAW_DIR.mkdir(parents=True, exist_ok=True)

FRED_SERIES = [
    "CPIAUCSL", "CPILFESL", "UNRATE", "FEDFUNDS", "EFFR",
    "INDPRO", "GDPC1", "DGS2", "DGS10", "PAYEMS", "DTWEXBGS",
    "DFEDTAR", "DFEDTARL", "DFEDTARU",
]


def clean_records(df: pd.DataFrame):
    out = df.replace({np.nan: None}).to_dict(orient="records")
    for rec in out:
        for k, v in list(rec.items()):
            if isinstance(v, float) and not math.isfinite(v):
                rec[k] = None
    return out


def download_fred_series(series_id: str, start: str = "1978-01-01") -> pd.DataFrame:
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}&cosd={start}"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    df = pd.read_csv(StringIO(r.text))
    out = RAW_DIR / f"{series_id}.csv"
    df.to_csv(out, index=False)
    print(f"saved {series_id}: {len(df)} rows -> {out}")
    return df


def main_download():
    for sid in FRED_SERIES:
        try:
            download_fred_series(sid)
        except Exception as e:
            print(f"WARNING: failed to download {sid}: {e}")

    # Rebuild processed panel and model using the same backend functions the app uses.
    panel = main.build_macro_panel()
    if "macroDataComplete" not in panel.columns:
        panel["macroDataComplete"] = True
    if "dataQualityNote" not in panel.columns:
        panel["dataQualityNote"] = "refreshed from FRED CSV download"

    panel_csv = panel.reset_index().rename(columns={"index": "date"})
    panel_csv["date"] = pd.to_datetime(panel_csv["date"]).dt.strftime("%Y-%m-%d")
    panel_csv.to_csv(OFFICIAL_DIR / "macro_dataset_v3.csv", index=False)
    with open(ROOT / "macro_dataset_v3.json", "w", encoding="utf-8") as f:
        json.dump(clean_records(panel_csv), f, indent=2, allow_nan=False)

    model = main.estimate_full_model(panel)
    model["cacheVersion"] = main.CACHE_VERSION
    with open(ROOT / "irf_v3.json", "w", encoding="utf-8") as f:
        json.dump(main._clean_for_json(model), f, indent=2, allow_nan=False)

    # Store official decisions in a standalone CSV for transparent Real Fed display.
    decisions = []
    for date, row in panel.iterrows():
        if pd.notna(row.get("realDecision", np.nan)):
            decisions.append({
                "ym": pd.Timestamp(date).strftime("%Y-%m"),
                "meeting_date": "",
                "actual_move_bps": float(row.get("realDecision")) * 100.0,
                "post_target_upper": row.get("policyTargetUpper", row.get("policyTargetMid", None)),
                "decision_source": row.get("realDecisionSource", "FRED official target-rate series"),
            })
    pd.DataFrame(decisions).to_csv(OFFICIAL_DIR / "official_fomc_decisions.csv", index=False)
    print(f"processed panel rows: {len(panel_csv)}")
    print("done: runtime can now use data/official/macro_dataset_v3.csv and official_fomc_decisions.csv without internet")


if __name__ == "__main__":
    main_download()
