from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

import backtest
import config
from conftest import price_frame


def test_month_return_uses_last_available_closes():
    close = pd.Series(
        [100.0, 105.0, 110.0],
        index=pd.to_datetime(["2024-01-30", "2024-02-15", "2024-02-29"]),
    )
    result = backtest._month_return(
        close,
        pd.Timestamp("2024-01-31"),
        pd.Timestamp("2024-02-29"),
    )
    assert result == pytest.approx(0.10)


def test_monthly_and_quarterly_rebalance_dates():
    monthly = backtest._rebalance_dates(
        pd.Timestamp("2024-01-01"), pd.Timestamp("2024-12-31"), "monthly"
    )
    quarterly = backtest._rebalance_dates(
        pd.Timestamp("2024-01-01"), pd.Timestamp("2024-12-31"), "quarterly"
    )
    assert len(monthly) == 12
    assert list(quarterly.month) == [3, 6, 9, 12]


def test_ticker_becomes_eligible_after_one_year(monkeypatch):
    monkeypatch.setattr(config, "SECTORS", {"TEST": "Test"})
    monkeypatch.setattr(config, "SECTOR_INCEPTION", {"TEST": date(2023, 1, 1)})
    assert backtest._eligible_tickers(pd.Timestamp("2023-12-31")) == []
    assert backtest._eligible_tickers(pd.Timestamp("2024-01-02")) == ["TEST"]


def test_summary_statistics_and_drawdown():
    index = pd.Index(
        [date(2024, 1, 31), date(2024, 2, 29), date(2024, 3, 31)],
        name="date",
    )
    frame = pd.DataFrame(
        {
            "strategy_ret": [0.0, 0.10, -0.20],
            "spy_ret": [0.0, 0.05, 0.05],
            "strategy_equity": [1.0, 1.1, 0.88],
            "spy_equity": [1.0, 1.05, 1.1025],
        },
        index=index,
    )
    result = backtest._summarize(frame)
    assert result["strategy_cum"] == pytest.approx(-0.12)
    assert result["strategy_maxdd"] == pytest.approx(-0.20)
    assert result["spy_cum"] == pytest.approx(0.1025)
    assert result["beats_spy_net"] is False


def test_run_backtest_requires_spy():
    with pytest.raises(RuntimeError, match="Need SPY"):
        backtest.run_backtest({}, {}, end_date="2024-12-31")


def test_run_backtest_uses_explicit_end_date(monkeypatch):
    monkeypatch.setattr(config, "BACKTEST_START", "2024-01-31")
    monkeypatch.setattr(config, "SECTORS", {})
    monkeypatch.setattr(config, "REBALANCE_FREQUENCY", "monthly")
    monkeypatch.setattr(
        backtest.cycle_mod,
        "classify_at_date",
        lambda vintage, asof: {"phase": "Mid-cycle"},
    )
    prices = {"SPY": price_frame("2024-01-02", 260, daily_return=0.001)}
    frame, _ = backtest.run_backtest(prices, {}, end_date="2024-06-30")
    assert frame.index[-1] == date(2024, 6, 30)
    assert len(frame) == 6


