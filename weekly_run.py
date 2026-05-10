"""Weekly orchestrator.

Runs the screener, asks Claude for a short commentary on the output, and
publishes both the dashboard and the commentary to weekly/latest/ and
weekly/history/<date>/.

Designed to run in GitHub Actions on Sundays. Required env vars:
    FRED_API_KEY        — for the screener
    ANTHROPIC_API_KEY   — for the Claude commentary

Optional:
    SECTOR_SCREENER_OUTPUT_DIR — override the default outputs/ folder
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import date
from pathlib import Path

import anthropic

import config
import screener


CLAUDE_MODEL = "claude-sonnet-4-5"  # set to claude-sonnet-4-6 for the latest model


DISCLAIMER = (
    "**⚠️ Not financial advice.** This commentary is auto-generated each week by "
    "Claude (Anthropic's AI model). Brian Beals is not a registered investment "
    "advisor, and Claude is not licensed to provide personalized financial "
    "advice. The screener is a research and methodology demo, not a "
    "recommendation system. Past performance does not predict future results. "
    "Do your own research before making any investment decisions."
)

PROMPT_TEMPLATE = """You are reviewing the weekly output of an open-source sector-rotation screener. The repository is a public methodology demo on GitHub. Your commentary will be committed alongside the dashboard and read by anyone who visits the repo.

CRITICAL CONSTRAINTS:
- You are NOT a registered financial advisor and the user is NOT a registered financial advisor.
- This commentary is for educational and research purposes only.
- Do NOT recommend specific buy/sell actions.
- Do NOT make predictions about future market or sector performance.
- Comment on what the screen says THIS WEEK, not what someone should DO with it.
- If a sector has a "Buy" signal in the screen, that is an output of THIS rule set, not your recommendation. Frame accordingly.

This week's screen output:

Date: {date}
Cycle phase: {cycle_phase}
Cycle reasoning: {cycle_why}

Backtest ({bt_years}y, {trade_cost_bps} bps trading cost): Strategy {strategy_cum:+.2%} vs SPY {spy_cum:+.2%}. {beats_spy_text}

Sector scores (composite scale 0-100, sorted descending):
{sector_table}

Composite weights: Seasonality {w_season}% + Cycle Fit {w_cycle}% + Relative Strength {w_rs}%.
Signal thresholds: Buy ≥ {signal_buy}, Avoid ≤ {signal_avoid}.

Macro vintage as of: {vintage_date}

Write a markdown commentary in this exact structure (use the headings verbatim):

## What the screen said this week
2-3 short paragraphs. Cover: the current cycle phase classification and the macro signals driving it; which sectors topped the composite (and roughly why — seasonality, cycle fit, relative strength, or some combination); any notable Avoid signals.

## Things worth noticing
2-3 short paragraphs of educational observations. Possible angles: sectors with strong RS but weak cycle fit (or vice versa), thin-sample seasonality warnings, divergence between cycle phase and price action, anything counterintuitive in the rankings. Stay descriptive, not prescriptive. No forward predictions.

## Methodology reminder
2-3 sentences. Remind readers: the composite is the weighted sum named above. Lookahead bias is controlled via FRED ALFRED vintages in the backtest. The backtest result is a property of THIS rule set, not a forecast.

Aim for 300-400 words total. Plain language. Define jargon when used."""


def _build_sector_table(rows: list) -> str:
    """Render the sector rows as a fixed-width text block for the prompt."""
    lines = []
    for r in rows:
        thin = " (thin sample)" if r.get("seasonality_thin") else ""
        lines.append(
            f"  {r['ticker']:4} {r['name'][:22]:22}  "
            f"composite {r['composite']:5.1f}  "
            f"signal {r['signal']:5}  "
            f"season {r['seasonality_score']:5.1f}{thin}  "
            f"cycle {r['cycle_fit_score']:5.1f}  "
            f"rs {r['rs_score']:5.1f}  "
            f"rs3m {100 * r.get('rs_3m', 0):+5.2f}%"
        )
    return "\n".join(lines)


def _generate_commentary(data: dict) -> str:
    bt = data.get("backtest_summary") or {}
    beats = bt.get("beats_spy_net")
    if beats is True:
        beats_text = "Strategy beat SPY net of trading cost over the backtest window."
    elif beats is False:
        beats_text = "Strategy did NOT beat SPY net of trading cost over the backtest window."
    else:
        beats_text = "Backtest skipped or unavailable."

    w = config.WEIGHTS
    prompt = PROMPT_TEMPLATE.format(
        date=data["date"],
        cycle_phase=data["cycle"].get("phase", "Unknown"),
        cycle_why=data["cycle"].get("why", "—"),
        strategy_cum=bt.get("strategy_cum", 0),
        spy_cum=bt.get("spy_cum", 0),
        bt_years=config.BACKTEST_YEARS,
        trade_cost_bps=int(config.TRADE_COST_BPS),
        beats_spy_text=beats_text,
        sector_table=_build_sector_table(data["rows"]),
        w_season=int(round(w.seasonality * 100)),
        w_cycle=int(round(w.cycle_fit * 100)),
        w_rs=int(round(w.rel_strength * 100)),
        signal_buy=int(config.SIGNAL_BUY),
        signal_avoid=int(config.SIGNAL_AVOID),
        vintage_date=data["vintage"].get("fred_vintage", "—"),
    )

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text.strip()


def _build_summary_md(today: str, commentary: str) -> str:
    return f"""# Weekly Sector Rotation Commentary — {today}

