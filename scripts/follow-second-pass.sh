#!/usr/bin/env bash
# Keep second_pass.csv current while a day is still being processed, so the review page has crops.
# usage: scripts/follow-second-pass.sh <day-folder-of-clips>   e.g. /Volumes/camera-share/<camera-id>/2026-01-01
set -u
DAY="${1:?day folder}"
OUT="$(lpr where "$DAY")"
# The [p] trick keeps pgrep from matching this script's own command line.
while pgrep -f "lpr_dav_ocr.[p]ipeline|lpr [p]rocess" >/dev/null; do
  lpr second-pass "$OUT" 2>&1 | grep -E "images|done"
  sleep 120
done
echo "processing finished; run: lpr second-pass $OUT"
