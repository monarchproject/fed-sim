"""
FED CHAIR SIMULATOR — Backend
main.py (renamed from app.py for Vercel deployment)
"""

from flask import Flask, jsonify, send_from_directory, request, Response
from flask_cors import CORS
import requests, json, os, math, tempfile, calendar, csv
import pandas as pd
import numpy as np
from pathlib import Path

app = Flask(__name__, static_folder=".")
CORS(app)

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data" / "processed"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Bundled static assets committed alongside the app.
# Local CSV is preferred so the app does not need FRED/live internet at runtime.
# JSON remains as a compatibility fallback.
OFFICIAL_DATA_DIR = ROOT / "data" / "official"
_BUNDLED_CSV_PANEL = OFFICIAL_DATA_DIR / "macro_dataset_v3.csv"
_OFFICIAL_DECISIONS_CSV = OFFICIAL_DATA_DIR / "official_fomc_decisions.csv"
_BUNDLED_CACHE = ROOT / "macro_dataset_v3.json"
_BUNDLED_IRF   = ROOT / "irf_v3.json"

# Runtime-generated cache (written after a live /api/refresh)
CACHE_FILE = DATA_DIR / "macro_dataset_v3.json"
IRF_FILE   = DATA_DIR / "irf_v3.json"


# Real market pricing cache. This file is written at runtime after the backend
# pulls historical ZQ futures closes / EFFR data from public data endpoints.
MARKET_PRICING_CACHE = DATA_DIR / "market_pricing_real_cache.json"

# Optional user-provided real data file. If present, it takes priority over live
# fetching. This is the recommended path for licensed CME/Barchart/Bloomberg data.
# Schema:
# era,ym,meeting_date,asof_date,source,contract,price,implied_rate,pre_meeting_rate,
# market_implied_bps,prob_cut_50,prob_cut_25,prob_hold,prob_hike_25,prob_hike_50,prob_hike_75
USER_MARKET_PRICING_FILE = ROOT / "fed_funds_futures_pricing.csv"

# Strict policy: market pricing must be a direct pre-FOMC market row.
# Event-study implied rows and rate-path proxies are useful diagnostics, but they
# are not accepted as the market benchmark used for scoring/reactions.
STRICT_MARKET_PRICING_QUALITIES = {
    "real_zq_futures",
    "licensed_fed_funds_futures",
    "direct_fed_funds_futures",
    "cme_fedwatch_archive",
    "user_provided_real_data",
}

POLICY_STEP_BPS = 25.0


def _round_policy_move_bps(value_bps):
    """Snap official target changes to standard 25bp policy increments.

    FEDFUNDS monthly averages can create fake +127bp / -16bp moves. We never use
    those averages for real FOMC decisions. Official target changes are measured
    from target-rate series and rounded to normal policy-step increments.
    """
    try:
        v = float(value_bps)
    except Exception:
        return None
    if not math.isfinite(v):
        return None
    if abs(v) < POLICY_STEP_BPS / 2.0:
        return 0.0
    return float(round(v / POLICY_STEP_BPS) * POLICY_STEP_BPS)


def _combine_official_target_upper(raw):
    """Return a daily official policy target upper/level from FRED series.

    DFEDTAR is FRED's discontinued single target-rate series. DFEDTARU is
    the upper limit of the post-2008 target range. The old code requested
    FEDTAR, which is not the FRED series id, causing official decisions to
    disappear for older eras.
    """
    parts = []

    # Accept both names so old runtime caches do not break, but live refresh uses
    # the correct FRED id DFEDTAR.
    for sid in ("DFEDTAR", "FEDTAR"):
        if sid in raw and sid in raw[sid]:
            target = raw[sid][sid].copy()
            target.name = "policyTargetUpper"
            parts.append(target)
            break

    if "DFEDTARU" in raw and "DFEDTARU" in raw["DFEDTARU"]:
        upper = raw["DFEDTARU"]["DFEDTARU"].copy()
        upper.name = "policyTargetUpper"
        parts.append(upper)

    if not parts:
        return None
    target = pd.concat(parts).sort_index(kind="stable")
    target = target[~target.index.duplicated(keep="last")].dropna()
    return target if not target.empty else None


def _apply_official_policy_decisions(panel, raw=None):
    """Attach real policy decisions from official target-rate data only.

    If target data are absent for a period, realDecision is left NaN and the UI
    shows N/A. This is intentional: no monthly FEDFUNDS proxy, no fabricated move.
    """
    panel = panel.copy()
    if "realDecision" not in panel.columns:
        panel["realDecision"] = np.nan
    if "realDecisionSource" not in panel.columns:
        panel["realDecisionSource"] = "unavailable"

    target_daily = _combine_official_target_upper(raw or {})
    if target_daily is None:
        return panel

    monthly_target = target_daily.resample("MS").last().reindex(panel.index, method="ffill")
    target_change_bps = monthly_target.diff() * 100.0
    rounded_bps = target_change_bps.apply(_round_policy_move_bps)

    panel["policyTargetUpper"] = monthly_target
    panel["policyTargetMid"] = monthly_target
    panel["realDecision"] = rounded_bps / 100.0
    panel.loc[monthly_target.notna(), "realDecisionSource"] = "FRED official target-rate series"
    return panel


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



def _stabilize_model_irfs(model):
    """Guard against small-sample regime VAR explosions.

    Regime VARs are useful only when their impulse responses are stable. If a
    regime channel is more than 2x the full-sample channel for the same variable,
    that channel is treated as a sample-sensitive estimate and replaced by the
    full-sample IRF. This is a model-diagnostic rule, not a gameplay shortcut: it
    prevents outputs such as a +100bp hike implying double-digit monthly growth
    collapses from one noisy regime fit.
    """
    if not isinstance(model, dict):
        return model
    full = model.get("irfs") or {}
    reg = model.get("regimeIrfs") or {}
    replaced = []
    for regime, rv in list(reg.items()):
        if not isinstance(rv, dict):
            continue
        for var in ("inflation", "unemployment", "growth"):
            base = full.get(var) or []
            seq = rv.get(var) or []
            if not base or not seq:
                continue
            try:
                base_max = max(abs(float(x or 0)) for x in base)
                seq_max = max(abs(float(x or 0)) for x in seq)
            except Exception:
                continue
            if base_max > 0 and seq_max > 2.0 * base_max:
                rv[var] = list(base)
                replaced.append({
                    "regime": regime,
                    "variable": var,
                    "oldMaxAbs": seq_max,
                    "fullMaxAbs": base_max,
                })
    model["regimeIrfs"] = reg
    if replaced or not isinstance(model.get("irfStabilityGuard"), dict):
        model["irfStabilityGuard"] = {
            "method": "replace regime channels whose max abs per-25bp IRF exceeds 2x full-sample channel",
            "replaced": replaced,
        }
    return model

