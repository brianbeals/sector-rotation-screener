"""Sector Rotation Screener — configuration.

All tunable parameters live here so the rest of the code stays clean.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional


# --- Universe -----------------------------------------------------------------

# 11 SPDR sector ETFs plus SPY benchmark.
SECTORS: Dict[str, str] = {
    "XLK":  "Technology",
    "XLF":  "Financials",
    "XLE":  "Energy",
    "XLV":  "Healthcare",
    "XLI":  "Industrials",
    "XLY":  "Consumer Discretionary",
    "XLP":  "Consumer Staples",
    "XLU":  "Utilities",
    "XLB":  "Materials",
    "XLRE": "Real Estate",
    "XLC":  "Communications",
}
BENCHMARK = "SPY"
ALL_TICKERS: List[str] = list(SECTORS.keys()) + [BENCHMARK]


# --- Sub-sector / thematic ETFs (Phase 2 drill-down) -------------------------
#
# When a sector scores "Buy", the drill-down pulls prices for these ETFs,
# scores them with the same RS + seasonality framework, and ranks them.
# The top sub-sector's holdings then get scored individually.
#
# Keep to 3-6 liquid, well-known ETFs per sector. AUM > $500M preferred.

SUBSECTOR_ETFS: Dict[str, Dict[str, str]] = {
    "XLK": {
        "SMH":  "Semiconductors",
        "IGV":  "Software",
        "CIBR": "Cybersecurity",
        "SKYY": "Cloud Computing",
        "BOTZ": "AI & Robotics",
        "FDN":  "Internet",
    },
    "XLF": {
        "KBE":  "Banks",
        "KIE":  "Insurance",
        "KRE":  "Regional Banks",
        "IYG":  "Financial Services",
    },
    "XLE": {
        "XOP":  "Oil & Gas E&P",
        "OIH":  "Oil Services",
        "AMLP": "MLPs / Midstream",
        "URA":  "Uranium / Nuclear",
    },
    "XLV": {
        "IBB":  "Biotech",
        "IHI":  "Medical Devices",
        "XBI":  "Biotech (Equal Wt)",
        "XHE":  "Health Equipment",
    },
    "XLI": {
        "ITA":  "Aerospace & Defense",
        "XAR":  "Aerospace & Def (EW)",
        "PAVE": "Infrastructure",
        "JETS": "Airlines",
    },
    "XLY": {
        "XRT":  "Retail",
        "IBUY": "Online Retail",
        "PEJ":  "Leisure & Entertain",
        "XHB":  "Homebuilders",
    },
    "XLP": {
        "PBJ":  "Food & Beverage",
        "FXG":  "Consumer Staples EW",
    },
    "XLU": {
        "TAN":  "Solar",
        "ICLN": "Clean Energy",
        "FAN":  "Wind Energy",
    },
    "XLB": {
        "GDX":  "Gold Miners",
        "SLX":  "Steel",
        "PICK": "Mining",
        "LIT":  "Lithium & Battery",
    },
    "XLRE": {
        "VNQ":  "REITs Broad",
        "MORT": "Mortgage REITs",
        "ITB":  "Homebuilders",
    },
    "XLC": {
        "SOCL": "Social Media",
        "NERD": "Gaming & Esports",
    },
}

# Max individual stock names to score within the winning sub-sector ETF.
DRILLDOWN_TOP_N_STOCKS: int = 10

# ETF inception dates — used by both seasonality (sample size warning) and
# backtest (only include in universe after this date). Real launch dates,
# checked against State Street prospectuses.
SECTOR_INCEPTION: Dict[str, date] = {
    "XLB":  date(1998, 12, 16),
    "XLE":  date(1998, 12, 16),
    "XLF":  date(1998, 12, 16),
    "XLI":  date(1998, 12, 16),
    "XLK":  date(1998, 12, 16),
    "XLP":  date(1998, 12, 16),
    "XLU":  date(1998, 12, 16),
    "XLV":  date(1998, 12, 16),
    "XLY":  date(1998, 12, 16),
    "XLRE": date(2015, 10, 8),
    "XLC":  date(2018, 6, 19),
    "SPY":  date(1993, 1, 22),
}


# --- History windows ----------------------------------------------------------

HISTORY_YEARS = 20

# Minimum sample for a seasonality score to even compute.
SEASONALITY_MIN_YEARS = 5

# If a sector has less than this many years of history, the dashboard flags
# its seasonality score as "thin sample."
SEASONALITY_TRUST_YEARS = 10

# How far back the backtest walks when no fixed anchor is set. Bumping this
# beyond ~25 starts to outrun XLRE/XLC inceptions and the universe gets sparse
# early.
BACKTEST_YEARS = 15

# Fixed inception date for the backtest. When set, the walk-forward starts here
# and only ever extends forward, so the published cumulative figures grow
# smoothly instead of lurching each month-end as an old month drops off the
# front of a rolling window. Set to None to fall back to a rolling
# BACKTEST_YEARS window ending today. 2011-05-31 matches where the prior
# rolling 15-year window started, so the historical series stays continuous.
BACKTEST_START = "2011-05-31"


# --- Cycle classification -----------------------------------------------------

# Standard rotation playbook.
CYCLE_FAVORED: Dict[str, List[str]] = {
    "Early-cycle": ["XLY", "XLF", "XLI"],
    "Mid-cycle":   ["XLK", "XLC"],
    "Late-cycle":  ["XLE", "XLB", "XLV"],
    "Recession":   ["XLP", "XLU", "XLV"],
}

# FRED series for cycle classification.
# NOTE: NAPM (ISM Manufacturing PMI) was removed from FRED (proprietary data).
# We use INDPRO (Industrial Production Index) instead, computing YoY growth
# to get an expansion/contraction signal that serves the same purpose.
FRED_SERIES = {
    "DGS10":  "10-Year Treasury Constant Maturity",
    "DGS2":   "2-Year Treasury Constant Maturity",
    "INDPRO": "Industrial Production Index",
}

# Cycle thresholds.
# INDPRO YoY growth replaces PMI. Mapping:
#   PMI > 55  (strong expansion)   -> INDPRO YoY > 4%
#   PMI > 50  (expansion)          -> INDPRO YoY > 0%
#   PMI < 47  (contraction)        -> INDPRO YoY < -2%
CYCLE_THRESHOLDS = {
    "yield_curve_inverted":  0.0,
    "indpro_expansion":      0.0,    # YoY % change above 0 = expanding
    "indpro_strong":         4.0,    # YoY % change above 4 = strong expansion
    "indpro_contraction":   -2.0,    # YoY % change below -2 = contraction
}

# Manual override. Set to one of "Early-cycle", "Mid-cycle", "Late-cycle",
# "Recession", or None to use the algorithm. When set, the dashboard shows
# both the algorithm's call and the override, with the override winning.
CYCLE_PHASE_OVERRIDE: Optional[str] = None


# --- Composite scoring weights ------------------------------------------------

@dataclass
class Weights:
    seasonality:  float = 0.25
    cycle_fit:    float = 0.40
    rel_strength: float = 0.35

    def normalize(self) -> "Weights":
        total = self.seasonality + self.cycle_fit + self.rel_strength
        return Weights(self.seasonality / total, self.cycle_fit / total, self.rel_strength / total)


WEIGHTS = Weights().normalize()

# 0-100 scale signal thresholds.
SIGNAL_BUY   = 65
SIGNAL_AVOID = 40

# Drill-down trigger: run sub-sector analysis for sectors at or above this
# composite score. Set equal to SIGNAL_BUY to only drill into Buy sectors,
# or lower (e.g. 55) to include near-Buy "Watch" sectors.
DRILLDOWN_THRESHOLD = 55


# --- Relative strength windows ------------------------------------------------

RS_WINDOWS_DAYS = {"1m": 21, "3m": 63, "6m": 126}
RS_WEIGHTS      = {"1m": 0.25, "3m": 0.40, "6m": 0.35}


# --- Position sizing & trading rules (drive backtest, not just cosmetics) ----

# Max sectors held simultaneously. Fewer survivors -> equal-weight what passes;
# zero survivors -> sit in cash for the period.
MAX_POSITIONS: int = 3

# How often the screen and backtest rebalance. Backtest now honors this.
REBALANCE_FREQUENCY: str = "monthly"   # "monthly" | "quarterly"

# Drop a position if its composite drops below this between rebalance dates.
# NOTE: composite is on a 0-100 scale. The user requested 0.5 in the brief;
# it's been converted to 50 to match the existing scale. Adjust as needed.
MIN_SCORE_TO_HOLD: float = 50.0

# One-way trading cost in basis points, applied per dollar traded at rebalance.
# 10 bps is realistic for ETF retail on major brokerages (Schwab/Fidelity).
TRADE_COST_BPS: float = 10.0


# --- Tax-aware mode -----------------------------------------------------------

# Set True if running in a taxable brokerage account. The dashboard surfaces
# a banner reminding you that monthly rotation creates short-term gains and
# the strategy needs to beat SPY by the tax differential to be worth it.
TAXABLE_ACCOUNT: bool = False


# --- Output paths -------------------------------------------------------------

OUTPUT_DIR = os.environ.get(
    "SECTOR_SCREENER_OUTPUT_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs"),
)

# Disk cache for FRED vintage pulls. ~10 MB at most. Speeds up repeat runs.
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")


# --- API keys -----------------------------------------------------------------

FRED_API_KEY = os.environ.get("FRED_API_KEY", "")
