#!/bin/zsh
set -euo pipefail
cd /Users/x/nimbus-os/equity-screener
LOCK_DIR=".exclusives.lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "$(date +%Y-%m-%dT%H:%M:%S%z) exclusives skipped: another run is active"
  exit 0
fi
trap 'rmdir "$LOCK_DIR"' EXIT INT TERM
# akshare 独家入仓：龙虎榜 / 资金流 / 北向。akshare 断连时优雅降级（写 ingest_failures，不 crash）。
.venv/bin/ah-screener update-exclusives --days 5 --top 100
