#!/usr/bin/env python3
"""Rebuild index.html (the sector.brianbeals.com landing page) from the current
weekly/history contents, without running the screener.

Why this exists
---------------
index.html is a generated artifact. Editing the template that produces it does
NOT change the published page; only a regeneration does. Historically the only
way to regenerate was a full `weekly.yml` run, which re-fetches market data,
spends an Anthropic API call, and writes a new dated folder under weekly/. That
is a lot of side effects when all you changed was a meta tag.

This script does the landing page and nothing else. No network, no API key, no
new archive entry. Use it after any edit to PAGE_SHELL in weekly_run.py.

Note the landing page is built by PAGE_SHELL in weekly_run.py, while the dated
pages under weekly/ come from HTML_TEMPLATE in report.py. The two <head> blocks
look nearly identical and are easy to confuse. Changing one does not change the
other.

Usage:
    ./.venv/bin/python refresh_index.py
"""
from __future__ import annotations

import pathlib
import sys


def main() -> int:
    root = pathlib.Path(__file__).resolve().parent
    sys.path.insert(0, str(root))

    history = root / "weekly" / "history"
    if not history.is_dir():
        print(f"error: {history} not found; run the weekly workflow at least once.")
        return 1

    dates = sorted(p.name for p in history.iterdir() if p.is_dir())
    if not dates:
        print(f"error: no dated runs under {history}.")
        return 1
    latest = dates[-1]

    # Imported here, after sys.path is set, and deliberately late so the error
    # message below is useful when the venv is missing a dependency.
    try:
        import weekly_run
    except ImportError as e:
        print(f"error: could not import weekly_run ({e}).")
        print("Run with the project venv: ./.venv/bin/python refresh_index.py")
        return 1

    target = root / "index.html"
    before = target.read_text() if target.exists() else ""
    after = weekly_run._build_index_html(latest)
    target.write_text(after)

    if before == after:
        print(f"index.html already current for {latest}; nothing changed.")
    else:
        print(f"regenerated index.html for {latest}")
        for tag in ("og:image", "og:url", "twitter:card"):
            print(f"  {tag:14} {'present' if tag in after else 'MISSING'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
