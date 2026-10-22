#!/usr/bin/env bash
# Install or remove the unattended watcher as a macOS LaunchAgent (runs while you are logged in).
# usage: scripts/install-watcher.sh install | uninstall | status | logs
# Needs paths.camera_dir in the config. Override the label with LPR_LABEL, the binary with LPR_BIN.
set -eu
LABEL="${LPR_LABEL:-com.example.lpr-watch}"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LPR_BIN="${LPR_BIN:-$(command -v lpr)}"
LOG="$HOME/.lpr-ocr/logs/watch.log"
case "${1:-status}" in
  install)
    [ -x "$LPR_BIN" ] || { echo "lpr not found; activate your virtualenv or set LPR_BIN"; exit 1; }
    mkdir -p "$(dirname "$LOG")" "$HOME/Library/LaunchAgents"
    sed -e "s|@LABEL@|$LABEL|g" -e "s|@LPR_BIN@|$LPR_BIN|g" -e "s|@LOG@|$LOG|g" \
        "$HERE/../config/launchd/com.example.lpr-watch.plist.template" > "$PLIST"
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST"
    echo "installed $LABEL. log: $LOG" ;;
  uninstall)
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"; echo "removed $LABEL" ;;
  logs) tail -n 40 "$LOG" ;;
  *) launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null | grep -E "state|pid|last exit" || echo "not installed" ;;
esac