{DISCLAIMER}

[View the dashboard ↗](dashboard.html)

---

{commentary}

---

*By [Brian Beals](https://brianbeals.com). Methodology and code: [github.com/brianbeals/sector-rotation-screener](https://github.com/brianbeals/sector-rotation-screener). Commentary generated by Claude ({CLAUDE_MODEL}). © {today[:4]} Brian Beals.*
"""


def _build_weekly_index(today: str) -> str:
    return f"""# Weekly runs

Auto-generated every Sunday afternoon ET by GitHub Actions. The screener pulls
fresh data from yfinance and FRED, scores the 11 SPDR sector ETFs, runs the
15-year backtest, and asks Claude for a short commentary on what the screen
said.

## Latest

- **[Dashboard](latest/dashboard.html)** — score table, seasonality heatmap, RS bars, equity curve, cycle context
- **[Commentary](latest/summary.md)** — Claude's plain-language reading of the week
- Last run: {today}

## Disclaimer

{DISCLAIMER}

## History

Each week's output is archived in [`history/`](history/), date-stamped. The latest is mirrored to `latest/`.
"""


def main():
    # 1. Run the screener (writes outputs/SectorScreen_<date>.{xlsx,html,json})
    screener.run_screen(skip_backtest=False)

    today = date.today().isoformat()
    output_dir = Path(config.OUTPUT_DIR)
    json_path = output_dir / f"SectorScreen_{today}.json"
    html_path = output_dir / f"SectorScreen_{today}.html"

    if not json_path.exists():
        print(f"ERROR: expected {json_path} but it does not exist.", file=sys.stderr)
        sys.exit(1)
    if not html_path.exists():
        print(f"ERROR: expected {html_path} but it does not exist.", file=sys.stderr)
        sys.exit(1)

    # 2. Read structured data
    data = json.loads(json_path.read_text())

    # 3. Generate Claude commentary
    print("Asking Claude for commentary...")
    try:
        commentary = _generate_commentary(data)
    except Exception as e:
        # If Claude fails for any reason, write a placeholder note rather than
        # abort the workflow. The dashboard still lands.
        print(f"WARNING: commentary generation failed: {e}", file=sys.stderr)
        commentary = (
            "## Commentary unavailable\n\n"
            f"Claude commentary failed for this run: `{e}`. "
            "The dashboard is still published below; the methodology and underlying "
            "scores are unchanged."
        )

    # 4. Write summary markdown
    summary_md = _build_summary_md(today, commentary)

    # 5. Publish to weekly/latest/ and weekly/history/<today>/
    repo_root = Path(__file__).parent
    latest = repo_root / "weekly" / "latest"
    history = repo_root / "weekly" / "history" / today
    latest.mkdir(parents=True, exist_ok=True)
    history.mkdir(parents=True, exist_ok=True)

    shutil.copy(html_path, latest / "dashboard.html")
    shutil.copy(html_path, history / "dashboard.html")
    (latest / "summary.md").write_text(summary_md)
    (history / "summary.md").write_text(summary_md)

    # 6. Refresh the weekly index
    (repo_root / "weekly" / "README.md").write_text(_build_weekly_index(today))

    print(f"Published weekly artifacts to {latest} and {history}")


if __name__ == "__main__":
    main()
