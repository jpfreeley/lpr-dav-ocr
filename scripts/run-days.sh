#!/usr/bin/env bash
# Process several days, then run the second pass on each. Resumable: run the same command again.
# usage: scripts/run-days.sh 2026-01-02 2026-01-01 ...      (needs paths.camera_dir in the config)
set -eu
CAM="${LPR_CAMERA_DIR:-$(lpr config show | python3 -c 'import json,sys; print(json.load(sys.stdin)["paths"]["camera_dir"])')}"
[ -n "$CAM" ] || { echo "set paths.camera_dir in the config (or LPR_CAMERA_DIR)"; exit 1; }
for d in "$@"; do
  echo "=== $d $(date +%T)"
  lpr process "$CAM/$d"
  lpr second-pass "$(lpr where "$CAM/$d")"
done
echo "=== all done $(date +%T)"
