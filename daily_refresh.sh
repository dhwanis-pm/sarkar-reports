#!/bin/bash
# Sarkar daily refresh — runs the Meta Ads fetch, then rebuilds dashboard.html
# from the freshly fetched data. This is what the 8am scheduled job calls.

# Move into this script's own folder, so it works no matter where launchd runs it from
cd "$(dirname "$0")"

echo "=== $(date) — Starting daily refresh ==="

python3 fetch_meta_ads.py
FETCH_STATUS=$?

if [ $FETCH_STATUS -ne 0 ]; then
    echo "Fetch failed (exit code $FETCH_STATUS) — skipping dashboard rebuild."
    exit 1
fi

python3 generate_dashboard.py

echo "=== $(date) — Daily refresh complete ==="
