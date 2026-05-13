"""Walk-forward backtest of the composite-signal strategy vs SPY.

Mechanics:
  - Step through month-end dates from BACKTEST_YEARS ago to today.
  - At each month-end T (the rebalance date), use ONLY data <= T:
      * point-in-time vintage FRED to classify cycle phase (no override)
      * truncated price history to score each sector
  - Rank eligible sectors by composite. Eligible means:
      inception_date + 365 days <= T  (need 1 year of history before scoring)
  - Hold equal-weighted top-N where N = min(MAX_POSITIONS, # passing Buy threshold).
  - If zero pass, sit in cash for that month (0% return, no cost).
  - Apply TRADE_COST_BPS to TURNOVER at each rebalance (one-way trades).

Output:
  monthly DataFrame with columns:
    date, strategy_ret, spy_ret, strategy_equity, spy_equity, holdings (str),
    n_held, turnover, cost_drag, phase

  summary dict with: cum_return, cagr, sharpe, max_dd (each for strategy and
  SPY), beat_spy_net (bool).
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

import config
import scoring
import cycle as cycle_mod

log = logging.getLogger(__name__)


# --- Helpers -----------------------------------------------------------------

def _rebalance_dates(start: pd.Timestamp, end: pd.Timestamp,
                     frequency: str = "monthly") -> pd.DatetimeIndex:
    """Generate rebalance dates honoring config.REBALANCE_FREQUENCY.

    'monthly'   -> month-end dates
    'quarterly' -> calendar quarter-end dates (Mar, Jun, Sep, Dec)
    """
    freq = (frequency or "monthly").lower()
    if freq == "quarterly":
        rule = "QE" if hasattr(pd.tseries.offsets, "QuarterEnd") else "Q"
    else:
        rule = "ME" if hasattr(pd.tseries.offsets, "MonthEnd") else "M"
    return pd.date_range(start, end, freq=rule)


# Backwards-compat alias for any external caller still using _month_ends.
def _month_ends(start: pd.Timestamp, end: pd.Timestamp) -> pd.DatetimeIndex:
    return _rebalance_dates(start, end, "monthly")


def _eligible_tickers(asof: pd.Timestamp) -> List[str]:
    out = []
    for tk in config.SECTORS:
        inception = config.SECTOR_INCEPTION.get(tk)
        if inception is None:
            continue
        if (asof.date() - inception).days >= 365:
            out.append(tk)
    return out


def _close(price_df: pd.DataFrame) -> pd.Series:
    if price_df is None or price_df.empty:
        return pd.Series(dtype=float)
    return price_df["Close"] if "Close" in price_df.columns else price_df.iloc[:, 0]


def _month_return(close: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> float:
    """Total return between the last close <= start and last close <= end."""
    s = close[(close.index > start) & (close.index <= end)]
    pre = close[close.index <= start]
    if pre.empty or s.empty:
        return float("nan")
    p_start = float(pre.iloc[-1])
    p_end = float(s.iloc[-1])
    return p_end / p_start - 1.0


# --- Main loop ---------------------------------------------------------------

def run_backtest(prices: Dict[str, pd.DataFrame],
                 vintage_macro: Dict[str, pd.DataFrame],
                 years: int = config.BACKTEST_YEARS) -> Tuple[pd.DataFrame, Dict[str, float]]:
    """Run the walk-forward backtest. Returns (monthly_df, summary)."""
    spy = prices.get(config.BENCHMARK)
    if spy is None or spy.empty:
        raise RuntimeError("Need SPY price history for backtest.")

    end = pd.Timestamp(date.today()).normalize()
    start = end - pd.DateOffset(years=years)
    rebal_dates = _rebalance_dates(start, end, config.REBALANCE_FREQUENCY)

    cost_one_way = config.TRADE_COST_BPS / 10000.0
    spy_close = _close(spy)

    rows = []
    held: List[str] = []   # holdings entering this month (decided at prior month-end)
    weights: Dict[str, float] = {}

    for i, asof in enumerate(rebal_dates):
        # Returns for THIS month: based on what we entered with.
        if i == 0:
            strategy_ret = 0.0
            spy_ret = 0.0
        else:
            prev = rebal_dates[i - 1]
            spy_ret = _month_return(spy_close, prev, asof)
            if not held:
                strategy_ret = 0.0
            else:
                rets = []
                for tk in held:
                    r = _month_return(_close(prices.get(tk, pd.DataFrame())), prev, asof)
                    if np.isnan(r):
                        r = 0.0
                    rets.append(weights[tk] * r)
                strategy_ret = float(sum(rets))

        # Pick next month's holdings using data <= asof.
        cycle_info = cycle_mod.classify_at_date(vintage_macro, asof.date())
        phase = cycle_info["phase"]

        candidates = []
        for tk in _eligible_tickers(asof):
            df = prices.get(tk)
            if df is None or df.empty:
                continue
            df_pit = df[df.index <= asof]
            if df_pit.empty:
                continue
            bench_pit = spy[spy.index <= asof]
            season = scoring.seasonality_score(df_pit, target_month=(asof.month % 12) + 1, asof=asof)
            cf = scoring.cycle_fit_score(tk, phase)
            rs = scoring.rel_strength_scores(df_pit, bench_pit, asof=asof)
            comp = scoring.composite_signal(season["score"], cf["score"], rs["score"])
            candidates.append({
                "ticker": tk,
                "composite": comp["composite"],
                "signal": comp["signal"],
            })

        # Filter to Buys above MIN_SCORE_TO_HOLD, take top N
        passing = [c for c in candidates
                   if c["signal"] == "Buy" and c["composite"] >= config.MIN_SCORE_TO_HOLD]
        passing.sort(key=lambda c: c["composite"], reverse=True)
        next_held = [c["ticker"] for c in passing[:config.MAX_POSITIONS]]
        next_weights = ({tk: 1.0 / len(next_held) for tk in next_held}
                        if next_held else {})

        # Turnover: |new - old| weights, sum over all tickers
        all_tk = set(weights) | set(next_weights)
        turnover = sum(abs(next_weights.get(tk, 0.0) - weights.get(tk, 0.0)) for tk in all_tk)
        cost_drag = turnover * cost_one_way
        # Apply the cost drag to the just-completed month's return.
        if i > 0:
            strategy_ret -= cost_drag

        rows.append({
            "date":           asof.date(),
            "phase":          phase,
            "holdings":       ", ".join(held) if held else "CASH",
            "n_held":         len(held),
            "strategy_ret":   strategy_ret,
            "spy_ret":        spy_ret,
            "turnover":       turnover,
            "cost_drag":      cost_drag,
        })

        held = next_held
        weights = next_weights

    df = pd.DataFrame(rows).set_index("date")
    df["strategy_equity"] = (1.0 + df["strategy_ret"].fillna(0.0)).cumprod()
    df["spy_equity"]      = (1.0 + df["spy_ret"].fillna(0.0)).cumprod()

    summary = _summarize(df)
    return df, summary


# --- Summary stats -----------------------------------------------------------

def _summarize(df: pd.DataFrame) -> Dict[str, float]:
    out: Dict[str, float] = {}
    n = len(df)
    if n < 2:
        return out
    years = (df.index[-1] - df.index[0]).days / 365.25 or 1.0

    for label, ret_col, eq_col in [
        ("strategy", "strategy_ret", "strategy_equity"),
        ("spy",      "spy_ret",      "spy_equity"),
    ]:
        rets = df[ret_col].fillna(0.0)
        eq = df[eq_col]
        cum = float(eq.iloc[-1] - 1.0)
        cagr = float(eq.iloc[-1] ** (1.0 / years) - 1.0)
        # Scale to annual Sharpe based on rebalance frequency; rf = 0.
        periods_per_year = 4 if (config.REBALANCE_FREQUENCY or "monthly").lower() == "quarterly" else 12
        std = float(rets.std())
        sharpe = float(rets.mean() / std * np.sqrt(periods_per_year)) if std > 0 else float("nan")
        rolling_max = eq.cummax()
        drawdown = eq / rolling_max - 1.0
        max_dd = float(drawdown.min())
        out[f"{label}_cum"]    = cum
        out[f"{label}_cagr"]   = cagr
        out[f"{label}_sharpe"] = sharpe
        out[f"{label}_maxdd"]  = max_dd

    out["beats_spy_net"] = bool(out.get("strategy_cum", 0) > out.get("spy_cum", 0))
    out["years"] = years
    out["months"] = n
    return out
