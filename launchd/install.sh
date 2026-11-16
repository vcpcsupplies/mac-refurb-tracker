#!/bin/bash
# Install / reinstall the launchd agent that polls every 2 minutes while the Mac is awake.
#   NTFY_TOPIC=your-topic ./launchd/install.sh
# Missed runs during sleep are coalesced into one run on wake.
set -euo pipefail
LABEL="local.mac-refurb-tracker"
DIR="$(cd "$(dirname "$0")/.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/mac-refurb-tracker.log"
PY="${PYTHON:-$(command -v python3)}"

# iCloud "Optimize Mac Storage" evicts files under ~/Documents and ~/Desktop to
# placeholders that a launchd background job cannot read (fails with
# "Resource deadlock avoided"). Refuse to install from a synced folder.
case "$DIR" in
  "$HOME/Documents"*|"$HOME/Desktop"*|"$HOME/Library/Mobile Documents"*)
    echo "refusing: $DIR is iCloud-synced; move the folder (e.g. to ~/mac-refurb-tracker) first" >&2
    exit 1 ;;
esac
: "${NTFY_TOPIC:?set NTFY_TOPIC to your ntfy topic}"
INTERVAL="${INTERVAL:-120}"

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string>
    <string>$DIR/refurb_tracker.py</string>
    <string>check</string>
    <string>--jitter-start</string><string>30</string>
  </array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>NTFY_TOPIC</key><string>$NTFY_TOPIC</string>
    <key>REFURB_STATE</key><string>$DIR/state.local.json</string>
  </dict>
  <key>StartInterval</key><integer>$INTERVAL</integer>
  <key>RunAtLoad</key><true/>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
PLIST
chmod 600 "$PLIST"   # contains the ntfy topic
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "installed $LABEL: every ${INTERVAL}s, log $LOG"
