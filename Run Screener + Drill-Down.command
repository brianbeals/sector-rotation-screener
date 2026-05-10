#!/bin/bash
# Double-click this file to run the sector rotation screener with drill-down.
# Skips the backtest for faster execution — focuses on the live screen
# and sub-sector drill-down for any Buy signals.
# Output: outputs/SectorScreen_<today>.{xlsx,html}

set -e
cd "$(dirname "$0")"

echo "==============================================="
echo "  Sector Rotation Screener + Drill-Down"
echo "==============================================="
echo ""

# Check Python 3 is present
if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: python3 isn't installed."
    echo "Install it: open Terminal and run 'xcode-select --install',"
    echo "or grab the installer from python.org."
    echo ""
    read -p "Press return to close..." _
    exit 1
fi

# One-time setup: create venv and install packages if .venv is missing
if [ ! -d ".venv" ]; then
    echo "First-time setup — creating Python environment..."
    python3 -m venv .venv
    source .venv/bin/activate
    echo "Installing required packages (yfinance, pandas, fredapi, etc.)..."
    pip install --quiet --upgrade pip
    pip install --quiet -r requirements.txt
    echo "Setup complete."
    echo ""
else
    source .venv/bin/activate
fi

# Load FRED key from .env
if [ -f ".env" ]; then
    set -a
    source .env
    set +a
fi

if [ -z "$FRED_API_KEY" ]; then
    echo "ERROR: FRED_API_KEY not set. Check that .env exists and contains your key."
    read -p "Press return to close..." _
    exit 1
fi

echo "Running screener + drill-down (no backtest — faster)..."
echo ""
python screener.py --no-backtest

echo ""
echo "==============================================="
echo "  Done. Outputs are in:"
echo "  $(pwd)/outputs/"
echo "==============================================="
echo ""

# Open the HTML in the default browser automatically
LATEST_HTML=$(ls -t outputs/SectorScreen_*.html 2>/dev/null | head -1)
if [ -n "$LATEST_HTML" ]; then
    echo "Opening $LATEST_HTML ..."
    open "$LATEST_HTML"
fi

read -p "Press return to close this window..." _
