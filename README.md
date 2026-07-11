# Apple News → Readwise Scraper

Automatically sends saved Apple News articles to Readwise Reader.

## How It Works

1. **LaunchAgent daemon** watches for News.app to open/close (zero CPU when idle)
2. When News opens, the watcher monitors your saved articles folder
3. When you save an article, it:
   - Reads the full article body from the Apple News on-disk cache (fast path
     for articles you've viewed, no UI automation)
   - On a cache miss, tries to fetch from the publisher URL (with images)
   - For articles with no publisher URL, falls back to clipboard
     extraction from the News window (Edit > Select All / Copy); pages the
     publisher fetch can't retrieve get a URL-only save instead
4. Sends to Readwise Reader with full text and images
5. If extraction keeps failing, retries up to 3 times (30s apart), then saves
   the article's link to Readwise anyway and notifies you — a save is never
   silently dropped

## Setup

### Prerequisites
- macOS with Apple News
- [uv](https://github.com/astral-sh/uv) for Python dependency management
- Readwise Reader account

### Installation

1. Store your API keys in the macOS Keychain (preferred):
   ```bash
   # READWISE_TOKEN - get from https://readwise.io/access_token
   security add-generic-password -s readwise-token -a "$USER" -w "YOUR_READWISE_TOKEN"
   # IMGBB_API_KEY (optional; only used by the dormant screenshot-upload code,
   # which no live path calls today) - get from https://api.imgbb.com/
   security add-generic-password -s imgbb-api-key -a "$USER" -w "YOUR_IMGBB_KEY"
   ```

   Secrets resolve lazily through a fallback chain: Keychain →
   `READWISE_TOKEN`/`IMGBB_API_KEY` environment variables → a `.env` file next
   to the scripts. If no Readwise token is found anywhere, the watcher logs the
   problem and shows a macOS notification at startup instead of failing
   silently on every save.

2. Install the project dependencies (the daemon and watcher run from this
   virtualenv):
   ```bash
   uv sync
   ```

3. Install the LaunchAgent (this generates a machine-specific plist from the
   portable template):
   ```bash
   ./install.sh
   ```

4. Grant **Full Disk Access** to Terminal (System Settings → Privacy & Security →
   Full Disk Access). The watcher is launched via a hidden Terminal window so it
   inherits Terminal's Full Disk Access — required to read the Apple News data.
   Also grant Accessibility permissions when prompted (for the copy fallback).

### Usage

1. Open Apple News
2. Navigate to an article
3. Save the article (bookmark icon)
4. Article appears in Readwise Reader

**No manual selection needed.** Articles you have viewed are extracted straight
from the Apple News on-disk cache (full body text, no UI automation). The
clipboard fallback — which clicks into the article body and runs
`Edit > Select All` / `Edit > Copy` automatically — only runs on a cache miss.
If Apple News accessibility changes, re-grant Accessibility permissions to your
terminal app for that fallback.

For subscribed sites (The Times, etc.), the article body comes from the News
cache when available; otherwise the publisher URL is saved to Readwise
directly (a direct fetch of those sites wouldn't return the article).

## Files

| File | Purpose |
|------|---------|
| `watch_likes.py` | Main watcher - monitors saves, extracts content, sends to Readwise |
| `news_cache.py` | Reads full article bodies straight from the Apple News on-disk cache (lookup by URL or title) |
| `news_watcher_daemon.py` | Event-driven daemon using PyObjC NSWorkspace notifications; launches the watcher via a hidden Terminal |
| `watcher_hidden.command` | Launcher the daemon runs in a hidden Terminal so the watcher inherits Full Disk Access |
| `test_news_cache.py`, `test_retry_logic.py`, `test_token_fallback.py` | Unit tests for cache extraction, failed-article retry, and token resolution |
| `scrape_current_article.py`, `scrape_to_readwise.py` | Legacy standalone one-shot scrapers (manual use; superseded by the watcher) |
| `probe_news_cache.py` / `.command`, `search_news_cache.py` | Debug utilities for inspecting the News asset-store cache |
| `generate_flow_diagram.py` | Renders `apple_news_readwise_flow.png` (the pipeline diagram) |
| `run.sh`, `run_watcher.command`, `run_scraper.scpt`, `news_launcher.sh` | Older manual launchers kept for debugging (not on the auto path) |
| `com.applenews-readwise.watcher.plist.template`, `install.sh` | Portable LaunchAgent template and installer |

## Configuration

### Subscribed Sites

Add your subscription sites to skip the publisher fetch (a direct fetch of
those sites wouldn't return the article):

```python
SUBSCRIBED_SITES = [
    "thetimes.com",
    "thetimes.co.uk",
    "telegraph.co.uk",
    # Add more...
]
```

### Logs

- Daemon: `/tmp/applenews-daemon.log`
- Watcher: `debug.log` in the project directory

## Future Development

- [ ] Extract actual images from clipboard RTF (if Apple News supports it)
- [ ] Multiple screenshot capture for long articles (scroll and capture)
- [ ] OCR on screenshots to extract image captions
- [ ] Support for highlighting/annotations in Apple News
- [ ] Sync reading progress back to Apple News
- [ ] Browser extension alternative for non-macOS
- [x] Move API keys out of source (Keychain → env var → `.env` fallback chain)
- [x] Add tests for content extraction (`test_news_cache.py`)
- [ ] Handle duplicate article detection better
- [ ] Persist the failed-article retry queue across News relaunches
