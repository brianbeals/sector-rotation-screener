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
import markdown as md_lib

import config
import screener


CLAUDE_MODEL = "claude-opus-4-8"  # Anthropic API model ID (pinned snapshot). Bump the version here to upgrade.


DISCLAIMER = (
    "**⚠️ Not financial advice.** This commentary is auto-generated each week by "
    "Anthropic's Claude (an AI model). Brian Beals is not a registered investment "
    "advisor, and Anthropic's Claude is not licensed to provide personalized financial "
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

FORMATTING:
- Do NOT use em dashes. Use commas, periods, or the word "to" for ranges. This is a hard rule.
- Refer to the backtest window as "since {bt_inception}", never as a fixed number of years.

This week's screen output:

Date: {date}
Cycle phase: {cycle_phase}
Cycle reasoning: {cycle_why}

Backtest (since {bt_inception}, {trade_cost_bps} bps trading cost): Strategy {strategy_cum:+.2%} vs SPY {spy_cum:+.2%}. {beats_spy_text}

Sector scores (composite scale 0-100, sorted descending):
{sector_table}

Composite weights: Seasonality {w_season}% + Cycle Fit {w_cycle}% + Relative Strength {w_rs}%.
Signal thresholds: Buy ≥ {signal_buy}, Avoid ≤ {signal_avoid}.

Macro vintage as of: {vintage_date}

Write a markdown commentary in this exact structure (use the headings verbatim):

## What the screen said this week
2-3 short paragraphs. Cover: the current cycle phase classification and the macro signals driving it; which sectors topped the composite (and roughly why, whether seasonality, cycle fit, relative strength, or some combination); any notable Avoid signals.

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


def _backtest_inception_label() -> str:
    """Human label for the backtest start, e.g. 'May 2011'.

    Uses the fixed anchor when set, otherwise the rolling window start.
    """
    import pandas as pd
    if getattr(config, "BACKTEST_START", None):
        start = pd.Timestamp(config.BACKTEST_START)
    else:
        start = pd.Timestamp(date.today()) - pd.DateOffset(years=config.BACKTEST_YEARS)
    return start.strftime("%B %Y")


def _strip_em_dashes(text: str) -> str:
    """Hard guarantee: no em dashes reach the published commentary.

    The prompt already forbids them, but a model can still slip one in, so we
    enforce it on the output. Spaced em dashes become a comma; bare ones too.
    """
    text = text.replace(" — ", ", ").replace(" —", ",").replace("— ", ", ")
    text = text.replace("—", ", ")  # any remaining em dash
    return text


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
        cycle_why=data["cycle"].get("why", "n/a"),
        strategy_cum=bt.get("strategy_cum", 0),
        spy_cum=bt.get("spy_cum", 0),
        bt_inception=_backtest_inception_label(),
        trade_cost_bps=int(config.TRADE_COST_BPS),
        beats_spy_text=beats_text,
        sector_table=_build_sector_table(data["rows"]),
        w_season=int(round(w.seasonality * 100)),
        w_cycle=int(round(w.cycle_fit * 100)),
        w_rs=int(round(w.rel_strength * 100)),
        signal_buy=int(config.SIGNAL_BUY),
        signal_avoid=int(config.SIGNAL_AVOID),
        vintage_date=data["vintage"].get("fred_vintage", "n/a"),
    )

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    return _strip_em_dashes(response.content[0].text.strip())


def _build_summary_md(today: str, commentary: str) -> str:
    return f"""# Weekly Sector Rotation Commentary: {today}

{DISCLAIMER}

[View the dashboard ↗](dashboard.html)

---

{commentary}

---

*By [Brian Beals](https://brianbeals.com). Methodology and code: [github.com/brianbeals/sector-rotation-screener](https://github.com/brianbeals/sector-rotation-screener). Commentary generated by Anthropic's Claude ({CLAUDE_MODEL}). © {today[:4]} Brian Beals.*
"""


def _build_weekly_index(today: str) -> str:
    return f"""# Weekly runs

Auto-generated every Sunday afternoon ET by GitHub Actions. The screener pulls
fresh data from yfinance and FRED, scores the 11 SPDR sector ETFs, runs the
backtest since May 2011, and asks Anthropic's Claude for a short commentary on
what the screen said.

## Latest

- **[Dashboard](latest/dashboard.html)**: score table, seasonality heatmap, RS bars, equity curve, cycle context
- **[Commentary](latest/summary.html)**: an AI reading of this week's screen (or [as markdown](latest/summary.md))
- Last run: {today}

## Disclaimer

{DISCLAIMER}

## History

Each week's output is archived in [`history/`](history/), date-stamped. The latest is mirrored to `latest/`.
"""


