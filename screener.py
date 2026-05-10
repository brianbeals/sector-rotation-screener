"""Sector Rotation Screener — main entry point.

Usage:
    export FRED_API_KEY=your_key_here
    python screener.py             # full run including backtest
    python screener.py --no-backtest

Pulls 20 years of price data + FRED macro + FRED vintage history, classifies
the cycle phase, scores each sector, runs the walk-forward backtest, and
writes Excel + HTML to the outputs folder.
"""
from __future__ import annotations

import argparse
import logging
import math
import os
from datetime import date, datetime
from typing import Dict, List

import pandas as pd

import config
import cycle as cycle_mod
import data
import scoring
import report
import backtest as bt_mod
import drilldown


log = logging.getLogger("screener")


def get_top_holdings(sector_etf: str) -> List[Dict[str, str]]:
    """Phase-2 placeholder. Returns the top 10 holdings of an ETF.

    Wire this up to a real source (yfinance .funds_data, ETF.com scrape, or
    a paid feed) when you start drilling into individual names.
    """
    log.info("get_top_holdings(%s) — stub returns empty list", sector_etf)
    return []


def _vintage_info(prices: Dict[str, pd.DataFrame], macro: Dict[str, pd.Series]) -> Dict[str, str]:
    last_price = max((df.index.max() for df in prices.values() if not df.empty), default=None)
    last_fred  = max((s.index.max() for s in macro.values() if len(s)), default=None)
    return {
        "generated_at":   datetime.now().strftime("%Y-%m-%d %H:%M"),
        "prices_through": last_price.strftime("%Y-%m-%d") if last_price is not None else "—",
        "fred_vintage":   last_fred.strftime("%Y-%m-%d")  if last_fred  is not None else "—",
    }


