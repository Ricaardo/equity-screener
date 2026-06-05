#!/bin/zsh
# Resident local dashboard server. Serves the prebuilt frontend/dist over HTTP.
# Data is fetched client-side at runtime from the data-latest branch (the URL is
# baked into the bundle at build time via VITE_DATA_BASE_URL), so this server
# never needs a rebuild when reports refresh — it just serves static assets.
#
# Managed by launchd (~/Library/LaunchAgents/com.ah-screener.dashboard.plist):
# RunAtLoad + KeepAlive keep it resident across logout/reboot/crash.
#
# To rebuild with live data:
#   cd frontend && VITE_DATA_BASE_URL="https://raw.githubusercontent.com/Ricaardo/ah-stock-screener/data-latest" npm run build
set -euo pipefail

PORT="${DASHBOARD_PORT:-4319}"
HOST="${DASHBOARD_HOST:-127.0.0.1}"
NODE_BIN="/usr/local/bin/node"

cd /Users/x/ah-stock-screener/frontend

if [[ ! -f dist/index.html ]]; then
  echo "$(date +%Y-%m-%dT%H:%M:%S%z) dist/ missing — build first: VITE_DATA_BASE_URL=... npm run build" >&2
  exit 1
fi

# exec so launchd tracks the vite process directly (clean KeepAlive restarts).
exec "$NODE_BIN" node_modules/vite/bin/vite.js preview --host "$HOST" --port "$PORT" --strictPort
