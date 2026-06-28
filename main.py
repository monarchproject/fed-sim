"""
FED CHAIR SIMULATOR — Backend
main.py (renamed from app.py for Vercel deployment)
"""

from flask import Flask, jsonify, send_from_directory, request
from flask_cors import CORS
import requests, json, os, math, tempfile
import pandas as pd
import numpy as np
from pathlib import Path

app = Flask(__name__, static_folder=".")
CORS(app)

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data" / "processed"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Bundled static assets committed alongside the app.
# Checked first so the server starts instantly without any FRED download.
_BUNDLED_CACHE = ROOT / "macro_dataset_v3.json"
_BUNDLED_IRF   = ROOT / "irf_v3.json"

# Runtime-generated cache (written after a live /api/refresh)
CACHE_FILE = DATA_DIR / "macro_dataset_v3.json"
IRF_FILE   = DATA_DIR / "irf_v3.json"


def _clean_for_json(obj):
    """Convert numpy/pandas scalars and non-finite floats to strict JSON-safe values."""
    if obj is None:
        return None
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        v = float(obj)
        return v if math.isfinite(v) else None
    if isinstance(obj, (np.ndarray, list, tuple)):
        return [_clean_for_json(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): _clean_for_json(v) for k, v in obj.items()}
    if pd.isna(obj) if not isinstance(obj, (str, bytes, dict, list, tuple)) else False:
        return None
    return obj


