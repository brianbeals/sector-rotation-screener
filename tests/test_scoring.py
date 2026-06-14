from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

import config
import scoring
from conftest import price_frame


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (config.SIGNAL_AVOID - 0.01, "Avoid"),
        (config.SIGNAL_AVOID, "Avoid"),
        (config.SIGNAL_AVOID + 0.01, "Hold"),
        (config.SIGNAL_BUY - 0.01, "Hold"),
        (config.SIGNAL_BUY, "Buy"),
        (config.SIGNAL_BUY + 0.01, "Buy"),
    ],
)
def test_composite_signal_boundaries(score, expected, monkeypatch):
    monkeypatch.setattr(
        config,
        "WEIGHTS",
        config.Weights(seasonality=1.0, cycle_fit=0.0, rel_strength=0.0),
    )
    result = scoring.composite_signal(score, 0.0, 0.0)
    assert result["composite"] == pytest.approx(score)
    assert result["signal"] == expected


def test_composite_signal_uses_configured_weights():
    result = scoring.composite_signal(80.0, 60.0, 40.0)
    expected = (
        config.WEIGHTS.seasonality * 80.0
        + config.WEIGHTS.cycle_fit * 60.0
        + config.WEIGHTS.rel_strength * 40.0
    )
    assert result["composite"] == pytest.approx(expected)


def test_seasonality_returns_neutral_for_empty_data():
    result = scoring.seasonality_score(pd.DataFrame(), target_month=1)
    assert result["score"] == 50.0
    assert result["n_years"] == 0
    assert result["thin_sample"] is True


def test_seasonality_requires_minimum_history(monkeypatch):
    monkeypatch.setattr(config, "SEASONALITY_MIN_YEARS", 5)
    index = pd.date_range("2020-01-31", periods=48, freq="ME")
    frame = pd.DataFrame({"Close": np.linspace(100.0, 160.0, len(index))}, index=index)
    result = scoring.seasonality_score(frame, target_month=2)
    assert result["score"] == 50.0
    assert result["n_years"] < 5
    assert result["thin_sample"] is True


def test_seasonality_respects_asof_cutoff():
    index = pd.date_range("2010-01-31", periods=180, freq="ME")
    frame = pd.DataFrame({"Close": np.linspace(100.0, 300.0, len(index))}, index=index)
    result = scoring.seasonality_score(
        frame,
        target_month=6,
        asof=pd.Timestamp("2018-12-31"),
    )
    assert result["n_years"] == 9


def test_relative_strength_outperformance_scores_above_neutral():
    sector = price_frame("2024-01-02", 150, daily_return=0.002)
    benchmark = price_frame("2024-01-02", 150, daily_return=0.001)
    result = scoring.rel_strength_scores(sector, benchmark)
    assert result["rs_1m"] > 0
    assert result["rs_3m"] > 0
    assert result["rs_6m"] > 0
    assert result["score"] > 50


def test_relative_strength_respects_asof_cutoff():
    sector = price_frame("2024-01-02", 180, daily_return=0.001)
    benchmark = price_frame("2024-01-02", 180, daily_return=0.001)
    sector.loc[sector.index[-20:], "Close"] *= 2
    cutoff = sector.index[-30]
    result = scoring.rel_strength_scores(sector, benchmark, asof=cutoff)
    assert result["score"] == pytest.approx(50.0)


@pytest.mark.parametrize(
    ("rsi", "pct_20d", "expected"),
    [
        (29.9, 0.00, "Oversold"),
        (50.0, 0.03, "Good entry"),
        (60.0, 0.04, "Reasonable"),
        (70.1, 0.00, "Extended"),
        (60.0, 0.051, "Extended"),
    ],
)
def test_timing_tags(rsi, pct_20d, expected):
    assert scoring._timing_tag(rsi, pct_20d, 0.0) == expected


def test_timing_tag_requires_enough_data():
    assert scoring._timing_tag(math.nan, 0.0, 0.0) == "—"


def test_fifty_two_week_high_uses_latest_252_rows():
    frame = price_frame("2023-01-02", 300, daily_return=0.0)
    frame.iloc[0, frame.columns.get_loc("Close")] = 1_000.0
    frame.iloc[-1, frame.columns.get_loc("Close")] = 90.0
    assert scoring.fifty_two_week_high_pct(frame) == pytest.approx(-0.10)
