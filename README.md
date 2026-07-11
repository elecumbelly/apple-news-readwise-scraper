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

### Beginner quick start

This project works only on a Mac with Apple News. You will also need a Readwise
Reader account.

1. Download the project: on GitHub, click **Code** → **Download ZIP**. Double
   click the downloaded ZIP file to unpack it.
2. Install [uv](https://docs.astral.sh/uv/getting-started/installation/), the
   tool that installs this project's Python requirements.
3. Open **Terminal** (press Command-Space, type `Terminal`, then press Return).
   Type `cd ` (including the space), drag the unpacked project folder onto the
   Terminal window, and press Return.
4. Get your Readwise access token from
   [Readwise](https://readwise.io/access_token). **Do not share this token or
   put it in a screenshot.** Paste these three lines into Terminal, pressing
   Return after each one:
   ```bash
   read -s "READWISE_TOKEN?Paste your Readwise token, then press Return: "
   security add-generic-password -U -s readwise-token -a "$USER" -w "$READWISE_TOKEN"
   unset READWISE_TOKEN
   ```
   Nothing appears while you paste the token—that is normal. This stores it in
   your Mac's Keychain, which is the recommended and safest option.
5. Paste these commands into Terminal:
   ```bash
   uv sync
   ./install.sh
   ```
6. Open **System Settings** → **Privacy & Security** → **Full Disk Access** and
   turn on access for **Terminal**. Also allow Accessibility access if macOS
   asks. These permissions let the app read Apple News and use the copy
   fallback when needed.

### Test it

1. Open Apple News.
2. Open an article, then click its bookmark button to save it.
3. Open Readwise Reader: the article should appear there shortly.

### For developers

The Keychain method above is recommended. The watcher can also read
`READWISE_TOKEN` from an environment variable or a local, gitignored `.env`
file. If no token is configured, it shows a macOS notification instead of
silently failing.

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
