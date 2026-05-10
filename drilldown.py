"""Phase 2 drill-down — sub-sector ETFs and top holdings within a Buy sector.

When the main screen flags a sector as "Buy", this module:
  1. Fetches prices for the sub-sector / thematic ETFs mapped to that sector.
  2. Scores each sub-ETF using relative strength (vs the parent sector ETF)
     and seasonality — same framework as the main screen.
  3. Ranks them and identifies the strongest sub-theme.
  4. Pulls the top holdings of the winning sub-ETF and scores each stock
     by RS vs the sub-ETF and vs SPY.

The output feeds into the HTML report as a drill-down panel.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

import config
import data
import scoring

log = logging.getLogger(__name__)


# --- Holdings: Yahoo crumb (primary) → hardcoded (fallback) ------------------
#
# Yahoo's v10 quoteSummary endpoint returns live holdings when given a valid
# crumb + cookies. The crumb is fetched once per run and cached. At once-a-day
# usage this is reliable; only heavy repeated runs trigger 429 rate limits.
# If Yahoo is down or rate-limited, hardcoded holdings kick in automatically.

# Module-level session cache: Session on success, False on failure, None = untried.
_yahoo_session = None

def _get_yahoo_session():
    """Return a requests.Session with Yahoo crumb + cookies.

    Caches both success and failure so we only attempt once per run.
    """
    global _yahoo_session

    if _yahoo_session is not None and _yahoo_session is not False:
        return _yahoo_session
    if _yahoo_session is False:
        return None

    import requests
    import time

    sess = requests.Session()
    sess.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
    })

    # Collect auth cookies from Yahoo's consent endpoint.
    try:
        sess.get("https://fc.yahoo.com", timeout=10)
    except Exception:
        pass

    time.sleep(0.5)

    # Fetch crumb — try both query hosts.
    crumb = None
    for host in ("query2.finance.yahoo.com", "query1.finance.yahoo.com"):
        try:
            crumb_resp = sess.get(f"https://{host}/v1/test/getcrumb", timeout=10)
            crumb_resp.raise_for_status()
            crumb = crumb_resp.text.strip()
            if crumb:
                break
        except Exception as exc:
            log.debug("Crumb attempt via %s failed: %s", host, exc)

    if not crumb:
        log.info("Yahoo crumb unavailable; will use hardcoded holdings this run.")
        _yahoo_session = False
        return None

    sess._crumb = crumb
    _yahoo_session = sess
    log.info("Yahoo crumb session initialised.")
    return sess


def _fetch_holdings(etf_ticker: str) -> List[Dict[str, str]]:
    """Fetch top holdings: Yahoo v10 → hardcoded fallback.

    Returns a list of dicts with 'ticker', 'name', and 'weight' keys,
    sorted by weight descending.
    """
    # 1. Try Yahoo v10 quoteSummary with crumb auth.
    sess = _get_yahoo_session()
    if sess is not None:
        url = (
            f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{etf_ticker}"
            f"?modules=topHoldings&crumb={sess._crumb}"
        )
        try:
            resp = sess.get(url, timeout=15)
            resp.raise_for_status()
            result = resp.json()
            summary = result["quoteSummary"]["result"][0]
            holdings = summary.get("topHoldings", {}).get("holdings", [])
            out = []
            for h in holdings:
                ticker = h.get("symbol", "")
                name = h.get("holdingName", ticker)
                weight_raw = h.get("holdingPercent", 0.0)
                if isinstance(weight_raw, dict):
                    weight_raw = weight_raw.get("raw", 0.0)
                if ticker:
                    out.append({
                        "ticker": ticker,
                        "name":   name,
                        "weight": float(weight_raw) * 100.0,
                    })
            if out:
                log.info("Fetched %d live holdings for %s via Yahoo.", len(out), etf_ticker)
                return sorted(out, key=lambda x: x["weight"], reverse=True)
        except Exception as exc:
            log.debug("Yahoo holdings failed for %s: %s", etf_ticker, exc)

    # 2. Hardcoded fallback — updated periodically, good enough for screening.
    log.info("Using hardcoded holdings for %s.", etf_ticker)
    return _hardcoded_holdings(etf_ticker)


# --- Hardcoded holdings for key ETFs (fallback) ------------------------------
# Yahoo's holdings endpoints are unreliable. These are the major holdings
# as of early 2026. Updated periodically — good enough for screening.

_KNOWN_HOLDINGS: Dict[str, List[Dict[str, str]]] = {
    "SMH": [
        {"ticker": "NVDA", "name": "NVIDIA",            "weight": 20.0},
        {"ticker": "TSM",  "name": "Taiwan Semi",       "weight": 12.0},
        {"ticker": "AVGO", "name": "Broadcom",          "weight": 8.0},
        {"ticker": "AMD",  "name": "AMD",               "weight": 5.5},
        {"ticker": "ASML", "name": "ASML Holdings",     "weight": 5.0},
        {"ticker": "QCOM", "name": "Qualcomm",          "weight": 5.0},
        {"ticker": "TXN",  "name": "Texas Instruments",  "weight": 4.5},
        {"ticker": "MU",   "name": "Micron Technology",  "weight": 4.0},
        {"ticker": "INTC", "name": "Intel",              "weight": 3.5},
        {"ticker": "LRCX", "name": "Lam Research",       "weight": 3.5},
    ],
    "IGV": [
        {"ticker": "MSFT", "name": "Microsoft",         "weight": 9.0},
        {"ticker": "CRM",  "name": "Salesforce",        "weight": 5.0},
        {"ticker": "ORCL", "name": "Oracle",            "weight": 5.0},
        {"ticker": "NOW",  "name": "ServiceNow",        "weight": 4.5},
        {"ticker": "ADBE", "name": "Adobe",             "weight": 4.0},
        {"ticker": "INTU", "name": "Intuit",            "weight": 3.5},
        {"ticker": "PANW", "name": "Palo Alto Networks","weight": 3.0},
        {"ticker": "SNPS", "name": "Synopsys",          "weight": 3.0},
        {"ticker": "CDNS", "name": "Cadence Design",    "weight": 3.0},
        {"ticker": "WDAY", "name": "Workday",           "weight": 2.5},
    ],
    "CIBR": [
        {"ticker": "PANW", "name": "Palo Alto Networks","weight": 7.0},
        {"ticker": "CRWD", "name": "CrowdStrike",       "weight": 6.5},
        {"ticker": "FTNT", "name": "Fortinet",          "weight": 5.5},
        {"ticker": "ZS",   "name": "Zscaler",           "weight": 5.0},
        {"ticker": "CSCO", "name": "Cisco Systems",     "weight": 4.5},
        {"ticker": "GEN",  "name": "Gen Digital",       "weight": 3.5},
        {"ticker": "OKTA", "name": "Okta",              "weight": 3.0},
        {"ticker": "NET",  "name": "Cloudflare",        "weight": 3.0},
        {"ticker": "CYBR", "name": "CyberArk",          "weight": 2.5},
        {"ticker": "TENB", "name": "Tenable",           "weight": 2.0},
    ],
    "SKYY": [
        {"ticker": "AMZN", "name": "Amazon",            "weight": 5.0},
        {"ticker": "MSFT", "name": "Microsoft",         "weight": 5.0},
        {"ticker": "GOOGL","name": "Alphabet",          "weight": 4.5},
        {"ticker": "SNOW", "name": "Snowflake",         "weight": 3.5},
        {"ticker": "MDB",  "name": "MongoDB",           "weight": 3.0},
        {"ticker": "DDOG", "name": "Datadog",           "weight": 3.0},
        {"ticker": "NET",  "name": "Cloudflare",        "weight": 2.5},
        {"ticker": "WDAY", "name": "Workday",           "weight": 2.5},
        {"ticker": "PLTR", "name": "Palantir",          "weight": 2.5},
        {"ticker": "CFLT", "name": "Confluent",         "weight": 2.0},
    ],
    "BOTZ": [
        {"ticker": "NVDA", "name": "NVIDIA",            "weight": 10.0},
        {"ticker": "ISRG", "name": "Intuitive Surgical","weight": 8.0},
        {"ticker": "ABB",  "name": "ABB Ltd",           "weight": 7.0},
        {"ticker": "FANUY","name": "FANUC",             "weight": 5.0},
        {"ticker": "UPST", "name": "Upstart",           "weight": 3.0},
        {"ticker": "PATH", "name": "UiPath",            "weight": 3.0},
        {"ticker": "IRBT", "name": "iRobot",            "weight": 2.5},
        {"ticker": "BRKS", "name": "Brooks Automation", "weight": 2.5},
    ],
    "FDN": [
        {"ticker": "AMZN", "name": "Amazon",            "weight": 10.0},
        {"ticker": "META", "name": "Meta Platforms",     "weight": 9.0},
        {"ticker": "NFLX", "name": "Netflix",           "weight": 5.0},
        {"ticker": "CRM",  "name": "Salesforce",        "weight": 4.5},
        {"ticker": "SHOP", "name": "Shopify",           "weight": 4.0},
        {"ticker": "UBER", "name": "Uber",              "weight": 3.5},
        {"ticker": "ABNB", "name": "Airbnb",            "weight": 3.0},
        {"ticker": "SPOT", "name": "Spotify",           "weight": 2.5},
        {"ticker": "SQ",   "name": "Block",             "weight": 2.5},
        {"ticker": "PYPL", "name": "PayPal",            "weight": 2.5},
    ],
    # --- Energy sub-sectors ---
    "OIH": [
        {"ticker": "SLB",  "name": "Schlumberger",       "weight": 20.3},
        {"ticker": "BKR",  "name": "Baker Hughes",       "weight": 11.5},
        {"ticker": "HAL",  "name": "Halliburton",        "weight": 6.9},
        {"ticker": "FTI",  "name": "TechnipFMC",         "weight": 6.7},
        {"ticker": "TS",   "name": "Tenaris",            "weight": 5.1},
        {"ticker": "NE",   "name": "Noble Corp",         "weight": 5.4},
        {"ticker": "RIG",  "name": "Transocean",         "weight": 5.3},
        {"ticker": "LBRT", "name": "Liberty Energy",     "weight": 4.4},
        {"ticker": "WHD",  "name": "Cactus",             "weight": 3.5},
        {"ticker": "WFRD", "name": "Weatherford Intl",   "weight": 3.3},
    ],
    "XOP": [  # equal-weighted
        {"ticker": "TPL",  "name": "Texas Pacific Land", "weight": 2.7},
        {"ticker": "VG",   "name": "Venture Global",     "weight": 3.2},
        {"ticker": "XOM",  "name": "Exxon Mobil",        "weight": 2.8},
        {"ticker": "CVX",  "name": "Chevron",            "weight": 2.8},
        {"ticker": "FANG", "name": "Diamondback Energy", "weight": 2.8},
        {"ticker": "APA",  "name": "APA Corp",           "weight": 2.9},
        {"ticker": "MRO",  "name": "Marathon Oil",       "weight": 2.7},
        {"ticker": "SM",   "name": "SM Energy",          "weight": 2.9},
        {"ticker": "CHRD", "name": "Chord Energy",       "weight": 2.8},
        {"ticker": "MUR",  "name": "Murphy Oil",         "weight": 2.9},
    ],
    "AMLP": [
        {"ticker": "PAA",  "name": "Plains All American","weight": 13.6},
        {"ticker": "SUN",  "name": "Sunoco LP",          "weight": 13.6},
        {"ticker": "ET",   "name": "Energy Transfer",    "weight": 13.1},
        {"ticker": "EPD",  "name": "Enterprise Products", "weight": 12.8},
        {"ticker": "MPLX", "name": "MPLX LP",            "weight": 12.4},
        {"ticker": "WES",  "name": "Western Midstream",  "weight": 12.3},
        {"ticker": "HESM", "name": "Hess Midstream",     "weight": 9.9},
        {"ticker": "CQP",  "name": "Cheniere Energy Ptrs","weight": 4.9},
        {"ticker": "USAC", "name": "USA Compression",    "weight": 4.2},
        {"ticker": "GEL",  "name": "Genesis Energy",     "weight": 3.8},
    ],
    # --- Uranium ---
    "URA": [
        {"ticker": "CCJ",  "name": "Cameco Corp",        "weight": 23.1},
        {"ticker": "NXE",  "name": "NexGen Energy",      "weight": 6.5},
        {"ticker": "OKLO", "name": "Oklo Inc",           "weight": 5.9},
        {"ticker": "UEC",  "name": "Uranium Energy",     "weight": 5.8},
        {"ticker": "PDN.AX","name": "Paladin Energy",    "weight": 5.2},
        {"ticker": "DNN",  "name": "Denison Mines",      "weight": 3.1},
        {"ticker": "SRUUF","name": "Sprott Physical Uranium","weight": 3.0},
        {"ticker": "UUUU", "name": "Energy Fuels",       "weight": 2.8},
        {"ticker": "LEU",  "name": "Centrus Energy",     "weight": 2.5},
        {"ticker": "SMR",  "name": "NuScale Power",      "weight": 2.3},
    ],
    # --- Social Media & Gaming ---
    "SOCL": [
        {"ticker": "META", "name": "Meta Platforms",     "weight": 11.4},
        {"ticker": "TCEHY","name": "Tencent Holdings",   "weight": 10.0},
        {"ticker": "035420.KS","name":"NAVER Corp",      "weight": 8.3},
        {"ticker": "GOOGL","name": "Alphabet",           "weight": 7.9},
        {"ticker": "RDDT", "name": "Reddit",             "weight": 7.0},
        {"ticker": "BIDU", "name": "Baidu",              "weight": 6.0},
        {"ticker": "SPOT", "name": "Spotify",            "weight": 4.5},
        {"ticker": "NTES", "name": "NetEase",            "weight": 4.3},
        {"ticker": "035720.KS","name":"Kakao Corp",      "weight": 4.2},
        {"ticker": "SNAP", "name": "Snap Inc",           "weight": 3.5},
    ],
    "NERD": [
        {"ticker": "NTES", "name": "NetEase",            "weight": 10.8},
        {"ticker": "NTDOY","name": "Nintendo",           "weight": 10.8},
        {"ticker": "RBLX", "name": "Roblox",             "weight": 8.3},
        {"ticker": "EA",   "name": "Electronic Arts",    "weight": 7.7},
        {"ticker": "TTWO", "name": "Take-Two Interactive","weight": 7.4},
        {"ticker": "NEXOY","name": "Nexon",              "weight": 4.5},
        {"ticker": "KONMY","name": "Konami",             "weight": 4.4},
        {"ticker": "U",    "name": "Unity Software",     "weight": 4.3},
        {"ticker": "NCBDY","name": "Bandai Namco",        "weight": 4.0},
        {"ticker": "CCOEY","name": "Capcom",             "weight": 3.4},
    ],
    # --- Real Estate sub-sectors ---
    "VNQ": [
        {"ticker": "WELL", "name": "Welltower",          "weight": 7.7},
        {"ticker": "PLD",  "name": "Prologis",           "weight": 7.0},
        {"ticker": "EQIX", "name": "Equinix",            "weight": 5.5},
        {"ticker": "AMT",  "name": "American Tower",     "weight": 4.6},
        {"ticker": "DLR",  "name": "Digital Realty",     "weight": 3.5},
        {"ticker": "SPG",  "name": "Simon Property",     "weight": 3.5},
        {"ticker": "O",    "name": "Realty Income",      "weight": 3.2},
        {"ticker": "PSA",  "name": "Public Storage",     "weight": 2.8},
        {"ticker": "CCI",  "name": "Crown Castle",       "weight": 2.5},
        {"ticker": "EXR",  "name": "Extra Space Storage", "weight": 2.3},
    ],
    "MORT": [
        {"ticker": "NLY",  "name": "Annaly Capital Mgmt","weight": 17.3},
        {"ticker": "AGNC", "name": "AGNC Investment",    "weight": 13.9},
        {"ticker": "STWD", "name": "Starwood Property",  "weight": 7.3},
        {"ticker": "RITM", "name": "Rithm Capital",      "weight": 6.5},
        {"ticker": "DX",   "name": "Dynex Capital",      "weight": 4.9},
        {"ticker": "BXMT", "name": "Blackstone Mortgage", "weight": 5.1},
        {"ticker": "HASI", "name": "Hannon Armstrong",   "weight": 5.0},
        {"ticker": "ABR",  "name": "Arbor Realty Trust",  "weight": 4.8},
        {"ticker": "RC",   "name": "Ready Capital",      "weight": 4.6},
        {"ticker": "TWO",  "name": "Two Harbors",        "weight": 4.7},
    ],
    "ITB": [
        {"ticker": "DHI",  "name": "D.R. Horton",        "weight": 15.2},
        {"ticker": "PHM",  "name": "PulteGroup",         "weight": 9.1},
        {"ticker": "LEN",  "name": "Lennar",             "weight": 7.5},
        {"ticker": "NVR",  "name": "NVR Inc",            "weight": 7.4},
        {"ticker": "TOL",  "name": "Toll Brothers",      "weight": 5.2},
        {"ticker": "LOW",  "name": "Lowe's",             "weight": 4.5},
        {"ticker": "HD",   "name": "Home Depot",         "weight": 4.4},
        {"ticker": "BLD",  "name": "TopBuild",           "weight": 4.0},
        {"ticker": "LII",  "name": "Lennox International","weight": 3.3},
        {"ticker": "MTH",  "name": "Meritage Homes",     "weight": 3.0},
    ],
    # --- Financials sub-sectors ---
    "KBE": [  # equal-weighted
        {"ticker": "C",    "name": "Citigroup",          "weight": 1.1},
        {"ticker": "TBBK", "name": "The Bancorp",        "weight": 1.1},
        {"ticker": "APO",  "name": "Apollo Global Mgmt", "weight": 1.1},
        {"ticker": "TFC",  "name": "Truist Financial",   "weight": 1.1},
        {"ticker": "WSFS", "name": "WSFS Financial",     "weight": 1.1},
        {"ticker": "BPOP", "name": "Popular Inc",        "weight": 1.1},
        {"ticker": "GBCI", "name": "Glacier Bancorp",    "weight": 1.1},
        {"ticker": "FHN",  "name": "First Horizon",      "weight": 1.1},
        {"ticker": "KEY",  "name": "KeyCorp",            "weight": 1.1},
        {"ticker": "CFG",  "name": "Citizens Financial",  "weight": 1.1},
    ],
    "KIE": [  # equal-weighted
        {"ticker": "LMND", "name": "Lemonade",           "weight": 2.9},
        {"ticker": "BHF",  "name": "Brighthouse Finl",   "weight": 2.5},
        {"ticker": "MCY",  "name": "Mercury General",    "weight": 2.2},
        {"ticker": "ORI",  "name": "Old Republic Intl",  "weight": 2.1},
        {"ticker": "WTM",  "name": "White Mountains Ins","weight": 2.1},
        {"ticker": "RYAN", "name": "Ryan Specialty",     "weight": 2.1},
        {"ticker": "L",    "name": "Loews Corp",         "weight": 2.1},
        {"ticker": "AGO",  "name": "Assured Guaranty",   "weight": 2.0},
        {"ticker": "CINF", "name": "Cincinnati Financial","weight": 2.0},
        {"ticker": "RNR",  "name": "RenaissanceRe",      "weight": 2.0},
    ],
    "KRE": [  # equal-weighted
        {"ticker": "BPOP", "name": "Popular Inc",        "weight": 2.2},
        {"ticker": "WBS",  "name": "Webster Financial",  "weight": 2.1},
        {"ticker": "VLY",  "name": "Valley National",    "weight": 2.1},
        {"ticker": "MTB",  "name": "M&T Bank",           "weight": 2.0},
        {"ticker": "CFR",  "name": "Cullen/Frost",       "weight": 2.0},
        {"ticker": "EWBC", "name": "East West Bancorp",  "weight": 2.0},
        {"ticker": "FNB",  "name": "F.N.B. Corp",        "weight": 2.0},
        {"ticker": "HBAN", "name": "Huntington Bancshr", "weight": 2.0},
        {"ticker": "RF",   "name": "Regions Financial",  "weight": 2.0},
        {"ticker": "CMA",  "name": "Comerica",           "weight": 2.0},
    ],
    "IYG": [
        {"ticker": "BRK-B","name": "Berkshire Hathaway", "weight": 13.8},
        {"ticker": "JPM",  "name": "JPMorgan Chase",     "weight": 12.3},
        {"ticker": "V",    "name": "Visa",               "weight": 7.9},
        {"ticker": "MA",   "name": "Mastercard",         "weight": 6.2},
        {"ticker": "BAC",  "name": "Bank of America",    "weight": 5.1},
        {"ticker": "WFC",  "name": "Wells Fargo",        "weight": 4.1},
        {"ticker": "GS",   "name": "Goldman Sachs",      "weight": 3.7},
        {"ticker": "MS",   "name": "Morgan Stanley",     "weight": 3.0},
        {"ticker": "SPGI", "name": "S&P Global",         "weight": 3.0},
        {"ticker": "BLK",  "name": "BlackRock",          "weight": 2.8},
    ],
    # --- Healthcare sub-sectors ---
    "IBB": [
        {"ticker": "GILD", "name": "Gilead Sciences",    "weight": 9.6},
        {"ticker": "VRTX", "name": "Vertex Pharma",      "weight": 8.8},
        {"ticker": "AMGN", "name": "Amgen",              "weight": 8.7},
        {"ticker": "REGN", "name": "Regeneron",          "weight": 6.7},
        {"ticker": "ALNY", "name": "Alnylam Pharma",     "weight": 3.7},
        {"ticker": "BIIB", "name": "Biogen",             "weight": 3.8},
        {"ticker": "IQV",  "name": "IQVIA Holdings",     "weight": 3.7},
        {"ticker": "MRNA", "name": "Moderna",            "weight": 3.4},
        {"ticker": "MTD",  "name": "Mettler-Toledo",     "weight": 2.4},
        {"ticker": "TECH", "name": "Bio-Techne",         "weight": 2.2},
    ],
    "IHI": [
        {"ticker": "ABT",  "name": "Abbott Labs",        "weight": 15.9},
        {"ticker": "ISRG", "name": "Intuitive Surgical", "weight": 16.5},
        {"ticker": "BSX",  "name": "Boston Scientific",  "weight": 9.0},
        {"ticker": "SYK",  "name": "Stryker",            "weight": 5.0},
        {"ticker": "EW",   "name": "Edwards Lifesciences","weight": 5.0},
        {"ticker": "RMD",  "name": "ResMed",             "weight": 3.5},
        {"ticker": "MDT",  "name": "Medtronic",          "weight": 4.7},
        {"ticker": "GEHC", "name": "GE HealthCare",      "weight": 3.5},
        {"ticker": "IDXX", "name": "IDEXX Laboratories", "weight": 4.6},
        {"ticker": "BDX",  "name": "Becton Dickinson",   "weight": 3.2},
    ],
    # --- Industrials sub-sectors ---
    "ITA": [
        {"ticker": "GE",   "name": "GE Aerospace",       "weight": 19.4},
        {"ticker": "RTX",  "name": "RTX Corp",           "weight": 15.1},
        {"ticker": "BA",   "name": "Boeing",             "weight": 10.3},
        {"ticker": "GD",   "name": "General Dynamics",   "weight": 4.8},
        {"ticker": "HWM",  "name": "Howmet Aerospace",   "weight": 4.8},
        {"ticker": "LHX",  "name": "L3Harris",           "weight": 4.7},
        {"ticker": "LMT",  "name": "Lockheed Martin",    "weight": 4.6},
        {"ticker": "NOC",  "name": "Northrop Grumman",   "weight": 4.6},
        {"ticker": "TDG",  "name": "TransDigm Group",    "weight": 4.5},
        {"ticker": "TXT",  "name": "Textron",            "weight": 2.5},
    ],
    "PAVE": [
        {"ticker": "PWR",  "name": "Quanta Services",    "weight": 3.7},
        {"ticker": "CSX",  "name": "CSX Corp",           "weight": 3.5},
        {"ticker": "ETN",  "name": "Eaton Corp",         "weight": 3.4},
        {"ticker": "TT",   "name": "Trane Technologies", "weight": 3.4},
        {"ticker": "HWM",  "name": "Howmet Aerospace",   "weight": 3.2},
        {"ticker": "UNP",  "name": "Union Pacific",      "weight": 3.2},
        {"ticker": "PH",   "name": "Parker Hannifin",    "weight": 3.6},
        {"ticker": "VMC",  "name": "Vulcan Materials",   "weight": 3.0},
        {"ticker": "MLM",  "name": "Martin Marietta",    "weight": 2.9},
        {"ticker": "EMR",  "name": "Emerson Electric",   "weight": 2.8},
    ],
    # --- Consumer Discretionary sub-sectors ---
    "XRT": [  # equal-weighted
        {"ticker": "MUSA", "name": "Murphy USA",         "weight": 1.8},
        {"ticker": "SAH",  "name": "Sonic Automotive",   "weight": 1.7},
        {"ticker": "CVNA", "name": "Carvana",            "weight": 1.7},
        {"ticker": "GO",   "name": "Grocery Outlet",     "weight": 1.7},
        {"ticker": "AMZN", "name": "Amazon",             "weight": 1.7},
        {"ticker": "W",    "name": "Wayfair",            "weight": 1.6},
        {"ticker": "BURL", "name": "Burlington Stores",  "weight": 1.6},
        {"ticker": "TJX",  "name": "TJX Companies",      "weight": 1.6},
        {"ticker": "ROST", "name": "Ross Stores",        "weight": 1.6},
        {"ticker": "DKS",  "name": "Dicks Sporting",     "weight": 1.6},
    ],
    "XHB": [  # equal-weighted
        {"ticker": "JCI",  "name": "Johnson Controls",   "weight": 4.0},
        {"ticker": "CARR", "name": "Carrier Global",     "weight": 3.8},
        {"ticker": "CSL",  "name": "Carlisle Companies", "weight": 3.7},
        {"ticker": "TT",   "name": "Trane Technologies", "weight": 3.7},
        {"ticker": "IBP",  "name": "Installed Building",  "weight": 3.6},
        {"ticker": "DHI",  "name": "D.R. Horton",        "weight": 3.5},
        {"ticker": "PHM",  "name": "PulteGroup",         "weight": 3.4},
        {"ticker": "MAS",  "name": "Masco Corp",         "weight": 3.3},
        {"ticker": "LEN",  "name": "Lennar",             "weight": 3.3},
        {"ticker": "TOL",  "name": "Toll Brothers",      "weight": 3.2},
    ],
    # --- Materials sub-sectors ---
    "GDX": [
        {"ticker": "AEM",  "name": "Agnico Eagle Mines", "weight": 12.2},
        {"ticker": "NEM",  "name": "Newmont Corp",       "weight": 10.9},
        {"ticker": "GOLD", "name": "Barrick Mining",     "weight": 7.7},
        {"ticker": "FNV",  "name": "Franco-Nevada",      "weight": 5.2},
        {"ticker": "AU",   "name": "AngloGold Ashanti",  "weight": 5.0},
        {"ticker": "WPM",  "name": "Wheaton Precious",   "weight": 4.9},
        {"ticker": "KGC",  "name": "Kinross Gold",       "weight": 4.7},
        {"ticker": "GFI",  "name": "Gold Fields",        "weight": 4.5},
        {"ticker": "PAAS", "name": "Pan American Silver","weight": 3.2},
        {"ticker": "NST",  "name": "Northern Star Res",  "weight": 2.6},
    ],
    "SLX": [
        {"ticker": "RIO",  "name": "Rio Tinto",          "weight": 7.9},
        {"ticker": "BHP",  "name": "BHP Group",          "weight": 7.8},
        {"ticker": "NUE",  "name": "Nucor",              "weight": 7.1},
        {"ticker": "VALE", "name": "Vale SA",            "weight": 6.8},
        {"ticker": "PKX",  "name": "POSCO Holdings",     "weight": 5.4},
        {"ticker": "STLD", "name": "Steel Dynamics",     "weight": 5.1},
        {"ticker": "MT",   "name": "ArcelorMittal",      "weight": 4.6},
        {"ticker": "TX",   "name": "Ternium",            "weight": 4.0},
        {"ticker": "GGB",  "name": "Gerdau SA",          "weight": 3.8},
        {"ticker": "RS",   "name": "Reliance Steel",     "weight": 3.5},
    ],
    "LIT": [
        {"ticker": "RIO",  "name": "Rio Tinto",          "weight": 19.7},
        {"ticker": "006400.KS","name":"Samsung SDI",      "weight": 7.0},
        {"ticker": "ALB",  "name": "Albemarle",          "weight": 6.7},
        {"ticker": "PCRFY","name": "Panasonic Holdings", "weight": 4.1},
        {"ticker": "PLS.AX","name":"Pilbara Minerals",   "weight": 3.9},
        {"ticker": "SQM",  "name": "SQM",                "weight": 3.9},
        {"ticker": "BYDDY","name": "BYD Company",        "weight": 3.8},
        {"ticker": "TEM",  "name": "Tempus AI",          "weight": 3.7},
        {"ticker": "ENVX", "name": "Enovix Corp",        "weight": 3.4},
        {"ticker": "QS",   "name": "QuantumScape",       "weight": 3.2},
    ],
    # --- Clean Energy ---
    "TAN": [
        {"ticker": "FSLR", "name": "First Solar",        "weight": 9.8},
        {"ticker": "NXT",  "name": "Nextracker",         "weight": 9.5},
        {"ticker": "ENLT", "name": "Enlight Renewable",  "weight": 7.9},
        {"ticker": "ENPH", "name": "Enphase Energy",     "weight": 5.4},
        {"ticker": "RUN",  "name": "Sunrun",             "weight": 4.7},
        {"ticker": "SEDG", "name": "SolarEdge Tech",     "weight": 4.5},
        {"ticker": "HASI", "name": "Hannon Armstrong",   "weight": 4.5},
        {"ticker": "CWEN", "name": "Clearway Energy",    "weight": 3.1},
        {"ticker": "SHLS", "name": "Shoals Technologies","weight": 2.7},
        {"ticker": "ARRY", "name": "Array Technologies", "weight": 2.6},
    ],
}


def _hardcoded_holdings(etf_ticker: str) -> List[Dict[str, str]]:
    return _KNOWN_HOLDINGS.get(etf_ticker, [])


# --- Sub-sector scoring ------------------------------------------------------

def score_subsectors(parent_ticker: str,
                     parent_prices: pd.DataFrame,
                     bench_prices: pd.DataFrame) -> List[Dict]:
    """Score all sub-sector ETFs for a given parent sector.

    Uses RS vs the parent sector ETF (not SPY) to find which theme is
    outperforming within the sector. Seasonality adds calendar context.

    Returns list of dicts sorted by composite score descending.
    """
    subs = config.SUBSECTOR_ETFS.get(parent_ticker, {})
    if not subs:
        log.info("No sub-sector ETFs defined for %s", parent_ticker)
        return []

    # Fetch prices for all sub-sector tickers
    sub_tickers = list(subs.keys())
    log.info("Drill-down: fetching %d sub-sector ETFs for %s", len(sub_tickers), parent_ticker)
    sub_prices = data.fetch_prices(sub_tickers, years=5)

    results = []
    for tk, name in subs.items():
        df = sub_prices.get(tk)
        if df is None or df.empty:
            log.warning("  No data for sub-sector %s (%s); skipping.", tk, name)
            continue

        # RS vs parent sector (which sub-theme is beating the sector?)
        rs_vs_parent = scoring.rel_strength_scores(df, parent_prices)

        # RS vs SPY (is this sub-theme beating the broad market?)
        rs_vs_spy = scoring.rel_strength_scores(df, bench_prices)

        # Seasonality
        season = scoring.seasonality_score(df)

        # Entry timing indicators
        timing = scoring.entry_timing(df)

        # Composite: 50% RS-vs-parent + 20% RS-vs-SPY + 30% seasonality
        composite = (0.50 * rs_vs_parent["score"]
                     + 0.20 * rs_vs_spy["score"]
                     + 0.30 * season["score"])

        if composite >= config.SIGNAL_BUY:
            signal = "Buy"
        elif composite <= config.SIGNAL_AVOID:
            signal = "Avoid"
        else:
            signal = "Hold"

        results.append({
            "ticker":        tk,
            "name":          name,
            "composite":     float(composite),
            "signal":        signal,
            "rs_parent":     rs_vs_parent["score"],
            "rs_spy":        rs_vs_spy["score"],
            "rs_3m_parent":  rs_vs_parent.get("rs_3m", float("nan")),
            "rs_3m_spy":     rs_vs_spy.get("rs_3m", float("nan")),
            "seasonality":   season["score"],
            "season_n":      season["n_years"],
            "season_thin":   season.get("thin_sample", False),
            "pct_from_high": scoring.fifty_two_week_high_pct(df),
            "rsi":           timing["rsi"],
            "pct_20d":       timing["pct_20d"],
            "pct_50d":       timing["pct_50d"],
            "vol_ratio":     timing["vol_ratio"],
            "timing_tag":    timing["timing_tag"],
        })

    results.sort(key=lambda r: r["composite"], reverse=True)
    for r in results:
        log.info("  %s %-20s  Comp: %5.1f  Sig: %-5s  RS(parent): %5.1f  RS(SPY): %5.1f",
                 r["ticker"], r["name"], r["composite"], r["signal"],
                 r["rs_parent"], r["rs_spy"])
    return results


# --- Individual stock scoring -------------------------------------------------

def score_top_holdings(sub_etf_ticker: str,
                       sub_etf_prices: pd.DataFrame,
                       bench_prices: pd.DataFrame,
                       max_names: int = config.DRILLDOWN_TOP_N_STOCKS) -> List[Dict]:
    """Score individual stocks within a sub-sector ETF.

    Fetches the ETF's top holdings, pulls their prices, and scores each by
    RS vs the sub-ETF and vs SPY. This tells you which names are leading
    the theme vs riding it.
    """
    holdings = _fetch_holdings(sub_etf_ticker)
    if not holdings:
        log.info("No holdings data for %s; skipping stock drill-down.", sub_etf_ticker)
        return []

    # Only score the top N by weight
    top = holdings[:max_names]
    tickers = [h["ticker"] for h in top]
    weight_map = {h["ticker"]: h for h in top}

    log.info("Drill-down stocks: fetching %d holdings of %s", len(tickers), sub_etf_ticker)
    stock_prices = data.fetch_prices(tickers, years=3)

    results = []
    for tk in tickers:
        df = stock_prices.get(tk)
        if df is None or df.empty:
            continue
        info = weight_map[tk]

        # RS vs sub-sector ETF (is this stock leading or lagging the theme?)
        rs_vs_sub = scoring.rel_strength_scores(df, sub_etf_prices)

        # RS vs SPY (is this stock beating the broad market?)
        rs_vs_spy = scoring.rel_strength_scores(df, bench_prices)

        # Entry timing indicators (RSI, MA distances, volume)
        timing = scoring.entry_timing(df)

        # Composite for stocks: 60% RS-vs-sub + 40% RS-vs-SPY
        composite = 0.60 * rs_vs_sub["score"] + 0.40 * rs_vs_spy["score"]

        if composite >= config.SIGNAL_BUY:
            signal = "Buy"
        elif composite <= config.SIGNAL_AVOID:
            signal = "Avoid"
        else:
            signal = "Hold"

        results.append({
            "ticker":        tk,
            "name":          info["name"],
            "weight":        info["weight"],
            "composite":     float(composite),
            "signal":        signal,
            "rs_sub":        rs_vs_sub["score"],
            "rs_spy":        rs_vs_spy["score"],
            "rs_3m_sub":     rs_vs_sub.get("rs_3m", float("nan")),
            "rs_3m_spy":     rs_vs_spy.get("rs_3m", float("nan")),
            "pct_from_high": scoring.fifty_two_week_high_pct(df),
            "rsi":           timing["rsi"],
            "pct_20d":       timing["pct_20d"],
            "pct_50d":       timing["pct_50d"],
            "vol_ratio":     timing["vol_ratio"],
            "timing_tag":    timing["timing_tag"],
        })

    results.sort(key=lambda r: r["composite"], reverse=True)
    for r in results:
        log.info("    %s %-20s  Comp: %5.1f  Sig: %-5s  RS(sub): %5.1f  RS(SPY): %5.1f  Wt: %.1f%%",
                 r["ticker"], r["name"], r["composite"], r["signal"],
                 r["rs_sub"], r["rs_spy"], r["weight"])
    return results


# --- Full drill-down pipeline -------------------------------------------------

def run_drilldown(buy_sectors: List[Dict],
                  prices: Dict[str, pd.DataFrame]) -> Dict[str, Dict]:
    """Run the full drill-down for all Buy-signal sectors.

    Parameters
    ----------
    buy_sectors : list of dicts from the main screen with 'ticker' key
    prices : the already-fetched price dict from the main screen

    Returns dict keyed by parent sector ticker, each value a dict with:
      "subsectors"  -> list of scored sub-ETF dicts
      "top_sub"     -> ticker of the winning sub-ETF (or None)
      "stocks"      -> list of scored individual stock dicts (from top sub)
    """
    bench = prices.get(config.BENCHMARK)
    if bench is None or bench.empty:
        log.error("No SPY data for drill-down; aborting.")
        return {}

    results = {}
    for sector in buy_sectors:
        tk = sector["ticker"]
        parent_df = prices.get(tk)
        if parent_df is None or parent_df.empty:
            continue

        log.info("=" * 60)
        log.info("DRILL-DOWN: %s (%s)", tk, config.SECTORS.get(tk, tk))
        log.info("=" * 60)

        # Step 1: Score sub-sector ETFs
        subsectors = score_subsectors(tk, parent_df, bench)

        # Step 2: Pick the top sub-sector and score its holdings
        top_sub = subsectors[0]["ticker"] if subsectors else None
        stocks = []
        if top_sub:
            # Need to fetch the top sub's prices (might already have them
            # from score_subsectors, but fetch_prices caches per call)
            sub_prices_map = data.fetch_prices([top_sub], years=3)
            sub_df = sub_prices_map.get(top_sub)
            if sub_df is not None and not sub_df.empty:
                stocks = score_top_holdings(top_sub, sub_df, bench)

        parent_composite = sector.get("composite", 0)
        results[tk] = {
            "subsectors":       subsectors,
            "top_sub":          top_sub,
            "stocks":           stocks,
            "parent_composite": parent_composite,
            "parent_signal":    sector.get("signal", "Hold"),
        }

    return results
