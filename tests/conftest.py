from __future__ import annotations

import numpy as np
import pandas as pd


def price_frame(start: str, periods: int, start_value: float = 100.0,
                daily_return: float = 0.001) -> pd.DataFrame:
    index = pd.bdate_range(start, periods=periods)
    values = start_value * np.power(1.0 + daily_return, np.arange(periods))
    return pd.DataFrame(
        {"Close": values, "Volume": np.full(periods, 1_000_000.0)},
        index=index,
    )


def vintage_frame(rows) -> pd.DataFrame:
    return pd.DataFrame(
        rows,
        columns=["date", "realtime_start", "realtime_end", "value"],
    ).assign(
        date=lambda df: pd.to_datetime(df["date"]),
        realtime_start=lambda df: pd.to_datetime(df["realtime_start"]),
        realtime_end=lambda df: pd.to_datetime(df["realtime_end"]),
    )
