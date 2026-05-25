#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

URL="${1:-http://127.0.0.1:5173}"
OUT_DIR="${UI_SCREENSHOT_DIR:-reports/ui-screenshots}"
BROWSER_USE_BIN="${BROWSER_USE_BIN:-browser-use}"

if ! command -v "$BROWSER_USE_BIN" >/dev/null 2>&1; then
  if [[ -x "$HOME/browser-use-env/bin/browser-use" ]]; then
    BROWSER_USE_BIN="$HOME/browser-use-env/bin/browser-use"
  else
    echo "browser-use CLI is required for screenshot checks." >&2
    exit 1
  fi
fi

trap '"$BROWSER_USE_BIN" close >/dev/null 2>&1 || true' EXIT

mkdir -p "$OUT_DIR"
"$BROWSER_USE_BIN" open "$URL" >/dev/null
"$BROWSER_USE_BIN" python "browser.wait(5)" >/dev/null
"$BROWSER_USE_BIN" screenshot "$OUT_DIR/desktop.png" >/dev/null

python_bin="${PYTHON:-.venv/bin/python}"
"$python_bin" - <<'PY'
from __future__ import annotations

import os
import struct
from pathlib import Path

path = Path(os.environ.get("UI_SCREENSHOT_DIR", "reports/ui-screenshots")) / "desktop.png"
if not path.exists():
    raise SystemExit(f"missing screenshot: {path}")

data = path.read_bytes()
if len(data) < 50_000:
    raise SystemExit(f"screenshot is unexpectedly small: {len(data)} bytes")
if data[:8] != b"\x89PNG\r\n\x1a\n":
    raise SystemExit(f"screenshot is not a PNG: {path}")

width, height = struct.unpack(">II", data[16:24])
if width < 1_000 or height < 700:
    raise SystemExit(f"screenshot dimensions are too small: {width}x{height}")

print(f"ui screenshot ok: {path} {width}x{height} {len(data)} bytes")
PY
