"""Data layer — yfinance prices and FRED macro series (live + vintage)."""
from __future__ import annotations

import logging
import os
import pickle
from datetime import date, timedelta
from typing import Dict, List, Optional

import pandas as pd

import config

log = logging.getLogger(__name__)


# --- Prices -------------------------------------------------------------------

def _fetch_yahoo_direct(ticker: str, start: date, end: date) -> Optional[pd.DataFrame]:
    """Fetch OHLCV data for a single ticker directly from Yahoo Finance API.

    Bypasses yfinance's curl_cffi dependency, which has TLS issues on some
    macOS configurations. Uses the standard requests library instead.
    """
    import requests
    import time

    period1 = int(time.mktime(start.timetuple()))
    period2 = int(time.mktime(end.timetuple()))
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        f"?period1={period1}&period2={period2}&interval=1d"
        f"&includeAdjustedClose=true"
    )
    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.error("Direct Yahoo fetch failed for %s: %s", ticker, exc)
        return None

    result = data.get("chart", {}).get("result")
    if not result:
        log.error("No chart data returned for %s", ticker)
        return None

    meta = result[0]
    timestamps = meta.get("timestamp", [])
    quote = meta.get("indicators", {}).get("quote", [{}])[0]
    adjclose_list = meta.get("indicators", {}).get("adjclose", [{}])
    adjclose = adjclose_list[0].get("adjclose", []) if adjclose_list else []

    if not timestamps:
        return None

    df = pd.DataFrame({
        "Open":   quote.get("open", []),
        "High":   quote.get("high", []),
        "Low":    quote.get("low", []),
        "Close":  adjclose if adjclose else quote.get("close", []),
        "Volume": quote.get("volume", []),
    }, index=pd.to_datetime(timestamps, unit="s", utc=True))

    df.index = df.index.tz_convert("America/New_York").tz_localize(None)
    df.index.name = "Date"
    return df.dropna(how="all")


def fetch_prices(tickers: List[str], years: int = config.HISTORY_YEARS) -> Dict[str, pd.DataFrame]:
    """Daily OHLCV history for each ticker. Returns a dict keyed by ticker."""
    end = date.today() + timedelta(days=1)
    start = end - timedelta(days=int(years * 365.25) + 10)

    log.info("Pulling %d tickers via direct Yahoo API, %s -> %s", len(tickers), start, end)

    out: Dict[str, pd.DataFrame] = {}
    for tk in tickers:
        df = _fetch_yahoo_direct(tk, start, end)
        if df is not None and not df.empty:
            out[tk] = df
            log.info("  %s: %d rows, %s -> %s", tk, len(df),
                     df.index.min().date(), df.index.max().date())

    missing = [t for t in tickers if t not in out]
    if missing:
        log.warning("No data for: %s", missing)
    return out


# --- Macro: live (latest revision) -------------------------------------------

def fetch_macro(api_key: Optional[str] = None) -> Dict[str, pd.Series]:
    """Latest-revision FRED series. Used for the LIVE screen, not backtest."""
    from fredapi import Fred

    key = api_key or config.FRED_API_KEY
    if not key:
        raise RuntimeError(
            "FRED_API_KEY not set. export FRED_API_KEY=your_key_here, then re-run."
        )

    fred = Fred(api_key=key)
    out: Dict[str, pd.Series] = {}
    for code in config.FRED_SERIES:
        try:
            s = fred.get_series(code).dropna()
            s.name = code
            out[code] = s
            log.info("FRED %s: %d obs, last=%.3f on %s",
                     code, len(s), s.iloc[-1], s.index[-1].date())
        except Exception as exc:
            log.error("Failed to pull FRED %s: %s", code, exc)
            out[code] = pd.Series(dtype=float, name=code)
    return out


# --- Macro: vintage (point-in-time, for backtest) ---------------------------