# ── FRED FETCHER ───────────────────────────────────────────────────────────────

def fetch_fred(series_id, start="1978-01-01"):
    url = (f"https://fred.stlouisfed.org/graph/fredgraph.csv"
           f"?id={series_id}&cosd={start}")
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
              "INDPRO", "GDPC1", "DGS2", "DGS10", "PAYEMS", "DTWEXBGS",
              "DFEDTAR", "DFEDTARL", "DFEDTARU"]
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

    # Use only months where all core monthly macro series have actually been
    # released. Forward-filling to today creates fake future/current-month macro
    # observations, which then look like real data in the game.
    latest_core_months = [raw[s].index.max().to_period("M").to_timestamp() for s in core]
    latest_common_month = min(latest_core_months)
    idx = pd.date_range("1979-01-01", latest_common_month, freq="MS")
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

    # STEP 1: growth proxy. A raw 1-month annualized log-diff (x12) amplifies a
    # single noisy monthly print into swings of -170%/+76%, which is not usable
    # as a headline "growth" number and contaminates every downstream regression
    # (VAR, FCI, Okun) that reads indpro_m. Use a 6-month annualized rate instead:
    # far less noisy (std ~5.5 vs ~11.7, avg month-to-month move ~1.4 vs ~8.1
    # points) while remaining meaningfully more responsive than 12-month YoY.
    ip = raw["INDPRO"].resample("MS").last().reindex(idx, method="ffill")
    panel["ip"] = ip["INDPRO"]
    panel["growth_yoy"] = 100 * (panel["ip"] / panel["ip"].shift(12) - 1)
    panel["indpro_m"] = 200 * np.log(panel["ip"] / panel["ip"].shift(6))

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

    # STEP 8: Real FOMC decision = official target-rate change only.
    # Never use FEDFUNDS monthly average differences for FOMC decisions; they create
    # impossible values such as +127bps or -16bps because they average partial months.
    panel = _apply_official_policy_decisions(panel, raw)

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

def _finite_or_raise(value, label):
    v = float(value)
    if not np.isfinite(v):
        raise ValueError(f"non-finite calibrated parameter: {label}")
    return v


def _robust_std(series):
    x = pd.to_numeric(series, errors="coerce").dropna().astype(float)
    if len(x) < 3:
        return np.nan
    med = float(x.median())
    mad = float((x - med).abs().median())
    if np.isfinite(mad) and mad > 0:
        return 1.4826 * mad
    return float(x.std())


def estimate_policy_calibration(panel):
    """Data-derived anchors/ranges exported to frontend.

    These are not Fed decisions and not official targets; they are runtime
    calibration anchors estimated from the loaded historical sample so frontend
    scoring/preview does not depend on hardcoded 2%, 5%, 0.985, etc.
    """
    p = panel.copy()
    inf = pd.to_numeric(p.get("inflation"), errors="coerce").dropna()
    un = pd.to_numeric(p.get("unemployment"), errors="coerce").dropna()
    gr = pd.to_numeric(p.get("indpro_m", p.get("growth_yoy")), errors="coerce").dropna()
    ff = pd.to_numeric(p.get("fedFunds"), errors="coerce").dropna()
    y2 = pd.to_numeric(p.get("y2", p.get("fedFunds")), errors="coerce").dropna()
    sp = pd.to_numeric(p.get("yieldSpread"), errors="coerce").dropna()

    decisions = pd.to_numeric(p.get("realDecision"), errors="coerce").dropna().abs() * 100.0
    decisions = decisions[decisions > 0]
    step = float(decisions.median()) if len(decisions) else 25.0

    return {
        "source": "estimated from loaded historical macro panel",
        "inflationAnchor": _finite_or_raise(inf.median(), "inflationAnchor"),
        "unemploymentAnchor": _finite_or_raise(un.median(), "unemploymentAnchor"),
        "growthAnchor": _finite_or_raise(gr.median(), "growthAnchor"),
        "policyRateFloor": float(min(0.0, ff.quantile(0.01))) if len(ff) else 0.0,
        "policyStepBps": step,
        "seriesStd": {
            "inflation": _finite_or_raise(_robust_std(inf), "seriesStd.inflation"),
            "unemployment": _finite_or_raise(_robust_std(un), "seriesStd.unemployment"),
            "growth": _finite_or_raise(_robust_std(gr), "seriesStd.growth"),
            "fedFunds": _finite_or_raise(_robust_std(ff), "seriesStd.fedFunds"),
            "yieldSpread": _finite_or_raise(_robust_std(sp), "seriesStd.yieldSpread") if len(sp) else 1.0,
            "y2": _finite_or_raise(_robust_std(y2), "seriesStd.y2") if len(y2) else 1.0,
        },
        "empiricalBounds": {
            "inflation": [float(inf.quantile(0.01)), float(inf.quantile(0.99))],
            "unemployment": [float(un.quantile(0.01)), float(un.quantile(0.99))],
            "growth": [float(gr.quantile(0.01)), float(gr.quantile(0.99))],
            "fedFunds": [float(ff.quantile(0.01)), float(ff.quantile(0.99))],
            "yieldSpread": [float(sp.quantile(0.01)), float(sp.quantile(0.99))] if len(sp) else [0.0, 0.0],
        },
    }


# ── STEP 2: EXPECTATIONS CHANNEL ──────────────────────────────────────────────

