#!/bin/bash
# Stop and remove the launchd agent. State and logs are kept.
LABEL="local.mac-refurb-tracker"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null && echo "stopped $LABEL" || echo "$LABEL was not running"
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