# ---------------------------------------------------------------------------
# Brand-styled HTML pages for sector.brianbeals.com
# ---------------------------------------------------------------------------

PAGE_SHELL = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title} | Brian Beals</title>
  <meta name="description" content="{description}">
  <link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='6' fill='%231E3A5F'/%3E%3Ctext x='16' y='15' text-anchor='middle' dominant-baseline='central' fill='white' font-family='system-ui' font-size='16' font-weight='800'%3EBB%3C/text%3E%3C/svg%3E">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Source+Serif+4:opsz,wght@8..60,600&display=swap" rel="stylesheet">
  <style>
    :root {{
      --navy: #1E3A5F;
      --blue: #2E86C1;
      --bg: #F4F6F9;
      --text: #1A1A2A;
      --muted: #6B7280;
      --card: #FFFFFF;
      --warn-bg: #FEF3F2;
      --warn-border: #C0392B;
    }}
    * {{ box-sizing: border-box; }}
    html, body {{ margin: 0; padding: 0; }}
    body {{
      min-height: 100vh;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      color: var(--text);
      background:
        radial-gradient(at 25% 20%, rgba(46,134,193,0.12) 0, transparent 50%),
        radial-gradient(at 75% 80%, rgba(30,58,95,0.10) 0, transparent 50%),
        var(--bg);
      line-height: 1.6;
    }}
    h1, h2, h3 {{
      font-family: "Source Serif 4", Georgia, serif;
      color: var(--navy);
      font-weight: 600;
      letter-spacing: -0.01em;
    }}
    h1 {{ font-size: 2.5rem; margin: 0 0 1rem; }}
    h2 {{ font-size: 1.6rem; margin: 2.5rem 0 1rem; }}
    h3 {{ font-size: 1.15rem; margin: 1.5rem 0 0.5rem; }}
    p {{ margin: 0 0 1rem; }}
    a {{ color: var(--blue); text-underline-offset: 4px; }}
    a:hover {{ text-decoration: none; }}
    header.site {{ padding: 2rem 1.5rem 0.5rem; }}
    header.site nav {{
      max-width: 42rem;
      margin: 0 auto;
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 0.95rem;
    }}
    header.site nav .brand {{
      color: var(--text);
      text-decoration: none;
      font-weight: 600;
    }}
    header.site nav .links {{ display: flex; gap: 1.5rem; }}
    header.site nav .links a {{ color: var(--text); text-decoration: none; }}
    header.site nav .links a:hover {{ text-decoration: underline; }}
    main {{ max-width: 42rem; margin: 0 auto; padding: 3rem 1.5rem 2rem; }}
    .subtitle {{ font-size: 1.2rem; color: var(--muted); margin-bottom: 2rem; }}
    .disclaimer {{
      background: var(--warn-bg);
      border-left: 4px solid var(--warn-border);
      padding: 1rem 1.25rem;
      border-radius: 4px;
      font-size: 0.95rem;
      margin: 2rem 0;
      color: var(--text);
    }}
    .disclaimer strong {{ color: var(--warn-border); }}
    .cards {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 1rem;
      margin: 1.5rem 0;
    }}
    @media (max-width: 600px) {{ .cards {{ grid-template-columns: 1fr; }} }}
    .card {{
      background: var(--card);
      border-radius: 8px;
      padding: 1.5rem;
      box-shadow: 0 2px 8px rgba(0,0,0,0.06);
      text-decoration: none;
      color: var(--text);
      display: block;
      transition: transform 0.15s ease, box-shadow 0.15s ease;
    }}
    .card:hover {{
      transform: translateY(-2px);
      box-shadow: 0 4px 12px rgba(0,0,0,0.08);
      text-decoration: none;
    }}
    .card h3 {{ color: var(--navy); margin-top: 0; }}
    .card p {{ color: var(--muted); margin-bottom: 0; font-size: 0.95rem; }}
    .back {{
      display: inline-block;
      margin-bottom: 1.5rem;
      color: var(--blue);
      text-decoration: none;
      font-size: 0.95rem;
    }}
    .back:hover {{ text-decoration: underline; }}
    .commentary {{ font-size: 1.02rem; }}
    .commentary p {{ margin: 0 0 1rem; }}
    .commentary h2 {{ font-size: 1.4rem; margin: 2rem 0 0.75rem; }}
    .commentary strong {{ color: var(--navy); }}
    .attribution {{
      font-size: 0.85rem;
      color: var(--muted);
      font-style: italic;
      margin-top: 2rem;
    }}
    .attribution a {{ color: var(--muted); }}
    footer.site {{
      max-width: 42rem;
      margin: 2rem auto;
      padding: 1.5rem;
      font-size: 0.85rem;
      color: var(--muted);
      text-align: center;
    }}
    footer.site a {{ color: inherit; text-underline-offset: 3px; }}
    code {{
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      background: rgba(30,58,95,0.06);
      padding: 0.1em 0.4em;
      border-radius: 3px;
      font-size: 0.92em;
    }}
    hr {{ border: none; border-top: 1px solid rgba(30,58,95,0.12); margin: 2rem 0; }}
  </style>
