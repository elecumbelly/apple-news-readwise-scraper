#!/bin/bash
# Generate and install the per-machine LaunchAgent configuration.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
LABEL="com.applenews-readwise.watcher"
TEMPLATE="$PROJECT_DIR/$LABEL.plist.template"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [ ! -x "$PROJECT_DIR/.venv/bin/python3" ]; then
    echo "error: virtualenv not found; run: uv sync" >&2
    exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents"
sed "s|__PROJECT_DIR__|$PROJECT_DIR|g" "$TEMPLATE" > "$DEST"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$DEST"

echo "Installed and loaded: $LABEL"
