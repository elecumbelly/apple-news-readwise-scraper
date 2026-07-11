#!/bin/bash
# Apple News to Readwise - Background Watcher
# Run this to watch for saved articles and send them to Readwise

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Find uv in common locations
UV_PATH="${HOME}/.local/bin/uv"
if [ ! -x "$UV_PATH" ]; then
    UV_PATH="$(which uv 2>/dev/null || echo "")"
fi

"$UV_PATH" run --with requests --with beautifulsoup4 --with readability-lxml --with lxml watch_likes.py