def fetch_macro_vintage(api_key: Optional[str] = None,
                        use_cache: bool = True) -> Dict[str, pd.DataFrame]:
    """Pull the FULL release history of each FRED series via ALFRED.

    Returns dict keyed by series code, each value a DataFrame with columns:
      date            -- the observation reference date
      realtime_start  -- first date this value was the published value
      realtime_end    -- last date this value was the published value
      value           -- the value as published in that vintage

    Resolve a point-in-time value with `value_as_of(df, ref_date, asof_date)`.
    """
    from fredapi import Fred

    key = api_key or config.FRED_API_KEY
    if not key:
        raise RuntimeError("FRED_API_KEY not set.")

    os.makedirs(config.CACHE_DIR, exist_ok=True)
    cache_file = os.path.join(config.CACHE_DIR, "fred_vintage.pkl")

    if use_cache and os.path.exists(cache_file):
        try:
            with open(cache_file, "rb") as fh:
                cached = pickle.load(fh)
            if cached.get("_fetched_on") == date.today().isoformat():
                log.info("Using cached vintage FRED (fetched today)")
                return {k: v for k, v in cached.items() if not k.startswith("_")}
        except Exception as exc:
            log.warning("Vintage cache unreadable, refetching: %s", exc)

    fred = Fred(api_key=key)

    # Daily series like DGS10/DGS2 have thousands of vintage dates, which
    # exceeds FRED's 2,000-vintage limit on get_series_all_releases().
    # We only need vintage data covering the backtest window, so we pull
    # in 2-year realtime chunks starting from (today - BACKTEST_YEARS).
    bt_start = date.today() - timedelta(days=int(config.BACKTEST_YEARS * 365.25) + 30)

    def _fetch_chunked(series_code: str) -> pd.DataFrame:
        """Pull vintage data in 2-year realtime chunks to stay under FRED's limit."""
        import requests as _req

        chunk_years = 2
        frames = []
        chunk_start = bt_start
        end_date = date.today()

        while chunk_start < end_date:
            chunk_end = min(
                date(chunk_start.year + chunk_years, chunk_start.month, chunk_start.day),
                end_date,
            )
            try:
                df = fred.get_series_all_releases(
                    series_code,
                    realtime_start=chunk_start.isoformat(),
                    realtime_end=chunk_end.isoformat(),
                )
                if df is not None and not df.empty:
                    frames.append(df)
            except Exception as exc:
                log.warning("Vintage chunk %s [%s -> %s] failed: %s",
                            series_code, chunk_start, chunk_end, exc)
            chunk_start = chunk_end + timedelta(days=1)

        if not frames:
            return pd.DataFrame(columns=["date", "realtime_start", "value"])
        return pd.concat(frames, ignore_index=True)

    out: Dict[str, pd.DataFrame] = {}
    for code in config.FRED_SERIES:
        try:
            df = _fetch_chunked(code)
            # fredapi columns: date, realtime_start, value
            # We need realtime_end derived from the next realtime_start per ref date.
            df = df.dropna(subset=["value"]).copy()
            df["date"]           = pd.to_datetime(df["date"])
            df["realtime_start"] = pd.to_datetime(df["realtime_start"])
            df = df.drop_duplicates(subset=["date", "realtime_start"])
            df = df.sort_values(["date", "realtime_start"])

            # Compute realtime_end per reference date as next realtime_start.
            df["realtime_end"] = (
                df.groupby("date")["realtime_start"].shift(-1) - pd.Timedelta(days=1)
            )
            far_future = pd.Timestamp("2999-12-31")
            df["realtime_end"] = df["realtime_end"].fillna(far_future)
            out[code] = df.reset_index(drop=True)
            log.info("FRED vintage %s: %d release rows, %s -> %s",
                     code, len(df), df["date"].min().date(), df["date"].max().date())
        except Exception as exc:
            log.error("Failed to pull vintage %s: %s", code, exc)
            out[code] = pd.DataFrame(columns=["date","realtime_start","realtime_end","value"])

    if use_cache:
        with open(cache_file, "wb") as fh:
            pickle.dump({**out, "_fetched_on": date.today().isoformat()}, fh)
    return out


def value_as_of(vintage_df: pd.DataFrame, asof_date) -> pd.Series:
    """Return the series of values that were published as of asof_date.

    Result is indexed by reference date and contains the value that was the
    "current" published value for that ref date as of asof_date.
    """
    if vintage_df is None or vintage_df.empty:
        return pd.Series(dtype=float)

    asof = pd.Timestamp(asof_date)
    visible = vintage_df[
        (vintage_df["realtime_start"] <= asof) & (vintage_df["realtime_end"] >= asof)
    ]
    if visible.empty:
        return pd.Series(dtype=float)

    # For each reference date, take the value that was visible at asof.
    result = (visible.sort_values("realtime_start")
                       .groupby("date", as_index=True)["value"].last())
    result.index = pd.to_datetime(result.index)
    return result.sort_index()


# --- CLI smoke test ----------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    prices = fetch_prices(["XLK", "SPY"], years=20)
    for t, df in prices.items():
        print(f"{t}: {len(df)} rows, {df.index.min().date()} -> {df.index.max().date()}")
    macro = fetch_macro()
    for k, s in macro.items():
        if len(s):
            print(f"{k}: latest={s.iloc[-1]:.2f} ({s.index[-1].date()})")
    print("Vintage spot check:")
    vintage = fetch_macro_vintage()
    for k, df in vintage.items():
        as_2020 = value_as_of(df, "2020-06-30")
        if len(as_2020):
            print(f"  {k} as of 2020-06-30: latest obs = {as_2020.iloc[-1]:.2f}")
