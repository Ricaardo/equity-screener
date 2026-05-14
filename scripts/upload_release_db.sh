#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

TAG="${1:-data-$(date +%F)}"
DB_PATH="${AH_SCREENER_DB:-data/ah_screener.duckdb}"
DATE_PART="${TAG#data-}"
DIST_DIR="dist"
GZ_PATH="$DIST_DIR/ah_screener-$DATE_PART.duckdb.gz"
SHA_PATH="$GZ_PATH.sha256"

if ! command -v gh >/dev/null 2>&1; then
  echo "gh CLI is required to upload release assets." >&2
  exit 1
fi

if [[ ! -f "$DB_PATH" ]]; then
  echo "Database not found: $DB_PATH" >&2
  exit 1
fi

mkdir -p "$DIST_DIR"
gzip -c "$DB_PATH" > "$GZ_PATH"
shasum -a 256 "$GZ_PATH" > "$SHA_PATH"

if ! gh release view "$TAG" >/dev/null 2>&1; then
  gh release create "$TAG" --title "$TAG" --notes "DuckDB data snapshot for $DATE_PART"
fi

gh release upload "$TAG" "$DB_PATH" "$GZ_PATH" "$SHA_PATH" --clobber
gh release view "$TAG" --json assets,url,tagName