def _atomic_json_dump(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    clean = _clean_for_json(payload)
    fd, tmp = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(clean, f, indent=2 if isinstance(clean, dict) else None, allow_nan=False)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _save_runtime_cache(panel, model):
    panel_records = panel.reset_index().rename(columns={"index": "date"})
    panel_records["date"] = panel_records["date"].astype(str)
    model = dict(model)
    model["cacheVersion"] = CACHE_VERSION
    _atomic_json_dump(CACHE_FILE, panel_records.to_dict(orient="records"))
    _atomic_json_dump(IRF_FILE, model)

# ── FRED FETCHER ───────────────────────────────────────────────────────────────

def fetch_fred(series_id, start="1978-01-01"):
    url = (f"https://fred.stlouisfed.org/graph/fredgraph.csv"
           f"?id={series_id}&start_date={start}")
    r = requests.get(url, timeout=15)
    if r.status_code != 200:
        raise RuntimeError(f"FRED HTTP {r.status_code} for {series_id}")
    from io import StringIO
    df = pd.read_csv(StringIO(r.text), parse_dates=[0])
    df.columns = ["date", series_id]
    df = df[df[series_id] != "."].copy()
    df[series_id] = pd.to_numeric(df[series_id], errors="coerce")
    df = df.dropna().set_index("date")
    if len(df) < 10:
        raise RuntimeError(f"Insufficient data for {series_id}: {len(df)} rows")
    print(f"  ✓ {series_id}: {len(df)} obs")
    return df

# ── BUILD MACRO PANEL ─────────────────────────────────────────────────────────

def build_macro_panel():
    print("Fetching FRED data...")
    SERIES = ["CPIAUCSL", "CPILFESL", "UNRATE", "FEDFUNDS",
              "INDPRO", "GDPC1", "DGS2", "DGS10", "PAYEMS", "DTWEXBGS"]
    raw = {}
    for sid in SERIES:
        try:
            raw[sid] = fetch_fred(sid)
        except Exception as e:
            print(f"  ✗ {sid}: {e}")

    core = ["CPIAUCSL", "UNRATE", "FEDFUNDS", "INDPRO"]
    missing_core = [s for s in core if s not in raw]
    if missing_core:
        raise RuntimeError(f"Cannot build panel — core FRED series failed: {missing_core}")

    idx = pd.date_range("1979-01-01", pd.Timestamp.today(), freq="MS")
    panel = pd.DataFrame(index=idx)

    # CPI → headline YoY inflation
    cpi = raw["CPIAUCSL"].resample("MS").last().reindex(idx, method="ffill")
    panel["cpi"] = cpi["CPIAUCSL"]
    panel["inflation"] = 100 * (panel["cpi"] / panel["cpi"].shift(12) - 1)

    # Core CPI
    if "CPILFESL" in raw:
        ccpi = raw["CPILFESL"].resample("MS").last().reindex(idx, method="ffill")
        panel["coreInflation"] = 100 * (ccpi["CPILFESL"] / ccpi["CPILFESL"].shift(12) - 1)

    # Unemployment
    unr = raw["UNRATE"].resample("MS").last().reindex(idx, method="ffill")
    panel["unemployment"] = unr["UNRATE"]

    # Fed Funds
    ff = raw["FEDFUNDS"].resample("MS").mean().reindex(idx, method="ffill")
    panel["fedFunds"] = ff["FEDFUNDS"]

    # STEP 1: rename IP-based growth proxy → "growth"
    ip = raw["INDPRO"].resample("MS").last().reindex(idx, method="ffill")
    panel["ip"] = ip["INDPRO"]
    panel["growth_yoy"] = 100 * (panel["ip"] / panel["ip"].shift(12) - 1)
    panel["indpro_m"] = 1200 * np.log(panel["ip"] / panel["ip"].shift(1))

    # GDP quarterly (kept for reference but not primary)
    if "GDPC1" in raw:
        gdp_q = raw["GDPC1"].resample("QS").last()["GDPC1"]
        gdp_growth = 400 * np.log(gdp_q / gdp_q.shift(1))   # log annualized QoQ
        # Forward-fill within each quarter (don't interpolate annualized growth rates)
        gdp_m = gdp_growth.reindex(idx, method="ffill")
        panel["gdp_qoq"] = gdp_m

    # NFP
    if "PAYEMS" in raw:
        pay = raw["PAYEMS"].resample("MS").last().reindex(idx, method="ffill")
        panel["nfp"] = pay["PAYEMS"].diff()

    # Yields
    for col, sid in [("y2", "DGS2"), ("y10", "DGS10")]:
        if sid in raw:
            y = raw[sid].resample("MS").mean().reindex(idx, method="ffill")
            panel[col] = y[sid]

    # Yield spread + dollar index
    if "y2" in panel.columns and "y10" in panel.columns:
        panel["yieldSpread"] = panel["y10"] - panel["y2"]

    if "DTWEXBGS" in raw:
        dol = raw["DTWEXBGS"].resample("MS").mean().reindex(idx, method="ffill")
        panel["dollar"] = dol["DTWEXBGS"]
        panel["dollar_yoy"] = 100 * (panel["dollar"] / panel["dollar"].shift(12) - 1)
    else:
        panel["dollar_yoy"] = 0.0

    # STEP 8: Real FOMC decision = change only in months where rate actually moved
    # Monthly avg diff is noisy (partial month effects); use diff but clip tiny moves
    ff_diff = panel["fedFunds"].diff().fillna(0)
    # Treat moves < 5bps as noise (avg artifacts), snap to 0
    panel["realDecision"] = ff_diff.where(ff_diff.abs() >= 0.05, 0.0)

    panel = panel.dropna(subset=["inflation", "unemployment", "fedFunds", "ip"])
    panel = panel.ffill()

    print(f"Panel: {len(panel)} months, {panel.index[0].date()} → {panel.index[-1].date()}")
    return panel

# ── STATIONARITY ──────────────────────────────────────────────────────────────

def run_adf(series, name):
    from statsmodels.tsa.stattools import adfuller
    result = adfuller(series.dropna(), maxlag=12, autolag="AIC")
    pval = result[1]
    print(f"  ADF {name}: p={pval:.4f} → {'stationary' if pval<0.05 else 'non-stationary'}")
    return pval < 0.05

# ── STEP 4: FIXED IRF SCALING ─────────────────────────────────────────────────

def estimate_var_irf_for_panel(p_sub, label="full"):
    """
    VAR on [indpro_m, unemployment, inflation, fedFunds].
    STEP 4: scale = 0.25 / avg(ff_diff_std) instead of chol diagonal.
    """
    try:
        from statsmodels.tsa.api import VAR

        p = p_sub.dropna(subset=["indpro_m","unemployment","inflation","fedFunds"]).copy()

        inf_stat  = run_adf(p["inflation"], "inflation")
        unemp_stat = run_adf(p["unemployment"], "unemployment")
        ip_stat   = run_adf(p["indpro_m"], "indpro_m")
        ff_stat   = run_adf(p["fedFunds"], "fedFunds")

        df_var = pd.DataFrame(index=p.index)
        df_var["ip"]    = p["indpro_m"] if ip_stat else p["indpro_m"].diff()
        df_var["unemp"] = p["unemployment"] if unemp_stat else p["unemployment"].diff()
        df_var["inf"]   = p["inflation"] if inf_stat else p["inflation"].diff()
        df_var["ff"]    = p["fedFunds"] if ff_stat else p["fedFunds"].diff()
        df_var = df_var.dropna()

        model = VAR(df_var)
        results = model.fit(maxlags=12, ic="aic", trend="c")
        print(f"  VAR {label} lag: {results.k_ar}")

        irf = results.irf(24)
        shock_idx = 3

        # STEP 4 fix: use avg std of ff_diff for scaling
        ff_diff_std = p["fedFunds"].diff().std()
        scale = 0.25 / max(ff_diff_std, 0.01)

        orth = irf.orth_irfs
        out = {
            "growth":       (orth[:, 0, shock_idx] * scale).tolist(),
            "unemployment": (orth[:, 1, shock_idx] * scale).tolist(),
            "inflation":    (orth[:, 2, shock_idx] * scale).tolist(),
        }
        # Sign normalization: a rate hike should REDUCE inflation (negative 12m sum)
        # and RAISE unemployment (positive 12m sum) and REDUCE growth (negative 12m sum)
        inf_sum_12   = sum(out["inflation"][:12])
        unemp_sum_12 = sum(out["unemployment"][:12])
        growth_sum_12= sum(out["growth"][:12])

        if inf_sum_12 > 0:
            out["inflation"] = [-x for x in out["inflation"]]
            print(f"  VAR {label}: inflation IRF flipped")
        if unemp_sum_12 < 0:
            out["unemployment"] = [-x for x in out["unemployment"]]
            print(f"  VAR {label}: unemployment IRF flipped")
        if growth_sum_12 > 0:
            out["growth"] = [-x for x in out["growth"]]
            print(f"  VAR {label}: growth IRF flipped")

        return out
    except Exception as e:
        print(f"  VAR {label} failed: {e}")
        return None

# ── STEP 7: REGIME VAR ────────────────────────────────────────────────────────

REGIME_ERAS = {
    "1980_1995": ("1980-01-01", "1995-12-31"),
    "1995_2008": ("1995-01-01", "2008-12-31"),
    "2008_2020": ("2008-01-01", "2020-12-31"),
    "2020_now":  ("2020-01-01", None),
}

def estimate_regime_irfs(panel):
    regime_irfs = {}
    for name, (start, end) in REGIME_ERAS.items():
        s = pd.Timestamp(start)
        e = pd.Timestamp(end) if end else panel.index[-1]
        sub = panel.loc[s:e]
        if len(sub) < 60:
            print(f"  Skipping regime {name}: too few obs ({len(sub)})")
            continue
        irfs = estimate_var_irf_for_panel(sub, label=name)
        if irfs:
            regime_irfs[name] = irfs
            print(f"  Regime {name}: IRF estimated OK")
    return regime_irfs

def get_regime_for_date(date_str):
    """Return regime key for a given date string."""
    d = pd.Timestamp(date_str)
    if d >= pd.Timestamp("2020-01-01"): return "2020_now"
    if d >= pd.Timestamp("2008-01-01"): return "2008_2020"
    if d >= pd.Timestamp("1995-01-01"): return "1995_2008"
    return "1980_1995"

# ── STEP 2: IMPROVED EXPECTATIONS CHANNEL ─────────────────────────────────────

def estimate_expectations_params(panel):
    """
    Regress Δinflation on lagged fedFunds, yieldSpread, inflation level.
    The fedFunds coefficient is negative (higher rates → lower inflation change).
    We export alpha_ff as a POSITIVE number representing how much inflation
    responds per unit of expectationsShock (which is signed separately in JS).
    """
    from numpy.linalg import lstsq
    p = panel.dropna(subset=["inflation","fedFunds","yieldSpread"]).copy()
    dinf = p["inflation"].diff().values[1:]
    X = np.column_stack([
        np.ones(len(p) - 1),
        p["fedFunds"].values[:-1],
        p["yieldSpread"].values[:-1],
        p["inflation"].values[:-1],
    ])
    coeffs, _, _, _ = lstsq(X, dinf, rcond=None)
    # coeffs[1] is typically negative (higher rates → lower Δinf)
    # alpha_ff = magnitude of that effect, always positive
    alpha = abs(float(coeffs[1]))
    alpha = float(np.clip(alpha, 0.01, 0.20))
    spread_alpha = float(coeffs[2])
    spread_alpha = float(np.clip(spread_alpha, -0.15, 0.15))
    # Persistence: how long a shock to expectations lasts (empirically ~0.80-0.90)
    # Also estimate from AR(1) on inflation itself
    inf_ar1 = float(np.clip(coeffs[3], 0.70, 0.95))
    print(f"  Expectations: alpha_ff={alpha:.4f}, alpha_spread={spread_alpha:.4f}, persistence={inf_ar1:.3f}")
    return {"alpha_ff": alpha, "alpha_spread": spread_alpha, "persistence": inf_ar1}

# ── STEP 3: COMPLETE FCI ──────────────────────────────────────────────────────

def estimate_fci_weights(panel):
    """
    OLS: indpro_m ~ y2 + yieldSpread + dollar_yoy
    FCI = w_y2*y2 + w_spread*spread + w_dollar*dollar_yoy
    growthDelta += -beta * FCI
    unemploymentDelta += gamma * (-growthDelta)   [Okun]
    """
    from numpy.linalg import lstsq
    cols = ["indpro_m","y2","yieldSpread","dollar_yoy"]
    p = panel.dropna(subset=cols).copy()
    X = np.column_stack([np.ones(len(p)), p["y2"].values,
                         p["yieldSpread"].values, p["dollar_yoy"].values])
    y = p["indpro_m"].values
    coeffs, _, _, _ = lstsq(X, y, rcond=None)
    beta_y2     = -float(coeffs[1])
    beta_spread = -float(coeffs[2])
    beta_dollar = -float(coeffs[3])
    # Clamp to economically sensible ranges
    beta_y2     = float(np.clip(beta_y2,     0.05, 0.5))
    beta_spread = float(np.clip(beta_spread, -0.3, 0.3))
    beta_dollar = float(np.clip(beta_dollar,  0.0, 0.1))
    # Okun's Law: ~0.5 (empirically 0.4-0.6 across cycles)
    okun_gamma = 0.5
    print(f"  FCI weights: y2={beta_y2:.4f}, spread={beta_spread:.4f}, dollar={beta_dollar:.4f}, okun={okun_gamma}")
    return {"y2": beta_y2, "spread": beta_spread, "dollar": beta_dollar, "okun": okun_gamma}

# ── STEP 10: DATA-DRIVEN APPROVAL ─────────────────────────────────────────────

def estimate_approval_weights(panel):
    from numpy.linalg import lstsq
    p = panel.dropna(subset=["inflation","unemployment","fedFunds"]).copy()
    X = np.column_stack([np.ones(len(p)), p["inflation"].values - 2.0, p["unemployment"].values - 5.0])
    y = p["fedFunds"].values
    coeffs, _, _, _ = lstsq(X, y, rcond=None)
    # Enforce economically sensible bounds: inf_gap ∈ [1.0,2.5], unemp_gap ∈ [-2.0,-0.3]
    inf_gap   = float(np.clip(coeffs[1],  1.0,  2.5))
    unemp_gap = float(np.clip(coeffs[2], -2.0, -0.3))
    print(f"  Taylor: const={coeffs[0]:.2f} inf_gap={inf_gap:.2f} unemp_gap={unemp_gap:.2f}")
    inf_std    = float(p["inflation"].std())
    unemp_std  = float(p["unemployment"].std())
    growth_std = float(panel["growth_yoy"].dropna().std())
    # Monthly diff std — empirical month-to-month volatility, used as velocity cap in JS
    inf_diff_std    = float(p["inflation"].diff().dropna().std())
    unemp_diff_std  = float(p["unemployment"].diff().dropna().std())
    growth_diff_std = float(panel["growth_yoy"].diff().dropna().std())
    return {
        "taylor": {"const": float(coeffs[0]), "inf_gap": inf_gap, "unemp_gap": unemp_gap},
        "zstd": {"inflation": inf_std, "unemployment": unemp_std, "growth": growth_std},
        "monthlyDiffStd": {"inflation": inf_diff_std, "unemployment": unemp_diff_std, "growth": growth_diff_std},
    }

def fallback_model_params():
    h = 25
    t = np.arange(h)
    fallback_irf = {
        "inflation":    (-0.06 * t * np.exp(-t / 12)).tolist(),   # peak ~-0.3pp at 6mo per 25bps
        "unemployment": ( 0.04 * t * np.exp(-t / 14)).tolist(),   # peak ~+0.3pp at 6mo per 25bps
        "growth":       (-0.08 * t * np.exp(-t /  8)).tolist(),   # faster hit, faster recovery
    }
    return {
        "irfs": fallback_irf,
        "regimeIrfs": {"1980_1995": fallback_irf, "1995_2008": fallback_irf,
                       "2008_2020": fallback_irf, "2020_now": fallback_irf},
        "fciWeights": {"y2": 0.15, "spread": -0.05, "dollar": 0.02, "okun": 0.5},
        "expParams": {"alpha_ff": 0.08, "alpha_spread": -0.03, "persistence": 0.85},
        "approvalWeights": {
            "taylor": {"const": 2.0, "inf_gap": 1.5, "unemp_gap": -1.0},
            "zstd": {"inflation": 2.5, "unemployment": 1.8, "growth": 3.0},
            "monthlyDiffStd": {"inflation": 0.3, "unemployment": 0.15, "growth": 0.8},
        },
    }

def estimate_full_model(panel):
    try:
        print("Estimating full-sample VAR...")
        full_irf = estimate_var_irf_for_panel(panel, "full")
        if not full_irf:
            raise ValueError("Full VAR failed")

        print("Estimating regime IRFs...")
        regime_irfs = estimate_regime_irfs(panel)
        if not regime_irfs:
            regime_irfs = {"full": full_irf}

        print("Estimating FCI weights...")
        fci_weights = estimate_fci_weights(panel)

        print("Estimating expectations params...")
        exp_params = estimate_expectations_params(panel)

        print("Estimating approval weights...")
        approval_weights = estimate_approval_weights(panel)

        return {
            "irfs": full_irf,
            "regimeIrfs": regime_irfs,
            "fciWeights": fci_weights,
            "expParams": exp_params,
            "approvalWeights": approval_weights,
        }
    except Exception as e:
        print(f"Model estimation failed: {e} — using fallback")
        return fallback_model_params()

# ── FOMC MEETING DATES ─────────────────────────────────────────────────────────

FOMC_DATES_BY_ERA = {
    "volcker": {
        "1979-08","1979-10","1979-11","1980-01","1980-02","1980-03","1980-05",
        "1980-07","1980-08","1980-09","1980-10","1980-11","1981-01","1981-02",
        "1981-03","1981-05","1981-07","1981-08","1981-09","1981-11","1981-12",
        "1982-02","1982-03","1982-04","1982-05","1982-07","1982-08","1982-09",
        "1982-10","1982-11","1982-12","1983-02","1983-03","1983-05","1983-07",
        "1983-08","1983-09","1983-10","1983-11","1984-01","1984-02","1984-03",
        "1984-05","1984-07","1984-08","1984-09","1984-10","1984-11","1985-02",
        "1985-03","1985-05","1985-07","1985-08","1985-09","1985-11","1985-12",
        "1986-02","1986-03","1986-04","1986-05","1986-07","1986-09","1986-12",
        "1987-02","1987-03","1987-05","1987-07","1987-08","1987-09",
    },
    "greenspan": {
        "1987-11","1988-02","1988-03","1988-05","1988-06","1988-08","1988-09",
        "1988-11","1988-12","1989-02","1989-03","1989-05","1989-06","1989-07",
        "1989-08","1989-10","1989-11","1990-02","1990-03","1990-05","1990-07",
        "1990-08","1990-10","1990-11","1991-02","1991-03","1991-04","1991-05",
        "1991-07","1991-08","1991-09","1991-10","1991-11","1991-12","1992-02",
        "1992-03","1992-04","1992-05","1992-07","1992-08","1992-09","1992-10",
        "1992-11","1993-02","1993-03","1993-05","1993-07","1993-08","1993-09",
        "1993-11","1994-02","1994-03","1994-04","1994-05","1994-07","1994-08",
        "1994-09","1994-11","1995-02","1995-03","1995-05","1995-07","1995-08",
        "1995-09","1995-11","1996-02","1996-03","1996-05","1996-07","1996-09",
        "1996-11","1997-02","1997-03","1997-05","1997-07","1997-09","1997-11",
        "1998-02","1998-03","1998-05","1998-06","1998-07","1998-09","1998-10",
        "1998-11","1999-02","1999-03","1999-05","1999-06","1999-08","1999-09",
        "1999-10","1999-11","2000-02","2000-03","2000-05","2000-06","2000-08",
        "2000-10","2001-01","2001-03","2001-04","2001-05","2001-06","2001-08",
        "2001-09","2001-10","2001-11","2002-01","2002-03","2002-05","2002-06",
        "2002-08","2002-09","2002-11","2003-01","2003-03","2003-05","2003-06",
        "2003-08","2003-09","2003-10","2003-12","2004-01","2004-03","2004-05",
        "2004-06","2004-08","2004-09","2004-11","2005-02","2005-03","2005-05",
        "2005-06","2005-08","2005-09","2005-11","2006-01","2006-03",
    },
    "bernanke": {
        "2006-05","2006-06","2006-08","2006-09","2006-10","2006-12",
        "2007-01","2007-03","2007-05","2007-06","2007-08","2007-09","2007-10",
        "2007-12","2008-01","2008-03","2008-04","2008-06","2008-08","2008-09",
        "2008-10","2008-12","2009-01","2009-03","2009-04","2009-06","2009-08",
        "2009-09","2009-11","2010-01","2010-03","2010-04","2010-06","2010-08",
        "2010-09","2010-11","2011-01","2011-03","2011-04","2011-06","2011-08",
        "2011-09","2011-11","2012-01","2012-03","2012-04","2012-06","2012-08",
        "2012-09","2012-10","2012-12","2013-01","2013-03","2013-04","2013-05",
        "2013-06","2013-07","2013-09","2013-10","2013-12","2014-01","2014-03",
    },
    "powell": {
        "2020-01","2020-03","2020-04","2020-06","2020-07","2020-09","2020-11",
        "2020-12","2021-01","2021-03","2021-04","2021-06","2021-07","2021-09",
        "2021-11","2021-12","2022-01","2022-03","2022-05","2022-06","2022-07",
        "2022-09","2022-11","2022-12","2023-02","2023-03","2023-05","2023-06",
        "2023-07","2023-09","2023-11","2023-12","2024-01","2024-03","2024-05",
        "2024-06","2024-07","2024-09","2024-11","2024-12",
        "2025-01","2025-03","2025-05","2025-06","2025-07","2025-09","2025-10","2025-12",
        "2026-01","2026-03","2026-04","2026-06","2026-07","2026-09","2026-10","2026-12",
    },
}

# ── STEP 9: EVENTS WITH MODEL EFFECTS ─────────────────────────────────────────

HISTORICAL_EVENTS = {
    "1979-10": {"headline":"Saturday Night Massacre: Volcker shifts to reserves targeting","type":"policy",
                "effects": {"inflationDelta": -0.5, "growthDelta": -0.8}},
    "1980-01": {"headline":"Iranian hostage crisis deepens oil shock","type":"shock",
                "effects": {"inflationDelta": 1.2, "growthDelta": -1.5}},
    "1980-04": {"headline":"Credit controls imposed — economy seizes","type":"policy",
                "effects": {"growthDelta": -2.0, "unemploymentDelta": 0.5}},
    "1981-01": {"headline":"Reagan takes office; promises to back inflation fight","type":"policy",
                "effects": {"inflationDelta": -0.3}},
    "1982-08": {"headline":"Mexico debt crisis: EM contagion risk rises","type":"market",
                "effects": {"growthDelta": -0.5}},
    "1982-12": {"headline":"Unemployment hits 10.8% — highest since Great Depression","type":"shock",
                "effects": {"growthDelta": -1.0}},
    "1987-10": {"headline":"Black Monday: Dow −22.6% in one session","type":"market",
                "effects": {"growthDelta": -1.2, "inflationDelta": -0.2}},
    "1989-08": {"headline":"S&L Crisis: over 1,000 thrifts fail","type":"market",
                "effects": {"growthDelta": -0.5}},
    "1990-08": {"headline":"Iraq invades Kuwait — oil price surges","type":"shock",
                "effects": {"inflationDelta": 1.0, "growthDelta": -1.0}},
    "1997-07": {"headline":"Asian Financial Crisis: baht collapses, EM contagion","type":"market",
                "effects": {"growthDelta": -0.4}},
    "1998-09": {"headline":"Russia default, LTCM collapse — systemic risk","type":"market",
                "effects": {"growthDelta": -0.8, "inflationDelta": -0.3}},
    "2000-03": {"headline":"Dot-com bubble peaks — Nasdaq at 5,048","type":"market",
                "effects": {"growthDelta": -0.5}},
    "2001-09": {"headline":"9/11 attacks — markets closed 4 days","type":"shock",
                "effects": {"growthDelta": -2.0, "inflationDelta": -0.5, "unemploymentDelta": 0.4}},
    "2005-08": {"headline":"Hurricane Katrina — energy supply shock","type":"shock",
                "effects": {"inflationDelta": 0.5, "growthDelta": -0.3}},
    "2007-08": {"headline":"Subprime cracks: BNP Paribas freezes funds","type":"market",
                "effects": {"growthDelta": -1.0}},
    "2008-09": {"headline":"Lehman Brothers files $639B bankruptcy","type":"shock",
                "effects": {"growthDelta": -4.0, "unemploymentDelta": 1.5, "inflationDelta": -0.8}},
    "2010-05": {"headline":"European sovereign debt crisis — contagion fears","type":"market",
                "effects": {"growthDelta": -0.4}},
    "2011-08": {"headline":"US credit rating downgraded by S&P","type":"shock",
                "effects": {"growthDelta": -0.3}},
    "2013-05": {"headline":"Taper Tantrum: 10Y yield spikes 100bps on tapering hint","type":"market",
                "effects": {"growthDelta": -0.3, "inflationDelta": -0.2}},
    "2020-03": {"headline":"COVID-19 declared pandemic — economy shuttered","type":"shock",
                "effects": {"growthDelta": -10.0, "unemploymentDelta": 8.0, "inflationDelta": -1.0}},
    "2021-03": {"headline":"Stimulus checks + reopening: demand surge begins","type":"shock",
                "effects": {"inflationDelta": 1.5, "growthDelta": 3.0}},
    "2022-02": {"headline":"Russia invades Ukraine — energy & food price shock","type":"shock",
                "effects": {"inflationDelta": 1.2, "growthDelta": -0.5}},
    "2023-03": {"headline":"SVB fails: fastest bank run in history","type":"market",
                "effects": {"growthDelta": -0.4}},
}

# ── DATA CACHE ─────────────────────────────────────────────────────────────────

_cache = {}

CACHE_VERSION = "v5"  # bump this when model/data structure changes

def _try_load_cache(irf_path, cache_path, label=""):
    """Attempt to load model + panel from a pair of JSON files.
    Returns (panel, model) or raises on failure."""
    with open(irf_path) as f:
        model = json.load(f)
    if model.get("cacheVersion") != CACHE_VERSION:
        raise ValueError(f"{label} version mismatch ({model.get('cacheVersion')} vs {CACHE_VERSION})")
    print(f"Loading dataset from {cache_path} [{label}]")
    with open(cache_path) as f:
        rows = json.load(f)
    panel = pd.DataFrame(rows)
    panel["date"] = pd.to_datetime(panel["date"])
    panel = panel.set_index("date")
    return panel, model


def load_or_build():
    if "panel" in _cache:
        return _cache["panel"], _cache["model"]

    # 1. Runtime cache (written by /api/refresh) — freshest, checked first
    if CACHE_FILE.exists() and IRF_FILE.exists():
        try:
            panel, model = _try_load_cache(IRF_FILE, CACHE_FILE, "runtime")
            _cache["panel"] = panel
            _cache["model"] = model
            return panel, model
        except Exception as e:
            print(f"Runtime cache unusable: {e} — falling back")
            CACHE_FILE.unlink(missing_ok=True)
            IRF_FILE.unlink(missing_ok=True)

    # 2. Bundled static assets committed to the repo — instant cold start
    if _BUNDLED_CACHE.exists() and _BUNDLED_IRF.exists():
        try:
            panel, model = _try_load_cache(_BUNDLED_IRF, _BUNDLED_CACHE, "bundled")
            _cache["panel"] = panel
            _cache["model"] = model
            print("Loaded bundled dataset — no FRED fetch needed.")
            return panel, model
        except Exception as e:
            print(f"Bundled cache unusable: {e} — will fetch from FRED")

    # 3. Last resort: fetch live from FRED and estimate model
    print("No usable cache — fetching from FRED...")
    panel = build_macro_panel()
    model = estimate_full_model(panel)
    _save_runtime_cache(panel, model)
    model["cacheVersion"] = CACHE_VERSION
    print("Live cache saved.")
    _cache["panel"] = panel
    _cache["model"] = model
    return panel, model

# ── ROUTES ─────────────────────────────────────────────────────────────────────

ERA_WINDOWS = {
    "volcker":   ("1979-08-01", "1987-08-01"),
    "greenspan": ("1987-08-01", "2006-02-01"),
    "bernanke":  ("2006-02-01", "2014-02-01"),
    "powell":    ("2020-01-01", None),
}

@app.route("/api/era/<era_name>")
def get_era_data(era_name):
    if era_name not in ERA_WINDOWS:
        return jsonify({"error": "Unknown era"}), 400
    try:
        panel, model = load_or_build()
    except RuntimeError as e:
        return jsonify({"error": str(e), "hint": "FRED unreachable and no cache found"}), 503

    start_str, end_str = ERA_WINDOWS[era_name]
    start = pd.Timestamp(start_str)
    end   = pd.Timestamp(end_str) if end_str else panel.index[-1]
    era_panel = panel.loc[start:end].copy()
    fomc_months = FOMC_DATES_BY_ERA.get(era_name, set())

    months = []
    for date, row in era_panel.iterrows():
        ym = date.strftime("%Y-%m")
        event = HISTORICAL_EVENTS.get(ym)
        # Serialize event (remove effects from frontend-visible data, keep headline/type)
        event_out = None
        if event:
            event_out = {
                "headline": event["headline"],
                "type": event["type"],
                "effects": event.get("effects", {}),
            }
        m = {
            "date":           date.strftime("%Y-%m-%d"),
            "inflation":      _f(row.get("inflation")),
            "coreInflation":  _f(row.get("coreInflation", row.get("inflation"))),
            "unemployment":   _f(row.get("unemployment")),
            "growth":         _f(row.get("growth_yoy", 0)),   # STEP 1: renamed
            "indpro_m":       _f(row.get("indpro_m", 0)),
            "fedFunds":       _f(row.get("fedFunds")),
            "y2":             _f(row.get("y2", row.get("fedFunds", 3) + 0.2)),
            "y10":            _f(row.get("y10", row.get("fedFunds", 3) + 1.5)),
            "yieldSpread":    _f(row.get("yieldSpread", 1.5)),
            "dollar_yoy":     _f(row.get("dollar_yoy", 0)),
            "nfp":            round(float(row.get("nfp", 0) or 0)),
            "realDecision":   _f(row.get("realDecision", 0)),  # STEP 8
            "regime":         get_regime_for_date(date.strftime("%Y-%m-%d")),
            "isMeeting":      1 if ym in fomc_months else 0,
            "event":          event_out,
        }
        months.append(m)

    return jsonify(_clean_for_json({
        "era":    era_name,
        "months": months,
        "model":  model,
        "meta": {
            "startDate": months[0]["date"] if months else None,
            "endDate": months[-1]["date"] if months else None,
            "monthCount": len(months),
            "meetingCount": sum(1 for m in months if m.get("isMeeting") == 1),
            "eventCount": sum(1 for m in months if m.get("event")),
            "cacheVersion": model.get("cacheVersion"),
        },
    }))

@app.route("/api/refresh", methods=["POST"])
def refresh_data():
    """Force a live FRED rebuild.

    The previous implementation deleted runtime cache and then immediately loaded the
    bundled cache, so it looked successful but did not actually refresh data.
    """
    try:
        panel = build_macro_panel()
        model = estimate_full_model(panel)
        _save_runtime_cache(panel, model)
        model["cacheVersion"] = CACHE_VERSION
        _cache.clear()
        _cache["panel"] = panel
        _cache["model"] = model
        return jsonify({
            "status": "refreshed",
            "rows": int(len(panel)),
            "latestDate": panel.index[-1].strftime("%Y-%m-%d"),
        })
    except Exception as e:
        return jsonify({
            "error": str(e),
            "hint": "Live FRED refresh failed; bundled/runtime cache is still available if present.",
        }), 503

@app.route("/api/health")
def health():
    try:
        panel, model = load_or_build()
        latest = panel.index[-1].strftime("%Y-%m-%d") if len(panel) else None
        rows = int(len(panel))
    except Exception:
        latest = None
        rows = 0
    return jsonify({
        "status": "ok",
        "runtime_cache": CACHE_FILE.exists(),
        "runtime_irf_cache": IRF_FILE.exists(),
        "bundled_cache": _BUNDLED_CACHE.exists(),
        "bundled_irf": _BUNDLED_IRF.exists(),
        "rows": rows,
        "latestDate": latest,
        "cacheVersion": CACHE_VERSION,
    })

@app.route("/")
def index():
    return send_from_directory(".", "index.html")

def _f(val, digits=2):
    try:
        v = float(val)
        if math.isnan(v) or math.isinf(v): return 0.0
        return round(v, digits)
    except:
        return 0.0

if __name__ == "__main__":
    print("Starting FED CHAIR simulator backend v3...")
    try:
        load_or_build()
    except Exception as e:
        print(f"Pre-load warning: {e}")
        print("Server will still start; data loads on first request.")
    port = int(os.environ.get("PORT", "5000"))
    app.run(debug=False, host="0.0.0.0", port=port)