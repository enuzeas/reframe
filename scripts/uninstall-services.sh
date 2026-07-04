#!/usr/bin/env bash
# Unload + remove the reframe launchd agents (reverse of install-services.sh).
# The persisted channel layout (state.json) is left in place - reinstalling restores it.
set -euo pipefail

UID_NUM="$(id -u)"
AGENTS="$HOME/Library/LaunchAgents"

for label in com.reframe.server com.reframe.mediamtx; do
  launchctl bootout "gui/${UID_NUM}/${label}" 2>/dev/null || true
  rm -f "$AGENTS/$label.plist"
  echo "removed ${label}"
done

echo "done. (channel layout kept at ~/Library/Application Support/reframe/state.json)"
