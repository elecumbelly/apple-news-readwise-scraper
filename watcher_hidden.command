#!/bin/bash
# Hosted watcher launcher.
#
# Run BY Terminal (via the daemon's `do script`) so the watcher inherits
# Terminal's Full Disk Access — the only reliable way for a launchd-rooted setup
# to read the Apple News container. The daemon hides Terminal's window right
# after launching this, so nothing visible pops up.
#
# Uses the stable venv python directly (no `uv run` re-resolution).

# Resolve the project dir from this script's own location (portable; survives
# the project being moved or cloned elsewhere).
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$PROJECT_DIR/.venv/bin/python3" "$PROJECT_DIR/watch_likes.py"
