"""Scoring layer — seasonality, cycle fit, relative strength, composite.

All functions are pure: pass in price frames, get back numbers. Backtest
calls these with truncated (point-in-time) price frames so there's no
lookahead.
"""
from __future__ import annotations

from datetime import date
from typing import Dict, Optional

import numpy as np
import pandas as pd

import config


# --- Seasonality --------------------------------------------------------------

def seasonality_score(price_df: pd.DataFrame,
                      target_month: Optional[int] = None,
                      asof: Optional[pd.Timestamp] = None) -> Dict[str, float]:
    """Average return + hit rate for `target_month` across history up to asof.

    Returns dict with avg_return, hit_rate (0-1), n_years, score (0-100), and
    `thin_sample` flag — True when fewer than SEASONALITY_TRUST_YEARS years.
    """
    if asof is not None:
        price_df = price_df[price_df.index <= asof]
    if target_month is None:
        target_month = date.today().month

    rets = _compute_monthly_returns(price_df)
    if rets.empty:
        return {"avg_return": np.nan, "hit_rate": np.nan,
                "n_years": 0, "score": 50.0, "thin_sample": True}

    by_month = rets[rets.index.month == target_month]
    n_years = by_month.index.year.nunique()

    if n_years < config.SEASONALITY_MIN_YEARS:
        return {"avg_return": np.nan, "hit_rate": np.nan,
                "n_years": int(n_years), "score": 50.0, "thin_sample": True}

    avg = float(by_month.mean())
    hit = float((by_month > 0).mean())
    avg_part = 50 + 50 * np.tanh(avg / 0.03)
    hit_part = 100 * hit
    score = float(0.6 * avg_part + 0.4 * hit_part)

    return {
        "avg_return":  avg,
        "hit_rate":    hit,
        "n_years":     int(n_years),
        "score":       score,
        "thin_sample": bool(n_years < config.SEASONALITY_TRUST_YEARS),
    }


# --- Cycle fit ----------------------------------------------------------------

def cycle_fit_score(ticker: str, current_phase: str) -> Dict[str, float]:
    favored = config.CYCLE_FAVORED.get(current_phase, [])
    if ticker in favored:
        return {"favored": True, "phase": current_phase, "score": 100.0}

    opposites = {
        "Early-cycle": "Late-cycle",
        "Mid-cycle":   "Recession",
        "Late-cycle":  "Early-cycle",
        "Recession":   "Mid-cycle",
    }
    opp_phase = opposites.get(current_phase)
    opp_favored = config.CYCLE_FAVORED.get(opp_phase, []) if opp_phase else []
    if ticker in opp_favored:
        return {"favored": False, "phase": current_phase, "score": 35.0}
    return {"favored": False, "phase": current_phase, "score": 50.0}


# --- Relative strength --------------------------------------------------------

def rel_strength_scores(sector_prices: pd.DataFrame,
                        bench_prices: pd.DataFrame,
                        asof: Optional[pd.Timestamp] = None) -> Dict[str, float]:
    if asof is not None:
        sector_prices = sector_prices[sector_prices.index <= asof]
        bench_prices  = bench_prices[bench_prices.index <= asof]

    s_close = sector_prices["Close"] if "Close" in sector_prices.columns else sector_prices.iloc[:, 0]
    b_close = bench_prices["Close"]  if "Close"  in bench_prices.columns  else bench_prices.iloc[:, 0]

    out: Dict[str, float] = {}
    weighted = 0.0
    weight_sum = 0.0
    for label, days in config.RS_WINDOWS_DAYS.items():
        s_ret = _trailing_return(s_close, days)
        b_ret = _trailing_return(b_close, days)
        diff = s_ret - b_ret
        out[f"rs_{label}"] = float(diff)
        sub_score = 50 + 50 * np.tanh(diff / 0.10)
        w = config.RS_WEIGHTS[label]
        weighted += w * sub_score
        weight_sum += w
    out["score"] = float(weighted / weight_sum) if weight_sum else 50.0
    return out


# --- Composite ----------------------------------------------------------------

def composite_signal(seasonality: float, cycle: float, rs: float) -> Dict[str, float]:
    w = config.WEIGHTS
    score = w.seasonality * seasonality + w.cycle_fit * cycle + w.rel_strength * rs
    if score >= config.SIGNAL_BUY:
        signal = "Buy"
    elif score <= config.SIGNAL_AVOID:
        signal = "Avoid"
    else:
        signal = "Hold"
    return {"composite": float(score), "signal": signal}


# --- Helpers ------------------------------------------------------------------

