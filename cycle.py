"""Economic cycle classifier — live and point-in-time.

Phase logic uses INDPRO YoY growth (%) as the activity indicator, the 10Y-2Y
Treasury spread as the curve LEVEL, and the 6-month change in that spread as a
LEADING curve-DIRECTION signal so transitions can fire ahead of lagging INDPRO:

  INDPRO YoY < -2% (any curve)       -> Recession    (deep contraction)
  curve <= 0 and YoY <= 0%           -> Recession    (contraction + inversion)
  curve steepening fast from a low   -> Early-cycle   (recovery leading INDPRO)
    spread while YoY still positive
  curve <= 0 and YoY > 0%            -> Late-cycle    (expansion but curve inverted)
  curve flattening fast while still  -> Late-cycle    (late-cycle warning ahead of
    positive and expanding                              INDPRO / before it inverts)
  INDPRO YoY > 4% and curve > 0      -> Early-cycle   (strong expansion)
  default                             -> Mid-cycle     (steady expansion)

Curve direction reuses DGS10/DGS2 (no new FRED series). Thresholds live in
config.CYCLE_THRESHOLDS (curve_flattening / curve_steepening / curve_low).
"""
from __future__ import annotations

from datetime import date
from typing import Dict, Optional

import pandas as pd

import config
from data import value_as_of


VALID_PHASES = ("Early-cycle", "Mid-cycle", "Late-cycle", "Recession")


# --- Internal helpers --------------------------------------------------------

def _decide(spread: Optional[float], indpro_yoy: Optional[float],
            spread_dir: Optional[float] = None) -> tuple:
    """Classify the phase. `spread_dir` is the 6-month change in the 10Y-2Y spread
    (positive = steepening, negative = flattening); None disables the leading rules
    and reproduces the level-only logic."""
    th = config.CYCLE_THRESHOLDS
    if indpro_yoy is None or spread is None:
        return "Mid-cycle", "Insufficient FRED data; defaulting to Mid-cycle."
    if indpro_yoy < th["indpro_contraction"]:
        return "Recession", (
            f"INDPRO YoY {indpro_yoy:+.1f}% below {th['indpro_contraction']}%: "
            f"deep contraction."
        )
    if spread <= th["yield_curve_inverted"] and indpro_yoy <= th["indpro_expansion"]:
        return "Recession", (
            f"Curve inverted ({spread:+.2f}) and INDPRO YoY {indpro_yoy:+.1f}% "
            f"<= {th['indpro_expansion']}%: both flashing recession."
        )
    # LEADING: curve steepening fast from a low/inverted spread while activity is still
    # positive -> early recovery, ahead of INDPRO turning up.
    if (spread_dir is not None and spread_dir >= th["curve_steepening"]
            and spread <= th["curve_low"]):
        return "Early-cycle", (
            f"Curve steepening {spread_dir:+.2f} over 6m from a low spread "
            f"({spread:+.2f}) with INDPRO YoY {indpro_yoy:+.1f}% positive: "
            f"recovery signal leading INDPRO."
        )
    if spread <= th["yield_curve_inverted"]:
        return "Late-cycle", (
            f"Curve inverted ({spread:+.2f}) but INDPRO YoY {indpro_yoy:+.1f}% "
            f"still positive: expansion late, recession warning live."
        )
    # LEADING: curve flattening fast while still positive and expanding -> late-cycle
    # warning before the curve actually inverts.
    if (spread_dir is not None and spread_dir <= th["curve_flattening"]
            and indpro_yoy > th["indpro_expansion"]):
        return "Late-cycle", (
            f"Curve flattening {spread_dir:+.2f} over 6m though still positive "
            f"({spread:+.2f}) with INDPRO YoY {indpro_yoy:+.1f}%: late-cycle "
            f"warning ahead of INDPRO."
        )
    if indpro_yoy > th["indpro_strong"]:
        return "Early-cycle", (
            f"INDPRO YoY {indpro_yoy:+.1f}% > {th['indpro_strong']}% "
            f"with positive curve: strong expansion."
        )
    return "Mid-cycle", (
        f"INDPRO YoY {indpro_yoy:+.1f}% between {th['indpro_expansion']}% "
        f"and {th['indpro_strong']}% with curve {spread:+.2f}: steady expansion."
    )


def _latest(series: pd.Series) -> Optional[float]:
    if series is None or len(series) == 0:
        return None
    s = series.dropna()
    return float(s.iloc[-1]) if len(s) else None


def _latest_date(series: pd.Series):
    if series is None or len(series) == 0:
        return None
    s = series.dropna()
    return s.index[-1].date() if len(s) else None


def _trend(series: pd.Series, months_back: int) -> Optional[float]:
    """Return the change in `series` over the last `months_back` months."""
    if series is None or len(series) == 0:
        return None
    s = series.dropna()
    if len(s) < 2:
        return None
    ref_date = s.index[-1] - pd.DateOffset(months=months_back)
    past = s.loc[s.index <= ref_date]
    if past.empty:
        return None
    return float(s.iloc[-1] - past.iloc[-1])