</head>
<body>
  <header class="site">
    <nav>
      <a href="https://brianbeals.com/" class="brand">Brian Beals</a>
      <div class="links">
        <a href="https://brianbeals.com/about">About</a>
        <a href="https://brianbeals.com/contact">Contact</a>
      </div>
    </nav>
  </header>
  <main>
{content}
  </main>
  <footer class="site">
    <svg viewBox="0 0 32 32" width="16" height="16" style="vertical-align:middle;margin-right:6px" aria-label="Brian Beals"><rect width="32" height="32" rx="6" fill="#1E3A5F"/><text x="16" y="15" text-anchor="middle" dominant-baseline="central" fill="#fff" font-family="-apple-system,system-ui,sans-serif" font-size="16" font-weight="800" letter-spacing="-0.04em">BB</text></svg>© {year} Brian Beals · <a href="https://brianbeals.com">brianbeals.com</a> · <a href="https://github.com/brianbeals/sector-rotation-screener">github</a>
  </footer>
</body>
</html>
"""


_DISCLAIMER_HTML = (
    '<div class="disclaimer">'
    '<strong>⚠️ Not financial advice.</strong> '
    "This is auto-generated each week by Anthropic's Claude (an AI model). "
    "Brian Beals is not a registered investment advisor, and Anthropic's Claude is not "
    "licensed to provide personalized financial advice. The screener is a "
    "research and methodology demo, not a recommendation system. Past "
    "performance does not predict future results. Do your own research before "
    "making any investment decisions."
    "</div>"
)


def _wrap_in_page(title: str, description: str, content: str, year: int) -> str:
    return PAGE_SHELL.format(
        title=title,
        description=description,
        content=content,
        year=year,
    )


def _build_index_html(today: str) -> str:
    pretty = date.fromisoformat(today).strftime("%B %d, %Y")
    year = date.fromisoformat(today).year
    content = f"""    <h1>Sector Rotation Screen</h1>
    <p class="subtitle">A weekly methodology demo. Open source, transparent rules.</p>

    <p>Every Sunday afternoon, GitHub Actions runs an 11-SPDR-ETF screen against three signals (seasonality, economic-cycle fit, and relative strength vs SPY), runs a backtest anchored to May 2011, and asks Anthropic's Claude for a brief commentary on what the output said.</p>

    {_DISCLAIMER_HTML}

    <h2>This week: {pretty}</h2>
    <div class="cards">
      <a href="weekly/latest/dashboard.html" class="card">
        <h3>Dashboard ↗</h3>
        <p>Score table, seasonality heatmap, RS bars, equity curve, cycle context.</p>
      </a>
      <a href="weekly/latest/summary.html" class="card">
        <h3>Commentary ↗</h3>
        <p>An AI reading of this week's screen.</p>
      </a>
    </div>

    <h2>Why this exists</h2>
    <p>Sector rotation isn't an investment thesis I'm pitching. It's a testbed for the kind of analytical workflow I'd build for a CIO or CDO who wants to evaluate macro exposures across business units: ingest data, score against multiple signals, output something humans can read, backtest the methodology before trusting it.</p>
    <p>The same pattern works for portfolio companies, supply-chain risk, customer-segment health, or any question where "let's score it against multiple signals and see how that would have played out historically" beats gut feel.</p>

    <h2>Open source</h2>
    <p>The screener, methodology, weekly automation, and license live at <a href="https://github.com/brianbeals/sector-rotation-screener">github.com/brianbeals/sector-rotation-screener</a>.</p>

    <h2>Past runs</h2>
    <p><a href="weekly/history/">Browse the date-stamped archive →</a></p>
