from __future__ import annotations

import pandas as pd

from data import value_as_of
from conftest import vintage_frame


def test_value_as_of_hides_future_revisions():
    frame = vintage_frame(
        [
            ("2024-01-01", "2024-02-01", "2024-03-14", 100.0),
            ("2024-01-01", "2024-03-15", "2999-12-31", 110.0),
        ]
    )
    before = value_as_of(frame, "2024-03-01")
    after = value_as_of(frame, "2024-03-20")
    assert before.iloc[-1] == 100.0
    assert after.iloc[-1] == 110.0


def test_value_as_of_hides_unpublished_observations():
    frame = vintage_frame(
        [
            ("2024-01-01", "2024-02-01", "2999-12-31", 100.0),
            ("2024-02-01", "2024-03-10", "2999-12-31", 101.0),
        ]
    )
    result = value_as_of(frame, "2024-03-01")
    assert list(result.index) == [pd.Timestamp("2024-01-01")]


def test_value_as_of_empty_input_returns_empty_series():
    assert value_as_of(pd.DataFrame(), "2024-01-01").empty
