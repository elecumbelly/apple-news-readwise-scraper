#!/bin/bash
# Called when News.app launches - starts the watcher
# The watcher will exit on its own when News closes

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Find uv in common locations
UV_PATH="${HOME}/.local/bin/uv"
if [ ! -x "$UV_PATH" ]; then
    UV_PATH="$(which uv 2>/dev/null || echo "")"
fi

# Only start if not already running
if ! pgrep -f "watch_likes.py" > /dev/null 2>&1; then
    "$UV_PATH" run --with requests --with beautifulsoup4 --with readability-lxml --with lxml python watch_likes.py >> /tmp/applenews-readwise.log 2>&1 &
fi