# --- Live classification -----------------------------------------------------

def _indpro_yoy(series: pd.Series) -> Optional[float]:
    """Compute the latest year-over-year % change from the INDPRO series."""
    if series is None or len(series) < 13:
        return None
    s = series.dropna()
    if len(s) < 13:
        return None
    current = float(s.iloc[-1])
    # Find the value closest to 12 months ago
    target_date = s.index[-1] - pd.DateOffset(months=12)
    past = s.loc[s.index <= target_date]
    if past.empty:
        return None
    year_ago = float(past.iloc[-1])
    if year_ago == 0:
        return None
    return ((current - year_ago) / year_ago) * 100.0


def classify(macro: Dict[str, pd.Series]) -> Dict[str, object]:
    """Classify the current cycle phase from the latest-revision FRED series.

    Honors config.CYCLE_PHASE_OVERRIDE — if set, the override wins but the
    algorithm's call is preserved alongside it for the dashboard.
    """
    dgs10 = _latest(macro.get("DGS10"))
    dgs2  = _latest(macro.get("DGS2"))
    indpro_yoy = _indpro_yoy(macro.get("INDPRO"))
    spread = (dgs10 - dgs2) if (dgs10 is not None and dgs2 is not None) else None

    spread_series = macro.get("DGS10") - macro.get("DGS2") if (
        isinstance(macro.get("DGS10"), pd.Series) and isinstance(macro.get("DGS2"), pd.Series)
    ) else None
    spread_6m_change = _trend(spread_series, 6) if spread_series is not None else None
    indpro_3m_change = _trend(macro.get("INDPRO"), 3)

    algo_phase, algo_why = _decide(spread, indpro_yoy, spread_6m_change)

    override = config.CYCLE_PHASE_OVERRIDE
    if override and override in VALID_PHASES:
        phase = override
        why = (
            f"Manual override active: phase forced to {override}. "
            f"Algorithm would have classified this as {algo_phase} "
            f"({algo_why.rstrip('.')})."
        )
    else:
        phase, why = algo_phase, algo_why

    return {
        "phase": phase,
        "algo_phase": algo_phase,
        "algo_why": algo_why,
        "override_active": bool(override and override in VALID_PHASES),
        "why": why,
        "inputs": {
            "DGS10":            dgs10,
            "DGS2":             dgs2,
            "spread":           spread,
            "spread_6m_chg":    spread_6m_change,
            "INDPRO_YoY":       indpro_yoy,
            "INDPRO_3m_chg":    indpro_3m_change,
            "DGS10_date":       _latest_date(macro.get("DGS10")),
            "DGS2_date":        _latest_date(macro.get("DGS2")),
            "INDPRO_date":      _latest_date(macro.get("INDPRO")),
        },
    }


# --- Point-in-time classification (backtest) ---------------------------------

def _indpro_yoy_from_series(series: pd.Series) -> Optional[float]:
    """Compute YoY % change from an already-resolved INDPRO series."""
    if series is None or len(series) < 13:
        return None
    s = series.dropna().sort_index()
    if len(s) < 13:
        return None
    current = float(s.iloc[-1])
    target_date = s.index[-1] - pd.DateOffset(months=12)
    past = s.loc[s.index <= target_date]
    if past.empty:
        return None
    year_ago = float(past.iloc[-1])
    if year_ago == 0:
        return None
    return ((current - year_ago) / year_ago) * 100.0


def classify_at_date(vintage: Dict[str, pd.DataFrame], asof: date) -> Dict[str, object]:
    """Classify cycle using only FRED data that was published as of `asof`.

    Override is INTENTIONALLY ignored in backtest mode — the override is a
    discretionary tool for the live screen, not a backtest assumption.
    """
    dgs10_s  = value_as_of(vintage.get("DGS10"), asof)
    dgs2_s   = value_as_of(vintage.get("DGS2"), asof)
    indpro_s = value_as_of(vintage.get("INDPRO"), asof)

    dgs10 = float(dgs10_s.iloc[-1]) if len(dgs10_s) else None
    dgs2  = float(dgs2_s.iloc[-1])  if len(dgs2_s)  else None
    indpro_yoy = _indpro_yoy_from_series(indpro_s)
    spread = (dgs10 - dgs2) if (dgs10 is not None and dgs2 is not None) else None

    # Curve direction (6-month change in the spread), point-in-time from the vintage series.
    spread_series = None
    if len(dgs10_s) and len(dgs2_s):
        spread_series = (dgs10_s - dgs2_s).dropna()
    spread_dir = _trend(spread_series, 6) if spread_series is not None and len(spread_series) else None

    phase, why = _decide(spread, indpro_yoy, spread_dir)
    return {"phase": phase, "why": why, "inputs": {
        "DGS10": dgs10, "DGS2": dgs2, "spread": spread,
        "spread_6m_chg": spread_dir, "INDPRO_YoY": indpro_yoy
    }}