def test_run_backtest_limits_positions_and_applies_turnover_cost(monkeypatch):
    monkeypatch.setattr(config, "BACKTEST_START", "2024-01-31")
    monkeypatch.setattr(config, "SECTORS", {"A": "A", "B": "B", "C": "C"})
    monkeypatch.setattr(
        config,
        "SECTOR_INCEPTION",
        {"A": date(2020, 1, 1), "B": date(2020, 1, 1), "C": date(2020, 1, 1)},
    )
    monkeypatch.setattr(config, "MAX_POSITIONS", 2)
    monkeypatch.setattr(config, "TRADE_COST_BPS", 10.0)
    monkeypatch.setattr(config, "MIN_SCORE_TO_HOLD", 50.0)
    monkeypatch.setattr(
        backtest.cycle_mod,
        "classify_at_date",
        lambda vintage, asof: {"phase": "Mid-cycle"},
    )
    monkeypatch.setattr(
        backtest.scoring,
        "seasonality_score",
        lambda *args, **kwargs: {"score": 100.0},
    )
    monkeypatch.setattr(
        backtest.scoring,
        "cycle_fit_score",
        lambda *args, **kwargs: {"score": 100.0},
    )
    scores = iter([90.0, 80.0, 70.0] * 3)
    monkeypatch.setattr(
        backtest.scoring,
        "rel_strength_scores",
        lambda *args, **kwargs: {"score": next(scores)},
    )
    monkeypatch.setattr(
        backtest.scoring,
        "composite_signal",
        lambda season, cycle, rs: {"composite": rs, "signal": "Buy"},
    )
    prices = {
        "SPY": price_frame("2024-01-02", 180, daily_return=0.001),
        "A": price_frame("2024-01-02", 180, daily_return=0.002),
        "B": price_frame("2024-01-02", 180, daily_return=0.0015),
        "C": price_frame("2024-01-02", 180, daily_return=0.001),
    }
    frame, _ = backtest.run_backtest(prices, {}, end_date="2024-03-31")
    assert frame.iloc[1]["holdings"] == "A, B"
    assert frame.iloc[1]["n_held"] == 2
    assert frame.iloc[1]["cost_drag"] == pytest.approx(0.0)
    assert np.isfinite(frame.iloc[1]["strategy_ret"])


def test_forward_return_uses_close_at_horizon():
    close = pd.Series(
        [100.0, 110.0, 121.0],
        index=pd.to_datetime(["2024-01-05", "2024-02-16", "2024-03-01"]),
    )
    # 6 weeks from 2024-01-05 is 2024-02-16; last close <= that is 110 -> +10%.
    assert backtest._forward_return(close, pd.Timestamp("2024-01-05"), 6) == pytest.approx(0.10)


def test_forward_return_validation_requires_spy():
    with pytest.raises(RuntimeError, match="Need SPY"):
        backtest.forward_return_validation({}, {}, end_date="2024-06-30")


def _patch_signals(monkeypatch, signal, comp=90.0):
    monkeypatch.setattr(config, "BACKTEST_START", "2024-01-05")
    monkeypatch.setattr(config, "SECTORS", {"A": "A"})
    monkeypatch.setattr(config, "SECTOR_INCEPTION", {"A": date(2020, 1, 1)})
    monkeypatch.setattr(backtest.cycle_mod, "classify_at_date", lambda v, a: {"phase": "Mid-cycle"})
    monkeypatch.setattr(backtest.scoring, "seasonality_score", lambda *a, **k: {"score": 100.0})
    monkeypatch.setattr(backtest.scoring, "cycle_fit_score", lambda *a, **k: {"score": 100.0})
    monkeypatch.setattr(backtest.scoring, "rel_strength_scores", lambda *a, **k: {"score": 100.0})
    monkeypatch.setattr(backtest.scoring, "composite_signal",
                        lambda s, c, r: {"composite": comp, "signal": signal})


def test_forward_return_validation_scores_buy_signals(monkeypatch):
    _patch_signals(monkeypatch, "Buy")
    # A compounds faster than SPY, so a Buy call beats SPY over any forward window.
    prices = {
        "SPY": price_frame("2024-01-01", 220, daily_return=0.0005),
        "A":   price_frame("2024-01-01", 220, daily_return=0.0020),
    }
    per_call, summary = backtest.forward_return_validation(
        prices, {}, horizons_weeks=(6, 7, 8), end_date="2024-06-30")
    assert summary["n_calls"] > 0
    assert summary["mean_fwd"] > summary["mean_spy_fwd"]
    assert summary["mean_excess"] > 0
    assert summary["hit_rate_vs_spy"] == pytest.approx(1.0)
    assert summary["hit_rate_positive"] == pytest.approx(1.0)
    assert set(per_call["ticker"]) == {"A"}
    assert (per_call["excess"] > 0).all()


def test_forward_return_validation_no_buys_is_empty(monkeypatch):
    _patch_signals(monkeypatch, "Avoid", comp=10.0)
    prices = {
        "SPY": price_frame("2024-01-01", 220, daily_return=0.0005),
        "A":   price_frame("2024-01-01", 220, daily_return=0.0005),
    }
    per_call, summary = backtest.forward_return_validation(prices, {}, end_date="2024-06-30")
    assert summary["n_calls"] == 0
    assert per_call.empty