def run_screen(skip_backtest: bool = False) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if not config.FRED_API_KEY:
        raise SystemExit("FRED_API_KEY not set. export FRED_API_KEY=your_key_here, then retry.")

    # 1. Live data
    log.info("Fetching prices for %d tickers...", len(config.ALL_TICKERS))
    prices = data.fetch_prices(config.ALL_TICKERS, years=config.HISTORY_YEARS)
    log.info("Fetching latest-revision FRED series...")
    macro  = data.fetch_macro()

    bench = prices.get(config.BENCHMARK)
    if bench is None or bench.empty:
        raise SystemExit("Could not pull SPY data; aborting.")

    # 2. Cycle phase (current)
    cycle_info = cycle_mod.classify(macro)
    log.info("Cycle phase: %s — %s", cycle_info["phase"], cycle_info["why"])

    # 3. Score every sector
    rows: List[Dict] = []
    sector_prices: Dict[str, pd.DataFrame] = {}
    for tk, name in config.SECTORS.items():
        df = prices.get(tk)
        if df is None or df.empty:
            log.warning("No price data for %s; skipping.", tk)
            continue
        sector_prices[tk] = df

        season = scoring.seasonality_score(df)
        cf = scoring.cycle_fit_score(tk, cycle_info["phase"])
        rs = scoring.rel_strength_scores(df, bench)
        comp = scoring.composite_signal(season["score"], cf["score"], rs["score"])

        rows.append({
            "ticker":            tk,
            "name":              name,
            "last_price":        float(df["Close"].iloc[-1]),
            "pct_from_52w_high": scoring.fifty_two_week_high_pct(df),
            "seasonality_score": season["score"],
            "seasonality_avg":   season["avg_return"],
            "seasonality_hit":   season["hit_rate"],
            "seasonality_n":     season["n_years"],
            "seasonality_thin":  season.get("thin_sample", False),
            "cycle_fit_score":   cf["score"],
            "cycle_favored":     cf["favored"],
            "rs_score":          rs["score"],
            "rs_1m":             rs.get("rs_1m", float("nan")),
            "rs_3m":             rs.get("rs_3m", float("nan")),
            "rs_6m":             rs.get("rs_6m", float("nan")),
            "composite":         comp["composite"],
            "signal":            comp["signal"],
        })
    rows.sort(key=lambda r: r["composite"], reverse=True)

    heat = scoring.seasonality_heatmap_table(sector_prices)
    vintage = _vintage_info(prices, macro)

    # 4. Backtest
    bt_df, bt_summary = None, None
    if not skip_backtest:
        log.info("Pulling FRED vintage history for backtest...")
        vintage_macro = data.fetch_macro_vintage()
        log.info("Running %d-year walk-forward backtest...", config.BACKTEST_YEARS)
        bt_df, bt_summary = bt_mod.run_backtest(prices, vintage_macro)
        log.info("Backtest done. Strategy cum: %.2f%% | SPY cum: %.2f%% | beats SPY: %s",
                 100 * bt_summary.get("strategy_cum", 0),
                 100 * bt_summary.get("spy_cum", 0),
                 bt_summary.get("beats_spy_net"))

    # 5. Drill-down: sub-sector ETFs + top holdings for Buy + near-Buy sectors
    dd_sectors = [r for r in rows if r["composite"] >= config.DRILLDOWN_THRESHOLD]
    dd_results = {}
    if dd_sectors:
        n_buy   = sum(1 for r in dd_sectors if r["signal"] == "Buy")
        n_watch = len(dd_sectors) - n_buy
        log.info("Running drill-down for %d sectors (%d Buy, %d Watch)...",
                 len(dd_sectors), n_buy, n_watch)
        dd_results = drilldown.run_drilldown(dd_sectors, prices)

    # 6. Write reports
    today = date.today().isoformat()
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    xlsx_path = os.path.join(config.OUTPUT_DIR, f"SectorScreen_{today}.xlsx")
    html_path = os.path.join(config.OUTPUT_DIR, f"SectorScreen_{today}.html")
    json_path = os.path.join(config.OUTPUT_DIR, f"SectorScreen_{today}.json")

    log.info("Writing Excel -> %s", xlsx_path)
    report.write_excel(rows, cycle_info, vintage, xlsx_path,
                       backtest_df=bt_df, backtest_summary=bt_summary)
    log.info("Writing HTML  -> %s", html_path)
    report.write_html(rows, cycle_info, heat, vintage, html_path,
                      backtest_df=bt_df, backtest_summary=bt_summary,
                      drilldown_data=dd_results)

    # JSON sidecar for downstream automation (weekly_run.py, etc.)
    import json as _json
    log.info("Writing JSON  -> %s", json_path)
    with open(json_path, "w") as f:
        _json.dump({
            "date": today,
            "cycle": cycle_info,
            "vintage": vintage,
            "rows": rows,
            "backtest_summary": bt_summary or {},
            "drilldown": dd_results,
        }, f, default=str, indent=2)

    # 7. Console (with ANSI stoplight colors)
    # Green = Buy, Yellow = Hold, Red = Avoid
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    def _sig(signal: str) -> str:
        if signal == "Buy":
            return f"{GREEN}{BOLD}{signal:>6}{RESET}"
        elif signal == "Avoid":
            return f"{RED}{signal:>6}{RESET}"
        return f"{YELLOW}{signal:>6}{RESET}"

    def _sig_short(signal: str) -> str:
        if signal == "Buy":
            return f"{GREEN}{BOLD}{signal:>5}{RESET}"
        elif signal == "Avoid":
            return f"{RED}{signal:>5}{RESET}"
        return f"{YELLOW}{signal:>5}{RESET}"

    print()
    print(f"Cycle phase: {BOLD}{cycle_info['phase']}{RESET}"
          + (" (override active)" if cycle_info.get("override_active") else ""))
    print(f"  {DIM}({cycle_info['why']}){RESET}")
    print()
    hdr = f"{'ETF':4} {'Name':24} {'Comp':>6} {'Sig':>6} {'Season':>10} {'Cycle':>6} {'RS':>6} {'RS3m':>8}"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        flag = " ⚠" if r["seasonality_thin"] else ""
        s_str = f"{r['seasonality_score']:5.1f}({r['seasonality_n']}){flag}"
        print(f"{r['ticker']:4} {r['name'][:24]:24} "
              f"{r['composite']:6.1f} {_sig(r['signal'])} "
              f"{s_str:>10} {r['cycle_fit_score']:6.1f} "
              f"{r['rs_score']:6.1f} {100*r['rs_3m']:+7.2f}%")
    if bt_summary:
        print()
        print(f"Backtest ({config.BACKTEST_YEARS}y): "
              f"Strategy {bt_summary.get('strategy_cum',0)*100:+.2f}% / "
              f"SPY {bt_summary.get('spy_cum',0)*100:+.2f}% — "
              f"{'BEATS SPY' if bt_summary.get('beats_spy_net') else 'DOES NOT beat SPY'}")

    # Drill-down console output
    for parent_tk, dd in dd_results.items():
        sector_name = config.SECTORS.get(parent_tk, parent_tk)
        # Look up the parent's composite to determine Buy vs Watch label
        parent_row = next((r for r in rows if r["ticker"] == parent_tk), None)
        parent_comp = parent_row["composite"] if parent_row else 0
        if parent_comp >= config.SIGNAL_BUY:
            dd_label = f"{GREEN}{BOLD}BUY{RESET}"
        else:
            dd_label = f"{YELLOW}{BOLD}WATCH{RESET}"
        print()
        print(f"{'='*70}")
        print(f"  DRILL-DOWN: {BOLD}{parent_tk} ({sector_name}){RESET} — {dd_label}")
        print(f"{'='*70}")
        if dd["subsectors"]:
            print(f"\n  Sub-sector ETFs (ranked by composite):")
            sub_hdr = (f"  {'ETF':5} {'Theme':22} {'Comp':>6} {'Sig':>5} "
                       f"{'RSI':>5} {'vs20d':>7} {'vs50d':>7} {'Vol':>5} {'Timing':>12}")
            print(sub_hdr)
            print(f"  {'-'*len(sub_hdr.strip())}")
            for s in dd["subsectors"]:
                _r = s.get("rsi");      rsi_val = f"{_r:5.0f}" if _r is not None and not math.isnan(_r) else "    —"
                _p2 = s.get("pct_20d"); p20 = f"{100*_p2:+6.1f}%" if _p2 is not None and not math.isnan(_p2) else "      —"
                _p5 = s.get("pct_50d"); p50 = f"{100*_p5:+6.1f}%" if _p5 is not None and not math.isnan(_p5) else "      —"
                _v = s.get("vol_ratio"); vr = f"{_v:5.2f}" if _v is not None and not math.isnan(_v) else "    —"
                tag = s.get("timing_tag", "—")
                if tag == "Good entry":
                    tag_str = f"{GREEN}{BOLD}{tag:>12}{RESET}"
                elif tag == "Oversold":
                    tag_str = f"{YELLOW}{BOLD}{tag:>12}{RESET}"
                elif tag == "Extended":
                    tag_str = f"{RED}{tag:>12}{RESET}"
                else:
                    tag_str = f"{tag:>12}"
                print(f"  {s['ticker']:5} {s['name'][:22]:22} "
                      f"{s['composite']:6.1f} {_sig_short(s['signal'])} "
                      f"{rsi_val} {p20} {p50} {vr} {tag_str}")
        if dd["stocks"]:
            top_sub_name = next((s["name"] for s in dd["subsectors"]
                                 if s["ticker"] == dd["top_sub"]), dd["top_sub"])
            print(f"\n  Top holdings in {BOLD}{dd['top_sub']}{RESET} ({top_sub_name}):")
            stk_hdr = (f"  {'Ticker':6} {'Name':22} {'Wt%':>5} {'Comp':>6} {'Sig':>5} "
                       f"{'RSI':>5} {'vs20d':>7} {'vs50d':>7} {'Vol':>5} {'Timing':>12}")
            print(stk_hdr)
            print(f"  {'-'*len(stk_hdr.strip())}")
            for st in dd["stocks"]:
                _r = st.get("rsi");      rsi_val = f"{_r:5.0f}" if _r is not None and not math.isnan(_r) else "    —"
                _p2 = st.get("pct_20d"); p20 = f"{100*_p2:+6.1f}%" if _p2 is not None and not math.isnan(_p2) else "      —"
                _p5 = st.get("pct_50d"); p50 = f"{100*_p5:+6.1f}%" if _p5 is not None and not math.isnan(_p5) else "      —"
                _v = st.get("vol_ratio"); vr = f"{_v:5.2f}" if _v is not None and not math.isnan(_v) else "    —"
                tag = st.get("timing_tag", "—")
                # Color the timing tag
                if tag == "Good entry":
                    tag_str = f"{GREEN}{BOLD}{tag:>12}{RESET}"
                elif tag == "Oversold":
                    tag_str = f"{YELLOW}{BOLD}{tag:>12}{RESET}"
                elif tag == "Extended":
                    tag_str = f"{RED}{tag:>12}{RESET}"
                else:
                    tag_str = f"{tag:>12}"
                print(f"  {st['ticker']:6} {st['name'][:22]:22} "
                      f"{st['weight']:5.1f} {st['composite']:6.1f} {_sig_short(st['signal'])} "
                      f"{rsi_val} {p20} {p50} {vr} {tag_str}")

    print()
    print(f"Excel: {xlsx_path}")
    print(f"HTML:  {html_path}")


def main():
    p = argparse.ArgumentParser(description="Sector Rotation Screener")
    p.add_argument("--no-backtest", action="store_true",
                   help="Skip the walk-forward backtest (faster, but no equity curve).")
    args = p.parse_args()
    run_screen(skip_backtest=args.no_backtest)


if __name__ == "__main__":
    main()
