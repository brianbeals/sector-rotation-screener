# Sector Rotation Screener

Screens the 11 SPDR sector ETFs against three signals: seasonality, economic-cycle fit, and relative strength vs SPY. Produces a Buy / Hold / Avoid composite. Backtests the strategy 15 years against SPY.

Outputs an Excel workbook and a single-page HTML dashboard.

> **Not financial advice.** This is research code I built for myself. It's published as a methodology demo, not a recommendation. Past performance does not indicate future results. Don't trade on this without doing your own homework.

## Live output

Every Sunday afternoon ET, GitHub Actions runs the screener, asks Claude for a brief plain-language commentary on the output, and commits both the dashboard and the commentary back to this repo. The disclaimer is in the README, in the commentary, and at the top of every weekly file.

- **[Live home ↗](https://sector.brianbeals.com/)** — branded landing page
- **[This week's dashboard ↗](https://sector.brianbeals.com/weekly/latest/dashboard.html)**
- **[This week's commentary ↗](https://sector.brianbeals.com/weekly/latest/summary.html)** (Claude's reading + disclaimer)
- **[Past runs](weekly/history/)** — date-stamped archive

If the weekly links 404, the first scheduled run hasn't happened yet. Static reference sample: [`samples/sector_screen_sample.html`](samples/sector_screen_sample.html) ([live](https://sector.brianbeals.com/samples/sector_screen_sample.html)).

## Why this exists

Sector rotation isn't an investment thesis I'm pitching. It's a testbed for the kind of analytical workflow I'd build for a CIO or CDO who wants to evaluate macro exposures across business units: ingest data, score against multiple signals, output something humans can read, backtest the methodology before trusting it.

The same pattern works for portfolio companies, supply-chain risk, customer-segment health, or any question where "let's score it against multiple signals and see how that would have played out historically" beats gut feel.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env and add a free FRED API key:
# https://fred.stlouisfed.org/docs/api/api_key.html
```

## Run

```bash
python screener.py                # full run with backtest
python screener.py --no-backtest  # faster, skip the 15-year backtest
```

Outputs land in `outputs/SectorScreen_YYYY-MM-DD.{xlsx,html}`.

The Excel workbook has three sheets: **Sector Screen**, **Cycle Context**, and **Backtest** (monthly returns plus summary stats). The HTML dashboard has a sortable score table, seasonality heatmap, 3-month RS bars, an inline equity-curve chart for strategy-vs-SPY, and banners that fire when the strategy underperforms or when taxable-account mode is on.

## Project layout

| File          | What it does                                                           |
|---------------|------------------------------------------------------------------------|
| `config.py`   | Universe, weights, cycle map, position sizing, override, tax flag      |
| `data.py`     | yfinance prices, FRED live, FRED vintage (cached to `.cache/`)         |
| `scoring.py`  | Seasonality, cycle fit, RS, composite. All point-in-time aware.        |
| `cycle.py`    | Algorithmic phase classification, manual override, point-in-time mode  |
| `backtest.py` | Walk-forward monthly rebalance, turnover cost, equity curves, stats    |
| `report.py`   | Excel + HTML writers, equity-curve SVG, banners                        |
| `screener.py` | Main entry. Orchestrates the whole run.                                |
| `drilldown.py`| Per-sector drill-down for top-holdings analysis                        |
| `weekly_run.py` | Wraps `screener.py`, calls Claude for commentary, publishes to `weekly/` |
| `.github/workflows/weekly.yml` | GitHub Actions: Sunday 21:00 UTC, commits results back |

## Configurable knobs (config.py)

**Position sizing and trading**

- `MAX_POSITIONS` (default 3)
- `REBALANCE_FREQUENCY` (`"monthly"`)
- `MIN_SCORE_TO_HOLD` (50.0)
- `TRADE_COST_BPS` (25)

**Cycle**

- `CYCLE_PHASE_OVERRIDE`: set to `"Mid-cycle"` etc. to force the phase
- `CYCLE_THRESHOLDS`: PMI levels and curve inversion line
- `CYCLE_FAVORED`: sector mapping per phase

**Scoring**

- `WEIGHTS`: Seasonality / Cycle / RS (default 30 / 30 / 40)
- `SIGNAL_BUY` (65), `SIGNAL_AVOID` (40)
- `SEASONALITY_TRUST_YEARS` (10): under this, sectors get a thin-sample warning

**Tax**

- `TAXABLE_ACCOUNT` (False): when True, the dashboard banner reminds you monthly rotation generates short-term gains

**Backtest**

- `BACKTEST_YEARS` (15)

## Lookahead bias

The backtest uses FRED's ALFRED endpoint (`get_series_all_releases`) to fetch each macro series **as it was published** at each historical month-end. ISM PMI gets revised. We don't use revised values to make past decisions. The manual cycle-phase override is intentionally ignored in backtest mode for the same reason.

## What's deliberately not in here

No options data. No sentiment indicators. No individual-stock scoring beyond the sector drill-down. No ML. The point is a small, transparent, auditable pipeline. Not a black box.

## Easy mode (macOS)

Double-click `Run Screener.command` in Finder. First run sets up the Python env (1-2 minutes). Subsequent runs are 30-90 seconds. The HTML dashboard auto-opens when the run finishes.

First time, macOS may block the script. Allow it via **System Settings → Privacy & Security → Open Anyway**.

## License

MIT. See [LICENSE](LICENSE).