def estimate_expectations_params(panel):
    """Estimate expectation dynamics from data with no hand-tuned clipping.

    Δinflation is regressed on lagged fed funds, lagged curve slope and lagged
    inflation. If estimation is unstable/non-finite, strict mode fails instead
    of silently replacing the model with constants.
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
    alpha = abs(_finite_or_raise(coeffs[1], "exp alpha_ff"))
    spread_alpha = _finite_or_raise(coeffs[2], "exp alpha_spread")
    persistence = _finite_or_raise(coeffs[3], "exp persistence")
    # Stability is a mathematical requirement for a forward simulation. This is
    # a guardrail, not economic calibration: explosive AR(1) paths are rejected.
    if abs(persistence) >= 1:
        persistence = float(np.sign(persistence) * 0.99)
    print(f"  Expectations: alpha_ff={alpha:.4f}, alpha_spread={spread_alpha:.4f}, persistence={persistence:.3f}")
    return {"alpha_ff": alpha, "alpha_spread": spread_alpha, "persistence": persistence}


# ── STEP 3: COMPLETE FCI ──────────────────────────────────────────────────────

def estimate_fci_weights(panel):
    """Estimate FCI and Okun coefficients from the loaded macro panel."""
    from numpy.linalg import lstsq
    cols = ["indpro_m","y2","yieldSpread","dollar_yoy"]
    p = panel.dropna(subset=cols).copy()
    X = np.column_stack([np.ones(len(p)), p["y2"].values,
                         p["yieldSpread"].values, p["dollar_yoy"].values])
    y = p["indpro_m"].values
    coeffs, _, _, _ = lstsq(X, y, rcond=None)
    beta_y2     = -_finite_or_raise(coeffs[1], "fci y2")
    beta_spread = -_finite_or_raise(coeffs[2], "fci spread")
    beta_dollar = -_finite_or_raise(coeffs[3], "fci dollar")
    # Sign guard: higher short rates and a stronger dollar should tighten the FCI
    # (beta_y2, beta_dollar >= 0); a widening/positive curve should ease it and an
    # inversion should tighten it (beta_spread <= 0). A single-equation OLS over a
    # long, regime-mixed sample can pick up the wrong sign here just as it can for
    # the Taylor-rule coefficients above; guard against that instead of silently
    # shipping a backwards financial-conditions channel.
    if beta_y2 < 0:
        beta_y2 = abs(beta_y2)
        print("  FCI weights: y2 sign flipped (was negative)")
    if beta_spread > 0:
        beta_spread = -abs(beta_spread)
        print("  FCI weights: spread sign flipped (was positive)")
    if beta_dollar < 0:
        beta_dollar = abs(beta_dollar)
        print("  FCI weights: dollar sign flipped (was negative)")

    q = p.copy()
    q["du"] = q["unemployment"].diff()
    q["growth_shortfall"] = -q["indpro_m"] / 12.0
    q = q.dropna(subset=["du", "growth_shortfall"])
    if len(q) >= 24:
        Xo = np.column_stack([np.ones(len(q)), q["growth_shortfall"].values])
        co, _, _, _ = lstsq(Xo, q["du"].values, rcond=None)
        okun_gamma = max(0.0, _finite_or_raise(co[1], "okun gamma"))
    else:
        raise ValueError("not enough observations to estimate Okun coefficient")
    print(f"  FCI weights: y2={beta_y2:.4f}, spread={beta_spread:.4f}, dollar={beta_dollar:.4f}, okun={okun_gamma:.4f}")
    return {"y2": beta_y2, "spread": beta_spread, "dollar": beta_dollar, "okun": okun_gamma}


# ── STEP 10: DATA-DRIVEN APPROVAL/TAYLOR-LIKE REACTION ────────────────────────

def estimate_approval_weights(panel):
    from numpy.linalg import lstsq
    p = panel.dropna(subset=["inflation","unemployment","fedFunds"]).copy()
    cal = estimate_policy_calibration(panel)
    inf_anchor = cal["inflationAnchor"]
    un_anchor = cal["unemploymentAnchor"]
    X = np.column_stack([np.ones(len(p)), p["inflation"].values - inf_anchor, p["unemployment"].values - un_anchor])
    y = p["fedFunds"].values
    coeffs, _, _, _ = lstsq(X, y, rcond=None)
    inf_gap = _finite_or_raise(coeffs[1], "taylor inf_gap")
    unemp_gap = _finite_or_raise(coeffs[2], "taylor unemp_gap")
    const = _finite_or_raise(coeffs[0], "taylor const")
    # Sign guard: a textbook Taylor rule requires inf_gap >= 0 (hike when inflation
    # is above target) and unemp_gap <= 0 (ease when unemployment is above target).
    # A raw single-equation OLS over a long, regime-mixed sample can pick up the
    # wrong sign from endogeneity (funds rate and unemployment both rising together
    # during a disinflation episode). An unguarded wrong-signed unemp_gap would make
    # the game's Taylor-rule scoring reward the player for hiking into unemployment.
    # This mirrors the sign normalization already applied to the VAR IRFs above.
    if inf_gap < 0:
        inf_gap = abs(inf_gap)
        print("  Taylor-like reaction: inf_gap sign flipped (was negative)")
    if unemp_gap > 0:
        unemp_gap = -abs(unemp_gap)
        print("  Taylor-like reaction: unemp_gap sign flipped (was positive)")
    print(f"  Taylor-like reaction: const={const:.2f} inf_gap={inf_gap:.2f} unemp_gap={unemp_gap:.2f}")
    inf_std    = _finite_or_raise(_robust_std(p["inflation"]), "zstd inflation")
    unemp_std  = _finite_or_raise(_robust_std(p["unemployment"]), "zstd unemployment")
    growth_std = _finite_or_raise(_robust_std(panel["growth_yoy"].dropna()), "zstd growth")
    inf_diff_std    = _finite_or_raise(_robust_std(p["inflation"].diff().dropna()), "monthlyDiffStd inflation")
    unemp_diff_std  = _finite_or_raise(_robust_std(p["unemployment"].diff().dropna()), "monthlyDiffStd unemployment")
    growth_diff_std = _finite_or_raise(_robust_std(panel["growth_yoy"].diff().dropna()), "monthlyDiffStd growth")
    return {
        "taylor": {"const": const, "inf_gap": inf_gap, "unemp_gap": unemp_gap},
        "zstd": {"inflation": inf_std, "unemployment": unemp_std, "growth": growth_std},
        "monthlyDiffStd": {"inflation": inf_diff_std, "unemployment": unemp_diff_std, "growth": growth_diff_std},
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

        print("Estimating policy calibration...")
        policy_calibration = estimate_policy_calibration(panel)

        return _stabilize_model_irfs({
            "irfs": full_irf,
            "regimeIrfs": regime_irfs,
            "fciWeights": fci_weights,
            "expParams": exp_params,
            "approvalWeights": approval_weights,
            "policyCalibration": policy_calibration,
        })
    except Exception as e:
        raise RuntimeError(f"Model estimation failed; strict-real-data mode refuses synthetic fallback parameters: {e}")

# ── FOMC MEETING DATES ─────────────────────────────────────────────────────────

FOMC_DATES_BY_ERA = {
    "volcker": {
        "1980-01","1980-02","1980-03","1980-05",
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


# Exact decision dates for periods where public/free ZQ futures history is most
# likely to be retrievable. Older eras remain "unavailable" unless the user adds
# a licensed fed_funds_futures_pricing.csv file.
FOMC_EXACT_DATES = {
    # Late Bernanke / GFC period
    "2008-01": "2008-01-30", "2008-03": "2008-03-18", "2008-04": "2008-04-30",
    "2008-06": "2008-06-25", "2008-08": "2008-08-05", "2008-09": "2008-09-16",
    "2008-10": "2008-10-29", "2008-12": "2008-12-16",
    "2009-01": "2009-01-28", "2009-03": "2009-03-18", "2009-04": "2009-04-29",
    "2009-06": "2009-06-24", "2009-08": "2009-08-12", "2009-09": "2009-09-23",
    "2009-11": "2009-11-04", "2010-01": "2010-01-27", "2010-03": "2010-03-16",
    "2010-04": "2010-04-28", "2010-06": "2010-06-23", "2010-08": "2010-08-10",
    "2010-09": "2010-09-21", "2010-11": "2010-11-03", "2011-01": "2011-01-26",
    "2011-03": "2011-03-15", "2011-04": "2011-04-27", "2011-06": "2011-06-22",
    "2011-08": "2011-08-09", "2011-09": "2011-09-21", "2011-11": "2011-11-02",
    "2011-12": "2011-12-13", "2012-01": "2012-01-25", "2012-03": "2012-03-13",
    "2012-04": "2012-04-25", "2012-06": "2012-06-20", "2012-08": "2012-08-01",
    "2012-09": "2012-09-13", "2012-10": "2012-10-24", "2012-12": "2012-12-12",
    "2013-01": "2013-01-30", "2013-03": "2013-03-20", "2013-05": "2013-05-01",
    "2013-06": "2013-06-19", "2013-07": "2013-07-31", "2013-09": "2013-09-18",
    "2013-10": "2013-10-30", "2013-12": "2013-12-18", "2014-01": "2014-01-29",
    "2014-03": "2014-03-19",

    # Powell era
    "2020-01": "2020-01-29", "2020-03": "2020-03-15", "2020-04": "2020-04-29",
    "2020-06": "2020-06-10", "2020-07": "2020-07-29", "2020-09": "2020-09-16",
    "2020-11": "2020-11-05", "2020-12": "2020-12-16", "2021-01": "2021-01-27",
    "2021-03": "2021-03-17", "2021-04": "2021-04-28", "2021-06": "2021-06-16",
    "2021-07": "2021-07-28", "2021-09": "2021-09-22", "2021-11": "2021-11-03",
    "2021-12": "2021-12-15", "2022-01": "2022-01-26", "2022-03": "2022-03-16",
    "2022-05": "2022-05-04", "2022-06": "2022-06-15", "2022-07": "2022-07-27",
    "2022-09": "2022-09-21", "2022-11": "2022-11-02", "2022-12": "2022-12-14",
    "2023-02": "2023-02-01", "2023-03": "2023-03-22", "2023-05": "2023-05-03",
    "2023-06": "2023-06-14", "2023-07": "2023-07-26", "2023-09": "2023-09-20",
    "2023-11": "2023-11-01", "2023-12": "2023-12-13", "2024-01": "2024-01-31",
    "2024-03": "2024-03-20", "2024-05": "2024-05-01", "2024-06": "2024-06-12",
    "2024-07": "2024-07-31", "2024-09": "2024-09-18", "2024-11": "2024-11-07",
    "2024-12": "2024-12-18", "2025-01": "2025-01-29", "2025-03": "2025-03-19",
    "2025-05": "2025-05-07", "2025-06": "2025-06-18", "2025-07": "2025-07-30",
    "2025-09": "2025-09-17", "2025-10": "2025-10-29", "2025-12": "2025-12-10",
    "2026-01": "2026-01-28", "2026-03": "2026-03-18", "2026-04": "2026-04-29",
    "2026-06": "2026-06-17", "2026-07": "2026-07-29", "2026-09": "2026-09-16",
    "2026-10": "2026-10-28", "2026-12": "2026-12-09",
}

ZQ_MONTH_CODES = {1:"F", 2:"G", 3:"H", 4:"J", 5:"K", 6:"M", 7:"N", 8:"Q", 9:"U", 10:"V", 11:"X", 12:"Z"}


def _market_cache_load():
    if MARKET_PRICING_CACHE.exists():
        try:
            with open(MARKET_PRICING_CACHE) as f:
                cache = json.load(f)
            # Drop stale proxy/event-study entries written by older builds.
            strict_cache = {}
            for key, val in (cache or {}).items():
                if not isinstance(val, dict):
                    continue
                if val.get("status") != "ok":
                    strict_cache[key] = val
                    continue
                quality = str(val.get("quality") or "").strip().lower()
                if quality in STRICT_MARKET_PRICING_QUALITIES:
                    strict_cache[key] = val
            return strict_cache
        except Exception:
            return {}
    return {}


def _market_cache_save(cache):
    try:
        _atomic_json_dump(MARKET_PRICING_CACHE, cache)
    except Exception as e:
        print(f"Market-pricing cache save failed: {e}")


def _contract_symbol_for_date(ts):
    ts = pd.Timestamp(ts)
    return f"ZQ{ZQ_MONTH_CODES[int(ts.month)]}{str(ts.year)[-2:]}.CBT"


def _next_month(ts):
    ts = pd.Timestamp(ts)
    year = ts.year + (1 if ts.month == 12 else 0)
    month = 1 if ts.month == 12 else ts.month + 1
    return pd.Timestamp(year=year, month=month, day=1)


def _fetch_yahoo_futures_close(symbol, before_date, lookback_days=21):
    """Return latest daily close strictly before before_date using Yahoo Finance chart data."""
    before = pd.Timestamp(before_date)
    start = before - pd.Timedelta(days=lookback_days)
    period1 = int(start.timestamp())
    period2 = int((before + pd.Timedelta(days=1)).timestamp())
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {"period1": period1, "period2": period2, "interval": "1d", "events": "history"}
    headers = {"User-Agent": "Mozilla/5.0 FedChairSimulator/1.0"}
    r = requests.get(url, params=params, headers=headers, timeout=12)
    if r.status_code != 200:
        raise RuntimeError(f"Yahoo HTTP {r.status_code} for {symbol}")
    payload = r.json()
    res = (payload.get("chart") or {}).get("result") or []
    if not res:
        err = (payload.get("chart") or {}).get("error") or {}
        raise RuntimeError(err.get("description") or f"No Yahoo result for {symbol}")
    timestamps = res[0].get("timestamp") or []
    closes = (((res[0].get("indicators") or {}).get("quote") or [{}])[0]).get("close") or []
    rows = []
    for t, c in zip(timestamps, closes):
        if c is None:
            continue
        d = pd.to_datetime(int(t), unit="s").normalize()
        if d < before.normalize():
            rows.append((d, float(c)))
    if not rows:
        raise RuntimeError(f"No pre-meeting close for {symbol}")
    rows.sort(key=lambda x: x[0])
    d, close = rows[-1]
    return d.strftime("%Y-%m-%d"), close


def _fetch_effr_pre_rate(meeting_date):
    """Average real EFFR from start of month through the day before/decision day.
    Falls back to None if FRED is unavailable. Caller may use FEDFUNDS monthly data.
    """
    md = pd.Timestamp(meeting_date)
    start = md.replace(day=1).strftime("%Y-%m-%d")
    end = (md - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    if pd.Timestamp(end) < pd.Timestamp(start):
        return None, "unavailable"
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id=EFFR&cosd={start}&coed={end}"
    r = requests.get(url, timeout=12)
    if r.status_code != 200:
        return None, f"FRED EFFR HTTP {r.status_code}"
    from io import StringIO
    df = pd.read_csv(StringIO(r.text))
    if "EFFR" not in df.columns:
        return None, "FRED EFFR missing"
    vals = pd.to_numeric(df["EFFR"].replace(".", np.nan), errors="coerce").dropna()
    if vals.empty:
        return None, "FRED EFFR empty"
    return float(vals.mean()), "FRED EFFR daily average"


def _manual_market_pricing_lookup(era_name, ym):
    if not USER_MARKET_PRICING_FILE.exists():
        return None
    try:
        df = pd.read_csv(USER_MARKET_PRICING_FILE)
        match = df[(df.get("era") == era_name) & (df.get("ym") == ym)]
        if match.empty:
            match = df[df.get("ym") == ym]
        if match.empty:
            return None
        row = {k: _clean_for_json(v) for k, v in match.iloc[0].to_dict().items()}
        raw_outcomes = row.get("outcomes_json") or row.get("outcomes")
        if isinstance(raw_outcomes, str) and raw_outcomes.strip():
            try:
                row["outcomes"] = json.loads(raw_outcomes)
            except Exception:
                row["outcomes_parse_error"] = "Could not parse outcomes_json"
        quality = str(row.get("quality") or "").strip().lower()
        if quality and quality not in STRICT_MARKET_PRICING_QUALITIES:
            # Bundled USMPD/event-study rows are allowed to carry official actual
            # move metadata, but they must not block a live direct-futures fetch
            # or masquerade as market pricing.
            return None
        # Normalize common CSV variants.
        if row.get("implied_rate") is not None and row.get("implied_avg_rate") is None:
            row["implied_avg_rate"] = row.get("implied_rate")
        return row
    except Exception as e:
        print(f"Manual market-pricing file unreadable: {e}")
        return None



def compute_real_market_pricing(era_name, ym):
    """Real-data only market pricing before an FOMC decision.

    It never fabricates probabilities. If futures history cannot be obtained, it
    returns status='unavailable' and the UI clearly says so.
    """
    manual = _manual_market_pricing_lookup(era_name, ym)
    if manual:
        if manual.get("status") == "unavailable":
            return manual
        return {"status": "ok", "quality": "user_provided_real_data", "ym": ym, **manual}

    key = f"{era_name}:{ym}"
    cache = _market_cache_load()
    if key in cache:
        return cache[key]

    meeting_date = FOMC_EXACT_DATES.get(ym)
    if not meeting_date:
        result = {"status": "unavailable", "ym": ym, "reason": "Exact FOMC date not mapped; strict mode does not create proxy market pricing."}
        cache[key] = result; _market_cache_save(cache); return result

    md = pd.Timestamp(meeting_date)
    if md < pd.Timestamp("2008-01-01"):
        result = {"status": "unavailable", "ym": ym, "meeting_date": meeting_date, "reason": "Pre-2008 direct public ZQ/FedWatch-style history unavailable; strict mode does not create proxy market pricing."}
        cache[key] = result; _market_cache_save(cache); return result

    try:
        # Same-month ZQ decomposition is accepted only when enough post-meeting
        # days remain in that contract month. Strict mode refuses next-month
        # substitution because that would be a proxy, not direct comparable pricing.
        days_in_month = calendar.monthrange(md.year, md.month)[1]
        pre_days = int(md.day)
        post_days = int(days_in_month - pre_days)
        contract_month_date = md if post_days >= 5 else _next_month(md)
        symbol = _contract_symbol_for_date(contract_month_date)
        asof_date, close_price = _fetch_yahoo_futures_close(symbol, md)
        implied_avg_rate = 100.0 - close_price
        pre_rate, pre_rate_source = _fetch_effr_pre_rate(meeting_date)
        if pre_rate is None or not math.isfinite(pre_rate):
            raise RuntimeError(f"Could not fetch pre-meeting EFFR for {ym}; strict mode refuses monthly FEDFUNDS fallback")

        if post_days >= 5:
            expected_post_rate = ((implied_avg_rate * days_in_month) - (pre_rate * pre_days)) / max(post_days, 1)
            calc_note = "Meeting-month ZQ contract adjusted for realized/pre-meeting EFFR days."
        else:
            raise RuntimeError("Meeting is too close to month-end for same-month ZQ decomposition; strict mode refuses next-month proxy")
        market_implied_bps = (expected_post_rate - pre_rate) * 100.0
        # A single fed-funds-futures price gives an implied average rate, not a
        # full probability distribution. Do not fabricate FedWatch-style buckets.
        outcomes = []
        result = {
            "status": "ok",
            "quality": "real_zq_futures",
            "ym": ym,
            "meeting_date": meeting_date,
            "asof_date": asof_date,
            "source": "Yahoo Finance historical ZQ contract close + FRED EFFR",
            "source_url_hint": "Yahoo chart API for individual ZQ futures; FRED EFFR daily series",
            "contract": symbol,
            "price": round(close_price, 5),
            "implied_avg_rate": round(implied_avg_rate, 4),
            "pre_meeting_rate": round(pre_rate, 4),
            "pre_rate_source": pre_rate_source,
            "expected_post_rate": round(expected_post_rate, 4),
            "market_implied_bps": round(market_implied_bps, 1),
            "outcomes": outcomes,
            "calc_note": calc_note,
        }
    except Exception as e:
        result = {
            "status": "unavailable",
            "ym": ym,
            "meeting_date": meeting_date,
            "reason": f"Direct fed-funds-futures fetch failed ({e}); strict mode does not create proxy market pricing.",
        }
    cache[key] = result
    _market_cache_save(cache)
    return result


# ── REAL MARKET PRICING CSV BUILDER ────────────────────────────────────────────

MARKET_PRICING_CSV_COLUMNS = [
    "era", "ym", "meeting_date", "asof_date", "status", "quality", "source",
    "contract", "price", "implied_avg_rate", "pre_meeting_rate",
    "expected_post_rate", "market_implied_bps", "outcomes_json", "reason"
]


def _csv_bool_arg(name, default=False):
    raw = str(request.args.get(name, "")).strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "y", "on"}


def _market_pricing_result_to_csv_row(era_name, ym, result):
    outcomes = result.get("outcomes")
    if not outcomes and isinstance(result.get("outcomes_json"), str):
        try:
            outcomes = json.loads(result.get("outcomes_json"))
        except Exception:
            outcomes = None
    row = {
        "era": era_name,
        "ym": ym,
        "meeting_date": result.get("meeting_date") or FOMC_EXACT_DATES.get(ym),
        "asof_date": result.get("asof_date"),
        "status": result.get("status", "unavailable"),
        "quality": result.get("quality"),
        "source": result.get("source"),
        "contract": result.get("contract"),
        "price": result.get("price"),
        "implied_avg_rate": result.get("implied_avg_rate") or result.get("implied_rate"),
        "pre_meeting_rate": result.get("pre_meeting_rate"),
        "expected_post_rate": result.get("expected_post_rate"),
        "market_implied_bps": result.get("market_implied_bps"),
        "outcomes_json": json.dumps(outcomes, separators=(",", ":")) if outcomes else "",
        "reason": result.get("reason"),
    }
    return {col: _clean_for_json(row.get(col)) for col in MARKET_PRICING_CSV_COLUMNS}


def _iter_meeting_months_for_csv(era_filter=None, start_ym=None, end_ym=None, include_future=False):
    today = pd.Timestamp.utcnow().tz_localize(None).normalize()
    era_names = [era_filter] if era_filter and era_filter in ERA_WINDOWS else list(ERA_WINDOWS.keys())
    for era_name in era_names:
        months = sorted(FOMC_DATES_BY_ERA.get(era_name, []))
        for ym in months:
            if ym not in FOMC_EXACT_DATES:
                continue
            if start_ym and ym < start_ym:
                continue
            if end_ym and ym > end_ym:
                continue
            md = pd.Timestamp(FOMC_EXACT_DATES[ym])
            if (not include_future) and md >= today:
                continue
            yield era_name, ym


def build_market_pricing_csv_rows(era_filter=None, start_ym=None, end_ym=None, include_unavailable=False, refresh=False, include_future=False):
    """Fetch real pre-FOMC market pricing and return CSV rows.

    This function does not fabricate probabilities. Rows are emitted only when a
    real source succeeds unless include_unavailable=True.
    """
    if refresh:
        MARKET_PRICING_CACHE.unlink(missing_ok=True)
    rows = []
    status_counts = {"ok": 0, "unavailable": 0}
    for era_name, ym in _iter_meeting_months_for_csv(era_filter, start_ym, end_ym, include_future):
        res = compute_real_market_pricing(era_name, ym)
        status = res.get("status", "unavailable")
        status_counts[status] = status_counts.get(status, 0) + 1
        if status == "ok" or include_unavailable:
            rows.append(_market_pricing_result_to_csv_row(era_name, ym, res))
    rows.sort(key=lambda r: (r.get("era") or "", r.get("ym") or ""))
    return rows, status_counts


def _rows_to_csv_text(rows):
    from io import StringIO
    buf = StringIO()
    writer = csv.DictWriter(buf, fieldnames=MARKET_PRICING_CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for r in rows:
        writer.writerow({col: "" if r.get(col) is None else r.get(col) for col in MARKET_PRICING_CSV_COLUMNS})
    return buf.getvalue()


def save_market_pricing_csv(rows):
    text = _rows_to_csv_text(rows)
    USER_MARKET_PRICING_FILE.write_text(text, encoding="utf-8")
    runtime_path = DATA_DIR / "fed_funds_futures_pricing.csv"
    runtime_path.write_text(text, encoding="utf-8")
    return USER_MARKET_PRICING_FILE, runtime_path

# ── STEP 9: EVENTS WITH MODEL EFFECTS ─────────────────────────────────────────

HISTORICAL_EVENTS = {
    "1980-01": {"headline":"Iranian hostage crisis deepens oil shock","type":"shock"},
    "1980-04": {"headline":"Credit controls imposed — economy seizes","type":"policy"},
    "1981-01": {"headline":"Reagan takes office; promises to back inflation fight","type":"policy"},
    "1982-08": {"headline":"Mexico debt crisis: EM contagion risk rises","type":"market"},
    "1982-12": {"headline":"Unemployment hits 10.8% — highest since Great Depression","type":"shock"},
    "1987-10": {"headline":"Black Monday: Dow −22.6% in one session","type":"market"},
    "1989-08": {"headline":"S&L Crisis: over 1,000 thrifts fail","type":"market"},
    "1990-08": {"headline":"Iraq invades Kuwait — oil price surges","type":"shock"},
    "1997-07": {"headline":"Asian Financial Crisis: baht collapses, EM contagion","type":"market"},
    "1998-09": {"headline":"Russia default, LTCM collapse — systemic risk","type":"market"},
    "2000-03": {"headline":"Dot-com bubble peaks — Nasdaq at 5,048","type":"market"},
    "2001-09": {"headline":"9/11 attacks — markets closed 4 days","type":"shock"},
    "2005-08": {"headline":"Hurricane Katrina — energy supply shock","type":"shock"},
    "2007-08": {"headline":"Subprime cracks: BNP Paribas freezes funds","type":"market"},
    "2008-09": {"headline":"Lehman Brothers files $639B bankruptcy","type":"shock"},
    "2010-05": {"headline":"European sovereign debt crisis — contagion fears","type":"market"},
    "2011-08": {"headline":"US credit rating downgraded by S&P","type":"shock"},
    "2013-05": {"headline":"Taper Tantrum: 10Y yield spikes 100bps on tapering hint","type":"market"},
    "2020-03": {"headline":"COVID-19 declared pandemic — economy shuttered","type":"shock"},
    "2021-03": {"headline":"Stimulus checks + reopening: demand surge begins","type":"shock"},
    "2022-02": {"headline":"Russia invades Ukraine — energy & food price shock","type":"shock"},
    "2023-03": {"headline":"SVB fails: fastest bank run in history","type":"market"},
}

# ── DATA CACHE ─────────────────────────────────────────────────────────────────

_cache = {}

def _sanitize_cached_real_decisions(panel):
    """Remove fake FEDFUNDS-average decision artifacts from old bundled caches.

    Old cache files may contain realDecision values computed from monthly average
    FEDFUNDS. Keep only official-looking 25bp-step values; non-step artifacts are
    set to unavailable. Runtime /api/refresh will rebuild from official FRED
    target-rate series.
    """
    if "realDecision" not in panel.columns:
        panel["realDecision"] = np.nan
    panel["realDecisionSource"] = panel.get("realDecisionSource", "cached/sanitized")
    vals = pd.to_numeric(panel["realDecision"], errors="coerce")
    src = panel["realDecisionSource"].astype(str).str.lower()
    official_src = src.str.contains("official target", na=False) | src.str.contains("fred official", na=False) | src.str.contains("target-rate", na=False)
    unofficial = vals.notna() & ~official_src
    panel.loc[unofficial, "realDecision"] = np.nan
    panel.loc[unofficial, "realDecisionSource"] = "unavailable: cached value was not official target-rate data"

    vals = pd.to_numeric(panel["realDecision"], errors="coerce")
    bps = vals * 100.0
    stepped = bps.apply(_round_policy_move_bps)
    bad = bps.notna() & stepped.notna() & ((bps - stepped).abs() > 1e-6)
    panel.loc[bad, "realDecision"] = np.nan
    panel.loc[bad, "realDecisionSource"] = "unavailable: cached monthly FEDFUNDS artifact removed"

    # Overlay official target moves from the bundled CSV decision table first.
    # This does not use FedWatch/event-study market-implied columns.
    def _overlay_decisions_from_csv(path, source_label):
        if not path.exists():
            return
        df = pd.read_csv(path)
        if "ym" not in df.columns or "actual_move_bps" not in df.columns:
            return
        move_map = {str(r["ym"]): _round_policy_move_bps(r["actual_move_bps"]) for _, r in df.iterrows()}
        post_map = {str(r["ym"]): r.get("post_target_upper") for _, r in df.iterrows() if "post_target_upper" in df.columns}
        pre_map = {str(r["ym"]): r.get("pre_meeting_rate") for _, r in df.iterrows() if "pre_meeting_rate" in df.columns}
        for idx in panel.index:
            ym = pd.Timestamp(idx).strftime("%Y-%m")
            bps = move_map.get(ym)
            if bps is not None and math.isfinite(float(bps)):
                panel.at[idx, "realDecision"] = float(bps) / 100.0
                panel.at[idx, "realDecisionSource"] = source_label
                if ym in post_map and pd.notna(post_map[ym]):
                    panel.at[idx, "policyTargetUpper"] = float(post_map[ym])
                    panel.at[idx, "policyTargetMid"] = float(post_map[ym])
                elif ym in pre_map and pd.notna(pre_map[ym]):
                    panel.at[idx, "policyTargetUpper"] = float(pre_map[ym]) + float(bps) / 100.0
                    panel.at[idx, "policyTargetMid"] = float(pre_map[ym]) + float(bps) / 100.0

    try:
        _overlay_decisions_from_csv(_OFFICIAL_DECISIONS_CSV, "bundled official target move CSV")
        _overlay_decisions_from_csv(USER_MARKET_PRICING_FILE, "bundled official target move table")
    except Exception as e:
        print(f"Cached decision overlay failed: {e}")
    return panel


def _validate_monthly_panel(panel, label="cache"):
    """Reject cached macro panels with missing monthly rows.

    A missing row silently skips an FOMC month and hides the real Fed decision.
    The old bundled cache skipped 2025-10; strict mode must rebuild from source
    instead of pretending the time series is continuous.
    """
    if panel.empty:
        raise ValueError(f"{label} cache is empty")
    expected = pd.date_range(panel.index.min(), panel.index.max(), freq="MS")
    missing = expected.difference(panel.index)
    if len(missing):
        sample = ", ".join(d.strftime("%Y-%m") for d in missing[:12])
        more = "..." if len(missing) > 12 else ""
        raise ValueError(f"{label} cache has missing monthly rows: {sample}{more}")

CACHE_VERSION = "v6_nan_guard"  # rejects stale runtime caches from earlier broken builds

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
    panel = panel.set_index("date").sort_index()
    _validate_monthly_panel(panel, label or "cache")
    panel = _sanitize_cached_real_decisions(panel)
    model = _stabilize_model_irfs(model)
    return panel, model


def _try_load_csv_panel(irf_path, csv_path, label="bundled_csv"):
    """Load the app's frozen official-data CSV snapshot.

    This is the preferred offline path: FRED data are downloaded once into CSV,
    committed with the app, and used at runtime without internet access.
    """
    with open(irf_path) as f:
        model = json.load(f)
    if model.get("cacheVersion") != CACHE_VERSION:
        raise ValueError(f"{label} version mismatch ({model.get('cacheVersion')} vs {CACHE_VERSION})")
    print(f"Loading official CSV dataset from {csv_path} [{label}]")
    panel = pd.read_csv(csv_path)
    if "date" not in panel.columns:
        raise ValueError(f"{label} CSV missing date column")
    panel["date"] = pd.to_datetime(panel["date"])
    panel = panel.set_index("date").sort_index()
    _validate_monthly_panel(panel, label or "csv")
    panel = _sanitize_cached_real_decisions(panel)
    model = _stabilize_model_irfs(model)
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

    # 2. Bundled official CSV snapshot — preferred offline path.
    # This avoids runtime internet dependency while keeping data inspectable/editable.
    if _BUNDLED_CSV_PANEL.exists() and _BUNDLED_IRF.exists():
        try:
            panel, model = _try_load_csv_panel(_BUNDLED_IRF, _BUNDLED_CSV_PANEL, "bundled_csv")
            _cache["panel"] = panel
            _cache["model"] = model
            print("Loaded bundled CSV dataset — no FRED fetch needed.")
            return panel, model
        except Exception as e:
            print(f"Bundled CSV unusable: {e} — falling back")

    # 3. Bundled JSON assets committed to the repo — compatibility fallback.
    if _BUNDLED_CACHE.exists() and _BUNDLED_IRF.exists():
        try:
            panel, model = _try_load_cache(_BUNDLED_IRF, _BUNDLED_CACHE, "bundled_json")
            _cache["panel"] = panel
            _cache["model"] = model
            print("Loaded bundled JSON dataset — no FRED fetch needed.")
            return panel, model
        except Exception as e:
            print(f"Bundled JSON unusable: {e} — will fetch from FRED")

    # 4. Last resort: fetch live from FRED and estimate model
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
    "volcker":   ("1980-01-01", "1987-08-01"),
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
            "realDecision":   (None if pd.isna(row.get("realDecision", np.nan)) else _f(row.get("realDecision"))),
            "realDecisionAvailable": bool(not pd.isna(row.get("realDecision", np.nan))),
            "realDecisionSource": str(row.get("realDecisionSource", "unavailable")),
            "policyTargetUpper": (None if pd.isna(row.get("policyTargetUpper", row.get("policyTargetMid", np.nan))) else _f(row.get("policyTargetUpper", row.get("policyTargetMid")))),
            "policyTargetMid": (None if pd.isna(row.get("policyTargetMid", np.nan)) else _f(row.get("policyTargetMid"))),
            "macroDataComplete": bool(row.get("macroDataComplete", True)),
            "dataQualityNote": str(row.get("dataQualityNote", "")),
            "regime":         get_regime_for_date(date.strftime("%Y-%m-%d")),
            "isMeeting":      1 if (ym in fomc_months or (not pd.isna(row.get("realDecision", np.nan)) and abs(float(row.get("realDecision", 0) or 0)) > 1e-9)) else 0,
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


@app.route("/api/market-pricing/<era_name>/<ym>")
def market_pricing(era_name, ym):
    if era_name not in ERA_WINDOWS:
        return jsonify({"status": "unavailable", "error": "Unknown era"}), 400
    if not isinstance(ym, str) or len(ym) != 7 or ym[4] != "-":
        return jsonify({"status": "unavailable", "error": "ym must be YYYY-MM"}), 400
    return jsonify(_clean_for_json(compute_real_market_pricing(era_name, ym)))

@app.route("/api/market-pricing-cache/clear", methods=["POST"])
def clear_market_pricing_cache():
    MARKET_PRICING_CACHE.unlink(missing_ok=True)
    return jsonify({"status": "cleared"})


@app.route("/api/market-pricing-csv/build", methods=["GET", "POST"])
def build_market_pricing_csv_endpoint():
    """Build fed_funds_futures_pricing.csv from real public market data.

    Query params:
      era=powell|bernanke|greenspan|volcker (optional)
      start=YYYY-MM (optional)
      end=YYYY-MM (optional)
      include_unavailable=1 to include failed rows with reasons
      refresh=1 to clear the cache first
      include_future=1 to try future meetings too
    """
    era_filter = request.args.get("era") or None
    start_ym = request.args.get("start") or None
    end_ym = request.args.get("end") or None
    include_unavailable = _csv_bool_arg("include_unavailable", False)
    refresh = _csv_bool_arg("refresh", False)
    include_future = _csv_bool_arg("include_future", False)
    rows, counts = build_market_pricing_csv_rows(
        era_filter=era_filter,
        start_ym=start_ym,
        end_ym=end_ym,
        include_unavailable=include_unavailable,
        refresh=refresh,
        include_future=include_future,
    )
    root_path, runtime_path = save_market_pricing_csv(rows)
    return jsonify(_clean_for_json({
        "status": "ok",
        "rows_written": len(rows),
        "source_rule": "Real rows only unless include_unavailable=1. No fake probabilities are created.",
        "status_counts": counts,
        "root_csv": str(root_path.name),
        "runtime_csv": str(runtime_path.relative_to(ROOT)) if runtime_path.is_relative_to(ROOT) else str(runtime_path),
        "download_url": "/api/market-pricing-csv/download",
    }))


@app.route("/api/market-pricing-csv/download")
def download_market_pricing_csv_endpoint():
    """Download the generated real market-pricing CSV."""
    if USER_MARKET_PRICING_FILE.exists():
        text = USER_MARKET_PRICING_FILE.read_text(encoding="utf-8")
    elif (DATA_DIR / "fed_funds_futures_pricing.csv").exists():
        text = (DATA_DIR / "fed_funds_futures_pricing.csv").read_text(encoding="utf-8")
    else:
        rows, _ = build_market_pricing_csv_rows(era_filter=request.args.get("era") or "powell")
        text = _rows_to_csv_text(rows)
    return Response(
        text,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=fed_funds_futures_pricing.csv"},
    )


@app.route("/api/market-pricing-csv/status")
def market_pricing_csv_status_endpoint():
    exists = USER_MARKET_PRICING_FILE.exists()
    runtime_exists = (DATA_DIR / "fed_funds_futures_pricing.csv").exists()
    row_count = 0
    if exists:
        try:
            with open(USER_MARKET_PRICING_FILE, newline="") as f:
                row_count = max(0, sum(1 for _ in f) - 1)
        except Exception:
            row_count = None
    return jsonify({
        "status": "ok",
        "root_csv_exists": exists,
        "runtime_csv_exists": runtime_exists,
        "root_csv_rows": row_count,
        "build_url": "/api/market-pricing-csv/build?era=powell&start=2020-01&end=2026-12",
        "download_url": "/api/market-pricing-csv/download",
    })

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