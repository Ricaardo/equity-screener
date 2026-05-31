#!/usr/bin/env bash
# Publish the latest report JSON to a dedicated data branch (default: data-latest)
# served via raw.githubusercontent.com (CORS: *). The dashboard fetches that base
# at runtime (path C), so this refreshes production data WITHOUT a redeploy.
#
# Uses git plumbing to push a single parentless commit each run: the branch stays
# tiny, history never bloats, and the working tree / index are untouched. Wire
# this into the daily pipeline after reports are generated.
#
# Usage: scripts/publish_reports_branch.sh [branch]
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

BRANCH="${1:-data-latest}"
REPORT="reports/ah-screening-report-latest.json"
US="reports/us-premarket/us-premarket-latest.json"

for f in "$REPORT" "$US"; do
  if [[ ! -f "$f" ]]; then
    echo "missing $f — run the screener pipeline first" >&2
    exit 1
  fi
done

# Write blobs into the object store, assemble a tree, commit it with no parent.
blob_report="$(git hash-object -w "$REPORT")"
blob_us="$(git hash-object -w "$US")"
tree="$(printf '100644 blob %s\tah-screening-report-latest.json\n100644 blob %s\tus-premarket-latest.json\n' \
  "$blob_report" "$blob_us" | git mktree)"
commit="$(git commit-tree "$tree" -m "data: refresh latest reports $(date '+%F %T')")"

git push -f origin "$commit:refs/heads/$BRANCH"
echo "published $REPORT + $US to origin/$BRANCH"