"""
    return _wrap_in_page(
        title="Sector Rotation Screen",
        description="Weekly screen of 11 SPDR sector ETFs with composite scoring and AI commentary from Anthropic's Claude. Open-source methodology demo by Brian Beals.",
        content=content,
        year=year,
    )


def _build_history_index_html(history_root: Path) -> str:
    """List every archived weekly run as a styled, branded index page."""
    if not history_root.exists():
        runs = []
    else:
        runs = sorted(
            [d.name for d in history_root.iterdir() if d.is_dir() and len(d.name) == 10],
            reverse=True,
        )

    year = date.today().year
    if runs:
        rows = []
        for run_date in runs:
            try:
                pretty = date.fromisoformat(run_date).strftime("%B %d, %Y")
            except ValueError:
                pretty = run_date
            rows.append(f"""      <div class="card">
        <h3>{pretty}</h3>
        <p>Dashboard, commentary, and disclaimer for the {pretty} run.</p>
        <p style="margin-top:0.75rem; font-size:0.9rem;"><a href="{run_date}/dashboard.html" style="color:var(--blue); text-decoration:none;">View dashboard ↗</a> &nbsp;·&nbsp; <a href="{run_date}/summary.html" style="color:var(--blue); text-decoration:none;">View commentary ↗</a></p>
      </div>""")
        cards_html = "\n".join(rows)
        body = f"""    <a href="../../" class="back">← Sector Rotation Screen home</a>

    <h1>Past runs</h1>
    <p class="subtitle">Every weekly run, date-stamped. The latest is also mirrored at <a href="../latest/dashboard.html">weekly/latest/</a>.</p>

    <div class="cards" style="grid-template-columns: 1fr;">
{cards_html}
    </div>
"""
    else:
        body = """    <a href="../../" class="back">← Sector Rotation Screen home</a>

    <h1>Past runs</h1>
    <p class="subtitle">The first weekly run hasn't happened yet. Check back after Sunday afternoon ET, or trigger a run manually from the <a href="https://github.com/brianbeals/sector-rotation-screener/actions">Actions tab</a>.</p>
"""
    return _wrap_in_page(
        title="Past runs",
        description="Date-stamped archive of every weekly sector rotation screen run, with dashboards and AI commentary.",
        content=body,
        year=year,
    )


def _build_summary_html(today: str, commentary_md: str) -> str:
    pretty = date.fromisoformat(today).strftime("%B %d, %Y")
    year = date.fromisoformat(today).year
    # Render Claude's commentary (markdown) to HTML.
    rendered = md_lib.markdown(
        commentary_md,
        extensions=["extra", "sane_lists"],
    )
    content = f"""    <a href="../../" class="back">← Sector Rotation Screen home</a>

    <h1>Weekly Commentary: {pretty}</h1>

    {_DISCLAIMER_HTML}

    <p><a href="dashboard.html">View this week's dashboard ↗</a></p>

    <hr>

    <div class="commentary">
{rendered}
    </div>

    <hr>

    <p class="attribution">
      By <a href="https://brianbeals.com">Brian Beals</a>. Methodology and code: <a href="https://github.com/brianbeals/sector-rotation-screener">github.com/brianbeals/sector-rotation-screener</a>. Commentary generated by Anthropic's Claude ({CLAUDE_MODEL}).
    </p>
"""
    return _wrap_in_page(
        title=f"Weekly Commentary: {pretty}",
        description=f"Anthropic's Claude commentary on the {pretty} sector rotation screen output. Not financial advice.",
        content=content,
        year=year,
    )


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

    # 4. Build all the deliverables
    summary_md = _build_summary_md(today, commentary)
    summary_html = _build_summary_html(today, commentary)
    index_html = _build_index_html(today)

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
    (latest / "summary.html").write_text(summary_html)
    (history / "summary.html").write_text(summary_html)

    # 6. Refresh the weekly index (markdown, for github.com viewing)
    (repo_root / "weekly" / "README.md").write_text(_build_weekly_index(today))

    # 7. Refresh the styled landing page at the repo root (sector.brianbeals.com/)
    (repo_root / "index.html").write_text(index_html)

    # 8. Refresh the history index (lists every dated run)
    history_root = repo_root / "weekly" / "history"
    (history_root / "index.html").write_text(_build_history_index_html(history_root))

    print(f"Published weekly artifacts to {latest} and {history}")
    print(f"Refreshed landing page: {repo_root / 'index.html'}")
    print(f"Refreshed history index: {history_root / 'index.html'}")


if __name__ == "__main__":
    main()
