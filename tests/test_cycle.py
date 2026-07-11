from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

import config
import cycle
from conftest import vintage_frame


@pytest.mark.parametrize(
    ("spread", "growth", "expected"),
    [
        (1.0, 5.0, "Early-cycle"),
        (1.0, 2.0, "Mid-cycle"),
        (0.0, 2.0, "Late-cycle"),
        (0.0, 0.0, "Recession"),
        (1.0, -2.01, "Recession"),
        (None, 2.0, "Mid-cycle"),
    ],
)
def test_cycle_phase_decisions(spread, growth, expected):
    phase, _ = cycle._decide(spread, growth)
    assert phase == expected


@pytest.mark.parametrize(
    ("spread", "growth", "spread_dir", "expected"),
    [
        # Flattening fast while still positive and expanding -> Late-cycle, ahead of INDPRO.
        (0.6, 5.0, -0.8, "Late-cycle"),
        # Steepening fast from a low positive spread -> Early-cycle recovery.
        (0.2, 0.5, 0.8, "Early-cycle"),
        # Steepening out of inversion while activity positive -> Early-cycle (leads INDPRO).
        (-0.1, 1.0, 0.7, "Early-cycle"),
        # Inverted, NOT steepening, positive INDPRO -> Late-cycle (unchanged level logic).
        (-0.2, 1.0, -0.1, "Late-cycle"),
        # Mild direction changes do not override the level logic.
        (0.6, 5.0, -0.1, "Early-cycle"),
        (0.6, 2.0, 0.1, "Mid-cycle"),
    ],
)
def test_cycle_direction_leading_rules(spread, growth, spread_dir, expected):
    phase, _ = cycle._decide(spread, growth, spread_dir)
    assert phase == expected


def test_cycle_direction_defaults_to_level_only():
    # spread_dir=None reproduces the level-only classification.
    assert cycle._decide(0.6, 5.0)[0] == "Early-cycle"
    assert cycle._decide(0.6, 5.0, None)[0] == "Early-cycle"


def test_live_classification_honors_valid_override(monkeypatch):
    dates = pd.date_range("2023-01-31", periods=13, freq="ME")
    macro = {
        "DGS10": pd.Series([4.0] * 13, index=dates),
        "DGS2": pd.Series([3.0] * 13, index=dates),
        "INDPRO": pd.Series([100.0] * 12 + [105.0], index=dates),
    }
    monkeypatch.setattr(config, "CYCLE_PHASE_OVERRIDE", "Recession")
    result = cycle.classify(macro)
    assert result["algo_phase"] == "Early-cycle"
    assert result["phase"] == "Recession"
    assert result["override_active"] is True


def test_backtest_classification_ignores_override(monkeypatch):
    rows_10 = [("2023-01-31", "2023-02-01", "2999-12-31", 4.0)]
    rows_2 = [("2023-01-31", "2023-02-01", "2999-12-31", 3.0)]
    indpro_rows = []
    for i, dt in enumerate(pd.date_range("2022-01-31", periods=13, freq="ME")):
        value = 100.0 if i < 12 else 105.0
        indpro_rows.append((dt, dt + pd.Timedelta(days=1), "2999-12-31", value))
    vintage = {
        "DGS10": vintage_frame(rows_10),
        "DGS2": vintage_frame(rows_2),
        "INDPRO": vintage_frame(indpro_rows),
    }
    monkeypatch.setattr(config, "CYCLE_PHASE_OVERRIDE", "Recession")
    result = cycle.classify_at_date(vintage, date(2023, 3, 31))
    assert result["phase"] == "Early-cycle"


def test_indpro_yoy_handles_zero_base():
    dates = pd.date_range("2023-01-31", periods=13, freq="ME")
    values = [0.0] + [100.0] * 12
    assert cycle._indpro_yoy(pd.Series(values, index=dates)) is None