def _compute_monthly_returns(price_df: pd.DataFrame) -> pd.Series:
    if price_df is None or price_df.empty:
        return pd.Series(dtype=float)
    close = price_df["Close"] if "Close" in price_df.columns else price_df.iloc[:, 0]
    rule = "ME" if hasattr(pd.tseries.offsets, "MonthEnd") else "M"
    monthly = close.resample(rule).last().dropna()
    return monthly.pct_change().dropna()


def _trailing_return(close: pd.Series, days: int) -> float:
    if len(close) < days + 1:
        return float("nan")
    today = close.iloc[-1]
    past = close.iloc[-days - 1]
    return float(today / past - 1.0)


def seasonality_heatmap_table(prices_by_ticker: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows = {}
    for tk, df in prices_by_ticker.items():
        rets = _compute_monthly_returns(df)
        if rets.empty:
            continue
        per_month = rets.groupby(rets.index.month).mean()
        rows[tk] = [float(per_month.get(m, np.nan)) for m in range(1, 13)]
    return pd.DataFrame(rows, index=range(1, 13)).T


# --- Entry timing indicators --------------------------------------------------

def entry_timing(price_df: pd.DataFrame, rsi_period: int = 14) -> Dict[str, float]:
    """Compute short-term timing indicators for purchase decisions.

    Returns dict with:
      rsi        — 14-day RSI (0-100)
      pct_20d    — % distance from 20-day SMA
      pct_50d    — % distance from 50-day SMA
      vol_ratio  — 5-day avg volume / 20-day avg volume
      timing_tag — "Good entry" / "Reasonable" / "Extended" / "Oversold"
    """
    close = price_df["Close"] if "Close" in price_df.columns else price_df.iloc[:, 0]

    # RSI (Wilder smoothing)
    rsi = _rsi(close, rsi_period)

    # Distance from moving averages
    pct_20d = _pct_from_sma(close, 20)
    pct_50d = _pct_from_sma(close, 50)

    # Volume ratio (5-day vs 20-day)
    vol_ratio = float("nan")
    if "Volume" in price_df.columns:
        vol = price_df["Volume"]
        if len(vol) >= 20:
            avg5  = float(vol.tail(5).mean())
            avg20 = float(vol.tail(20).mean())
            if avg20 > 0:
                vol_ratio = avg5 / avg20

    # Timing tag logic
    tag = _timing_tag(rsi, pct_20d, pct_50d)

    return {
        "rsi":        rsi,
        "pct_20d":    pct_20d,
        "pct_50d":    pct_50d,
        "vol_ratio":  vol_ratio,
        "timing_tag": tag,
    }


def _rsi(close: pd.Series, period: int = 14) -> float:
    """Wilder-smoothed RSI."""
    if len(close) < period + 1:
        return float("nan")
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    # Wilder smoothing (exponential with alpha = 1/period)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()

    last_gain = float(avg_gain.iloc[-1])
    last_loss = float(avg_loss.iloc[-1])
    if last_loss == 0:
        return 100.0
    rs = last_gain / last_loss
    return float(100 - 100 / (1 + rs))


def _pct_from_sma(close: pd.Series, window: int) -> float:
    """Percent distance of last close from its simple moving average."""
    if len(close) < window:
        return float("nan")
    sma = float(close.tail(window).mean())
    if sma <= 0:
        return float("nan")
    return float(close.iloc[-1] / sma - 1.0)


def _timing_tag(rsi: float, pct_20d: float, pct_50d: float) -> str:
    """Classify entry timing based on RSI and MA distances.

    Categories:
      Oversold    — RSI < 30 or > 5% below 20-day MA (potential bounce, watch)
      Good entry  — RSI 30-50 and within +3% of 20-day MA (pullback in uptrend)
      Reasonable  — RSI 50-70 and within +5% of 20-day MA (fair price)
      Extended    — RSI > 70 or > 5% above 20-day MA (wait for pullback)
    """
    if np.isnan(rsi) or np.isnan(pct_20d):
        return "—"

    # Oversold: deep pullback, may be a bounce candidate
    if rsi < 30 or pct_20d < -0.05:
        return "Oversold"

    # Extended: overbought or stretched above the 20-day
    if rsi > 70 or pct_20d > 0.05:
        return "Extended"

    # Good entry: pulling back within an uptrend
    if rsi <= 50 and pct_20d <= 0.03:
        return "Good entry"

    # Everything else is reasonable
    return "Reasonable"


def fifty_two_week_high_pct(price_df: pd.DataFrame) -> float:
    close = price_df["Close"] if "Close" in price_df.columns else price_df.iloc[:, 0]
    if len(close) < 252:
        window = close
    else:
        window = close.tail(252)
    high = float(window.max())
    last = float(close.iloc[-1])
    if high <= 0:
        return float("nan")
    return last / high - 1.0
