#!/bin/zsh
set -euo pipefail
cd /Users/x/ah-stock-screener
LOCK_DIR=".us-update.lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "$(date +%Y-%m-%dT%H:%M:%S%z) us update skipped: another run is active"
  exit 0
fi
trap 'rmdir "$LOCK_DIR"' EXIT INT TERM
/Users/x/ah-stock-screener/.venv/bin/python -m us_screener.cli update --history-top 4000 --lookback-days 430 --fundamentals-top 0 --json
