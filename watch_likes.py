#!/usr/bin/env python3
"""
Apple News Saved Articles -> Readwise

Watches for newly SAVED articles in Apple News and sends them to Readwise Reader.
Run this in the background while using Apple News.

HOW TO USE:
- Instead of "liking" an article, use "Save" (bookmark icon) in Apple News
- This script watches for new saved articles and sends them to Readwise
- The "Save" action is tracked locally and is easy to monitor

The approach:
1. Monitor the reading-list file for changes
2. Parse to find newly saved article IDs
3. Convert article IDs to publisher URLs
4. Scrape and send to Readwise
"""

import json
import os
import subprocess
import sys
import time
import hashlib
import re
from pathlib import Path
from datetime import datetime

# Read article bodies straight from the Apple News on-disk cache when available.
# Guarded so a missing/broken module just disables the fast path rather than
# taking down the watcher — every cache path has a full fallback below.
try:
    import news_cache
except Exception:  # pragma: no cover - defensive import
    news_cache = None

def _get_keychain_value(service: str) -> str:
    """Read a secret from macOS Keychain."""
    result = subprocess.run(
        ["security", "find-generic-password", "-s", service, "-w"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        return result.stdout.strip()
    return ""

# Secrets resolve lazily (not at import) through a fallback chain, so a locked
# Keychain or missing entry doesn't permanently blank the token for the whole
# watcher session: macOS Keychain -> environment variable -> project .env file.
ENV_FILE = Path(__file__).parent / ".env"
_SECRET_CACHE: dict[str, str] = {}


def _get_env_file_value(name: str) -> str:
    """Read NAME=value from the project .env file. Empty string if absent."""
    try:
        for line in ENV_FILE.read_text().splitlines():
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip().strip("'\"")
    except OSError:
        pass
    return ""


def _get_secret(keychain_service: str, env_name: str) -> str:
    """Resolve a secret via Keychain -> env var -> .env; cache successes only.

    Misses are not cached so a fix (unlocked Keychain, added entry) is picked
    up on the next call without restarting the watcher.
    """
    cached = _SECRET_CACHE.get(keychain_service)
    if cached:
        return cached
    value = (
        _get_keychain_value(keychain_service)
        or os.environ.get(env_name, "").strip()
        or _get_env_file_value(env_name)
    )
    if value:
        _SECRET_CACHE[keychain_service] = value
    return value


def get_readwise_token() -> str:
    return _get_secret("readwise-token", "READWISE_TOKEN")


def get_imgbb_api_key() -> str:
    return _get_secret("imgbb-api-key", "IMGBB_API_KEY")

# Paths to Apple News data files - use explicit path for launchd compatibility
NEWS_DATA_DIR = Path.home() / "Library/Containers/com.apple.news/Data/Library/Application Support/com.apple.news/com.apple.news.public-com.apple.news.private-production"
READING_LIST_FILE = NEWS_DATA_DIR / "reading-list"

# Track seen articles
SEEN_ARTICLES_FILE = Path(__file__).parent / ".seen_articles.json"

# Sites where you have subscriptions - skip the publisher fetch (it would hit the
# paywall without cookies) and save the publisher URL directly to Readwise
SUBSCRIBED_SITES = [
    "thetimes.com",
    "thetimes.co.uk",
    "telegraph.co.uk",
]

BLOCK_PAGE_INDICATORS = [
    "security systems have detected some unusual activity",
    "regain access to the telegraph website",
    "vpn client",
    "visit the telegraph website using a different web browser",
    "unusual activity on this connection",
    "verify you are human",
    "captcha",
    "cloudflare",
    "access denied",
    "checking if the site connection is secure",
]
DEBUG_LOG_FILE = Path(__file__).parent / "debug.log"
STARTUP_SETTLE_SECONDS = 15


def watcher_log(message: str):
    """Write a timestamped watcher message to stdout and the debug log."""
    timestamp = datetime.now().strftime('%H:%M:%S')
    line = f"[{timestamp}] {message}"
    print(line)
    try:
        with open(DEBUG_LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load_seen_articles() -> set:
    """Load previously seen article IDs."""
    if SEEN_ARTICLES_FILE.exists():
        try:
            with open(SEEN_ARTICLES_FILE) as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()


def save_seen_articles(articles: set):
    """Save seen article IDs."""
    with open(SEEN_ARTICLES_FILE, "w") as f:
        json.dump(list(articles), f)


def extract_article_ids_from_reading_list(debug_log=None) -> set:
    """Extract article IDs from the reading-list (saved articles) file.

    The reading-list file contains binary data with article IDs that start with 'A'.
    These are articles the user has SAVED (bookmarked) in Apple News.
    Apple News article IDs are exactly 22-23 characters (A + 21-22 base64 chars).
    """
    def debug(msg):
        if debug_log:
            with open(debug_log, "a") as f:
                f.write(f"[extract] {msg}\n")

    debug(f"READING_LIST_FILE = {READING_LIST_FILE}")
    debug(f"exists = {READING_LIST_FILE.exists()}")

    if not READING_LIST_FILE.exists():
        debug("File does not exist, returning empty set")
        return set()

    try:
        with open(READING_LIST_FILE, "rb") as f:
            data = f.read()

        debug(f"Read {len(data)} bytes from file")

        # Decode as ASCII, ignoring errors
        text = data.decode("ascii", errors="ignore")

        debug(f"Decoded to {len(text)} chars")

        # Find article IDs - they start with A and are exactly 22-23 chars
        # Format: A + 21-22 base64-like characters (letters, numbers, _, -)
        article_ids = set(re.findall(r'A[a-zA-Z0-9_-]{21,22}', text))

        debug(f"Found {len(article_ids)} article IDs")

        return article_ids
    except PermissionError as e:
        debug(f"ERROR: {e}")
        debug("Grant Full Disk Access to the app running this watcher so it can read Apple News data.")
        return None
    except Exception as e:
        debug(f"ERROR: {e}")
        import traceback
        debug(traceback.format_exc())
        return set()


def article_id_to_url(article_id: str) -> str:
    """Convert Apple News article ID to a web URL."""
    # Article IDs already start with 'A', so just use them directly
    return f"https://apple.news/{article_id}"


def resolve_apple_news_url(url: str) -> str:
    """Get the real publisher URL from an Apple News page.

    Apple News pages contain the publisher URL in the HTML.
    We look for https://www.* URLs that aren't from Apple.
    """
    import requests

    try:
        response = requests.get(url, timeout=10, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
        })
        response.raise_for_status()

        # Look for publisher URLs (https://www.* but not apple)
        urls = re.findall(r'https://www\.[^\s"<>]+', response.text)
        for found_url in urls:
            if "apple" not in found_url.lower():
                # Clean up the URL (remove trailing quotes, etc.)
                clean_url = found_url.rstrip('/"\'')
                return clean_url

        return url
    except Exception as e:
        print(f"Error resolving URL: {e}")
        return url


def fetch_and_clean_article(url: str) -> dict:
    """Fetch article and extract clean content with images."""
    import requests
    from bs4 import BeautifulSoup
    from readability import Document
    from urllib.parse import urljoin

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    }

    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
    except Exception as e:
        raise Exception(f"Failed to fetch article: {e}")

    original_soup = BeautifulSoup(response.text, "lxml")

    doc = Document(response.text)
    title = doc.title()
    html_content = doc.summary()

    # Parse readability output
    soup = BeautifulSoup(html_content, "lxml")

    # Readability often strips images - let's find them in the original article
    # and inject them back into our cleaned content
    all_image_urls = []

    # Get og:image first (usually the hero image)
    og_image = original_soup.find("meta", {"property": "og:image"})
    if og_image and og_image.get("content"):
        all_image_urls.append(og_image.get("content"))

    # Find images in the original article body
    # Look for common article containers
    article_containers = original_soup.find_all(["article", "main", "div"],
        class_=lambda x: x and any(c in str(x).lower() for c in ["article", "content", "post", "story", "entry"]))

    for container in article_containers:
        for img in container.find_all("img"):
            src = img.get("src") or img.get("data-src") or img.get("data-lazy-src")
            if src:
                absolute_src = urljoin(url, src)
                # Filter out tiny images, icons, avatars
                if not any(x in absolute_src.lower() for x in ["avatar", "icon", "logo", "badge", "emoji", "1x1"]):
                    if absolute_src not in all_image_urls:
                        all_image_urls.append(absolute_src)

    # Limit to reasonable number
    all_image_urls = all_image_urls[:10]

    # Get text content
    text_content = soup.get_text(separator="\n", strip=True)
    paragraphs = [p.strip() for p in text_content.split("\n") if p.strip() and len(p.strip()) > 30]
    summary = text_content[:500] + "..." if len(text_content) > 500 else text_content

    # Build HTML with images distributed throughout
    html_parts = [f"<article><h1>{title}</h1>"]

    if all_image_urls:
        # Add first image at top
        html_parts.append(f'<img src="{all_image_urls[0]}" style="max-width:100%">')
        remaining_images = all_image_urls[1:]

        # Distribute remaining images through paragraphs
        if remaining_images and paragraphs:
            interval = max(1, len(paragraphs) // (len(remaining_images) + 1))
            img_idx = 0
            for i, para in enumerate(paragraphs):
                html_parts.append(f"<p>{para}</p>")
                if img_idx < len(remaining_images) and (i + 1) % interval == 0:
                    html_parts.append(f'<img src="{remaining_images[img_idx]}" style="max-width:100%">')
                    img_idx += 1
        else:
            for para in paragraphs:
                html_parts.append(f"<p>{para}</p>")
    else:
        for para in paragraphs:
            html_parts.append(f"<p>{para}</p>")

    html_parts.append("</article>")
    html_content = "".join(html_parts)

    # Try to extract author
    author = None
    author_meta = original_soup.find("meta", {"name": "author"})
    if author_meta:
        author = author_meta.get("content")

    return {
        "title": title,
        "html": html_content,
        "summary": summary,
        "author": author,
        "url": url,
        "image_url": all_image_urls[0] if all_image_urls else None
    }


def send_to_readwise_reader(article: dict) -> bool:
    """Send article to Readwise Reader."""
    import requests

    token = get_readwise_token()
    if not token:
        watcher_log(
            "   ❌ No Readwise token found (checked Keychain 'readwise-token', "
            "$READWISE_TOKEN, and .env) — cannot save. Fix: "
            'security add-generic-password -s readwise-token -a "$USER" -w YOUR_TOKEN'
        )
        return False

    url = "https://readwise.io/api/v3/save/"
    headers = {
        "Authorization": f"Token {token}",
        "Content-Type": "application/json"
    }

    payload = {
        "url": article["url"],
        "html": article.get("html"),
        "title": article.get("title"),
        "summary": article.get("summary"),
        "author": article.get("author"),
        "image_url": article.get("image_url"),
        "saved_using": "Apple News Like Watcher"
    }
    payload = {k: v for k, v in payload.items() if v is not None}

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=15)
        if response.status_code in (200, 201):
            return True
        if response.status_code == 401:
            # Token rejected: drop it so the next attempt re-resolves and picks
            # up a corrected Keychain entry / .env without a watcher restart.
            _SECRET_CACHE.pop("readwise-token", None)
            watcher_log(
                "   ❌ Readwise rejected the token (401). Update it, e.g.: "
                'security add-generic-password -U -s readwise-token -a "$USER" -w NEW_TOKEN'
            )
            return False
        watcher_log(f"Readwise API error: {response.status_code} {response.text[:300]}")
        return False
    except Exception as e:
        watcher_log(f"Error sending to Readwise: {e}")
        return False


def show_notification(title: str, message: str):
    """Show macOS notification."""
    def esc(s: str) -> str:
        # Escape for AppleScript string literal: backslash first, then quotes.
        return s.replace("\\", "\\\\").replace('"', '\\"')

    subprocess.run([
        "osascript", "-e",
        f'display notification "{esc(message)}" with title "{esc(title)}"'
    ], capture_output=True)


def get_news_window_title() -> str:
    """Return the current News window title when available."""
    result = subprocess.run(["osascript", "-e", '''
    tell application "System Events"
        tell process "News"
            return name of window 1
        end tell
    end tell
    '''], capture_output=True, text=True)
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def build_url_only_article(url: str) -> dict:
    """Build a minimal Readwise payload that saves the resolved webpage URL."""
    window_title = get_news_window_title()
    title = window_title
    author = None

    if " - " in window_title:
        parts = window_title.rsplit(" - ", 1)
        title = parts[0]
        author = parts[1] if len(parts) > 1 else None

    return {
        "url": url,
        "title": title or url,
        "author": author,
        "summary": "Saved from Apple News via publisher URL handoff.",
    }


def get_frontmost_app_name() -> str:
    """Return the current frontmost application name."""
    result = subprocess.run(["osascript", "-e", '''
    tell application "System Events"
        return name of first application process whose frontmost is true
    end tell
    '''], capture_output=True, text=True)
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def is_news_frontmost() -> bool:
    """Return True when News is already the frontmost app."""
    result = subprocess.run(["osascript", "-e", '''
    tell application "News"
        if it is running then
            return frontmost
        end if
        return false
    end tell
    '''], capture_output=True, text=True)
    if result.stdout.strip().lower() == "true":
        return True
    return get_frontmost_app_name() == "News"


def run_osascript(script: str, timeout: float = 4.0) -> subprocess.CompletedProcess | None:
    """Run AppleScript with a timeout so the watcher cannot wedge on UI automation."""
    try:
        return subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        watcher_log("   ⚠️  AppleScript timed out")
        return None


def get_news_window_bounds() -> tuple | None:
    """Get the News window position and size."""
    script = '''
    tell application "System Events"
        tell process "News"
            set winPos to position of window 1
            set winSize to size of window 1
            return (item 1 of winPos) & "|" & (item 2 of winPos) & "|" & (item 1 of winSize) & "|" & (item 2 of winSize)
        end tell
    end tell
    '''
    result = run_osascript(script, timeout=3.0)
    if result is None:
        return None
    if result.returncode != 0:
        return None
    try:
        # Parse the output - AppleScript returns numbers with ", ," separators sometimes
        output = result.stdout.strip().replace(" ", "").replace(",", "")
        parts = output.split("|")
        x, y, w, h = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
        return (x, y, w, h)
    except Exception as e:
        print(f"   Error parsing window bounds: {e}")
        return None


def focus_news_article_body(offset_x: int | None = None, offset_y: int | None = None) -> bool:
    """Focus the article content area so Edit > Select All targets the text view."""
    bounds = get_news_window_bounds()
    if not bounds:
        return False

    x, y, w, h = bounds
    min_offset_x = min(max(120, int(w * 0.35)), max(120, w - 120))
    max_offset_x = max(min_offset_x, w - 120)
    min_offset_y = min(max(140, int(h * 0.22)), max(120, h - 120))
    max_offset_y = max(min_offset_y, h - 120)

    click_x = x + (offset_x if offset_x is not None else min(max(int(w * 0.58), min_offset_x), max_offset_x))
    click_y = y + (offset_y if offset_y is not None else min(max(int(h * 0.60), min_offset_y), max_offset_y))

    script = f'''
    tell application "System Events"
        tell process "News"
            set frontmost to true
        end tell
        click at {{{click_x}, {click_y}}}
        delay 0.2
    end tell
    '''
    result = run_osascript(script, timeout=3.0)
    if result is None:
        return False
    return result.returncode == 0


def get_news_article_focus_points() -> list[tuple[int, int]]:
    """Return conservative offsets inside the likely text column."""
    bounds = get_news_window_bounds()
    if not bounds:
        return []

    _, _, w, h = bounds
    x_offset = min(max(int(w * 0.66), 320), max(320, w - 160))
    y_offset = min(max(int(h * 0.62), 340), max(340, h - 180))
    return [(x_offset, y_offset)]


def select_all_and_copy_in_news() -> bool:
    """Run Edit > Select All and Edit > Copy in News."""
    result = run_osascript('''
        tell application "System Events"
            tell process "News"
                set frontmost to true
                try
                    click menu item "Select All" of menu "Edit" of menu bar 1
                    delay 0.3
                end try
                click menu item "Copy" of menu "Edit" of menu bar 1
                delay 0.5
            end tell
        end tell
    ''', timeout=3.0)
    if result is None:
        return False
    return result.returncode == 0


def shortcut_select_all_and_copy_in_news() -> bool:
    """Try Cmd+A / Cmd+C directly in News after the article body is focused."""
    result = run_osascript('''
        tell application "System Events"
            tell process "News"
                set frontmost to true
                keystroke "a" using command down
                delay 0.3
                keystroke "c" using command down
                delay 0.5
            end tell
        end tell
    ''', timeout=3.0)
    if result is None:
        return False
    return result.returncode == 0


def capture_article_screenshot() -> str | None:
    """Capture a screenshot of the News window and return the file path."""
    import tempfile

    bounds = get_news_window_bounds()
    if not bounds:
        return None

    x, y, w, h = bounds
    screenshot_path = tempfile.mktemp(suffix=".png")

    try:
        # Capture just the top portion where the main image usually is
        capture_height = int(h * 0.4)

        # Use screencapture to grab the region
        subprocess.run([
            "screencapture", "-R", f"{x},{y},{w},{capture_height}", "-x", screenshot_path
        ], capture_output=True)

        if os.path.exists(screenshot_path):
            return screenshot_path
    except Exception as e:
        print(f"   Screenshot error: {e}")

    return None


def capture_full_article_screenshots(max_screenshots: int = 6) -> list:
    """Capture hero image from top of article.

    Returns list of screenshot file paths (just the hero image for now).
    """
    import tempfile

    bounds = get_news_window_bounds()
    if not bounds:
        return []

    x, y, w, h = bounds
    screenshots = []

    # Activate News (no scrolling)
    subprocess.run(["osascript", "-e", '''
        tell application "News" to activate
        delay 0.3
    '''], capture_output=True)

    # Capture just the hero image area (top 40% of article, excluding toolbar)
    toolbar_offset = 80
    hero_height = int((h - toolbar_offset) * 0.4)

    screenshot_path = tempfile.mktemp(suffix="_hero.png")
    subprocess.run([
        "screencapture", "-R", f"{x},{y + toolbar_offset},{w},{hero_height}", "-x", screenshot_path
    ], capture_output=True)

    if os.path.exists(screenshot_path) and os.path.getsize(screenshot_path) > 5000:
        screenshots.append(screenshot_path)

    return screenshots


def upload_image_to_imgbb(image_path: str) -> str | None:
    """Upload image to imgbb and return the URL."""
    import requests
    import base64

    imgbb_key = get_imgbb_api_key()
    if not imgbb_key:
        # No API key configured, skip image upload
        return None

    try:
        with open(image_path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode()

        response = requests.post(
            "https://api.imgbb.com/1/upload",
            data={
                "key": imgbb_key,
                "image": image_data,
            },
            timeout=30
        )

        if response.status_code == 200:
            data = response.json()
            return data.get("data", {}).get("url")
    except Exception as e:
        print(f"   Image upload error: {e}")
    finally:
        # Clean up temp file
        if os.path.exists(image_path):
            os.remove(image_path)

    return None


def capture_and_upload_article_image() -> str | None:
    """Capture screenshot of article and upload to get a public URL."""
    screenshot_path = capture_article_screenshot()
    if not screenshot_path:
        return None

    image_url = upload_image_to_imgbb(screenshot_path)
    return image_url


def capture_and_upload_all_article_images(max_images: int = 8) -> list:
    """Capture screenshots of entire article and upload all images.

    Returns list of image URLs.
    """
    print("   📷 Capturing article screenshots...")
    screenshots = capture_full_article_screenshots(max_screenshots=max_images)

    if not screenshots:
        return []

    print(f"   📷 Uploading {len(screenshots)} screenshots...")
    image_urls = []
    for i, screenshot_path in enumerate(screenshots):
        url = upload_image_to_imgbb(screenshot_path)
        if url:
            image_urls.append(url)
            print(f"   📷 Uploaded image {i+1}/{len(screenshots)}")

    return image_urls


def scroll_news_to_top():
    """Scroll the frontmost News article to the top."""
    run_osascript('''
    tell application "System Events"
        tell process "News"
            set frontmost to true
            key code 115
        end tell
    end tell
    ''', timeout=2.0)


def scroll_news_page_down():
    """Page down inside the frontmost News article."""
    run_osascript('''
    tell application "System Events"
        tell process "News"
            set frontmost to true
            key code 121
        end tell
    end tell
    ''', timeout=2.0)


def advance_past_hero_section(page_downs: int = 1):
    """Move just enough to get below the hero image before attempting a body click."""
    for _ in range(page_downs):
        scroll_news_page_down()
        time.sleep(0.25)


def get_clipboard_html() -> str | None:
    """Get HTML content from clipboard if available."""
    # Use osascript to get the clipboard as HTML
    script = '''
    use framework "AppKit"
    set pb to current application's NSPasteboard's generalPasteboard()
    set htmlType to current application's NSPasteboardTypeHTML
    set htmlData to pb's dataForType:htmlType
    if htmlData is missing value then
        return ""
    end if
    set htmlString to current application's NSString's alloc()'s initWithData:htmlData encoding:(current application's NSUTF8StringEncoding)
    return htmlString as text
    '''
    try:
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=5)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return None


def get_clipboard_text() -> str:
    """Return plain text from the clipboard."""
    result = subprocess.run(["pbpaste"], capture_output=True, text=True)
    return result.stdout.strip()


def wait_for_clipboard_payload(timeout: float = 2.0, interval: float = 0.1) -> tuple[str, str | None]:
    """Poll the clipboard briefly because Apple News copy is not immediate."""
    deadline = time.time() + timeout
    last_text = ""
    last_html = None

    while time.time() < deadline:
        html_content = get_clipboard_html()
        text = get_clipboard_text()
        if text or html_content:
            return text, html_content
        last_text = text
        last_html = html_content
        time.sleep(interval)

    return last_text, last_html


def extract_images_from_html(html: str) -> list:
    """Extract image URLs from HTML content."""
    from bs4 import BeautifulSoup

    if not html:
        return []

    soup = BeautifulSoup(html, "lxml")
    image_urls = []

    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src")
        if src and src.startswith(("http://", "https://")):
            # Filter out tiny images, icons, avatars
            if not any(x in src.lower() for x in ["avatar", "icon", "logo", "badge", "emoji", "1x1", "pixel", "tracking"]):
                if src not in image_urls:
                    image_urls.append(src)

    return image_urls[:10]  # Limit to 10 images


def get_article_via_clipboard() -> tuple[str, list]:
    """Get article text using automated selection.

    Returns tuple of (text, image_urls).

    IMPORTANT: In Apple News, keyboard shortcuts (Cmd+C, Cmd+A) don't work via
    AppleScript key codes, but the Edit menu items DO work. We use:
    - Edit > Select All (requires text focus first)
    - Edit > Copy (instead of Cmd+C)

    Apple News often ignores Select All unless the article body is focused first,
    so we click into a few candidate positions in the content pane and retry.
    """
    last_text = ""
    last_images = []

    focus_points = get_news_article_focus_points() or [(None, None)]
    for index, (offset_x, offset_y) in enumerate(focus_points):
        subprocess.run(["osascript", "-e", 'set the clipboard to ""'], capture_output=True)
        if index == 0:
            watcher_log("   Paging down once to get below the hero image...")
            advance_past_hero_section(page_downs=1)
        focus_news_article_body(offset_x=offset_x, offset_y=offset_y)
        watcher_log(f"   Focusing article body at offsets x={offset_x}, y={offset_y}")
        select_all_and_copy_in_news()
        text, html_content = wait_for_clipboard_payload()
        image_urls = extract_images_from_html(html_content) if html_content else []
        watcher_log(f"   Focused clipboard capture length: {len(text)} chars")

        if len(text) < 100:
            subprocess.run(["osascript", "-e", 'set the clipboard to ""'], capture_output=True)
            watcher_log("   Trying focused Cmd+A / Cmd+C in News...")
            shortcut_select_all_and_copy_in_news()
            text, html_content = wait_for_clipboard_payload()
            image_urls = extract_images_from_html(html_content) if html_content else []
            watcher_log(f"   Focused keyboard clipboard length: {len(text)} chars")

        last_text = text
        last_images = image_urls
        if len(text) >= 100:
            return text, image_urls

    scroll_news_to_top()
    return last_text, last_images


def get_article_via_menu_copy() -> str:
    """Try Edit > Select All / Copy from the current News view."""
    subprocess.run(["osascript", "-e", 'set the clipboard to ""'], capture_output=True)
    watcher_log("   Trying Edit > Select All / Copy in News...")
    if not select_all_and_copy_in_news():
        watcher_log("   ❌ Could not trigger Edit > Select All / Copy")
    text, _ = wait_for_clipboard_payload(timeout=2.5)
    watcher_log(f"   Clipboard capture length: {len(text)} chars")
    if len(text) < 100:
        subprocess.run(["osascript", "-e", 'set the clipboard to ""'], capture_output=True)
        watcher_log("   Trying Cmd+A / Cmd+C in News...")
        shortcut_select_all_and_copy_in_news()
        text, _ = wait_for_clipboard_payload(timeout=2.5)
        watcher_log(f"   Keyboard clipboard length: {len(text)} chars")
    return text


def paragraphs_from_copied_text(copied_text: str) -> list[str]:
    """Split copied clipboard text into likely article paragraphs."""
    raw_paragraphs = []
    for chunk in copied_text.split("\n\n"):
        chunk = chunk.strip()
        if not chunk:
            continue
        if len(chunk) > 500 and "\n" in chunk:
            for line in chunk.split("\n"):
                line = line.strip()
                if line and len(line) > 30:
                    raw_paragraphs.append(line)
        elif len(chunk) > 20:
            raw_paragraphs.append(chunk)

    if len(raw_paragraphs) < 3 and "\n" in copied_text:
        raw_paragraphs = [p.strip() for p in copied_text.split("\n") if p.strip() and len(p.strip()) > 30]

    return raw_paragraphs


def get_article_from_news_window() -> dict:
    """Extract article content from the Apple News window.

    This path is intentionally immediate and aggressive: try copy from the
    current view first, then focus the article body and try again. It does not
    do slow scrolling retries.
    """
    # Get window title (contains article title)
    window_title = get_news_window_title()

    # Parse title - format is "Article Title - Publisher"
    if " - " in window_title:
        parts = window_title.rsplit(" - ", 1)
        title = parts[0]
        publisher = parts[1] if len(parts) > 1 else None
    else:
        title = window_title
        publisher = None

    copied_text = get_article_via_menu_copy()
    content_paragraphs = paragraphs_from_copied_text(copied_text)
    full_text = copied_text.strip()

    if len(full_text) < 100:
        watcher_log("   Copy was too short; trying focused Select All / Copy...")
        focused_text, _ = get_article_via_clipboard()
        if focused_text and len(focused_text) > len(full_text):
            content_paragraphs = paragraphs_from_copied_text(focused_text)
            full_text = focused_text.strip()

    # Create summary from first ~500 chars
    summary = full_text[:500] + "..." if len(full_text) > 500 else full_text

    # Create a unique fake URL based on title hash
    title_hash = hashlib.md5(title.encode(), usedforsecurity=False).hexdigest()[:12]
    fake_url = f"https://applenews.local/{title_hash}"

    html_content = f"<article><h1>{title}</h1>{''.join(f'<p>{p}</p>' for p in content_paragraphs)}</article>"

    return {
        "title": title,
        "author": publisher,
        "text": full_text,
        "summary": summary,
        "html": html_content,
        "paragraphs": content_paragraphs,
        "url": fake_url,
        "image_url": None,
        "image_urls": []
    }


# Retry policy for failed article processing (within a single News session).
MAX_PROCESS_ATTEMPTS = 3          # attempts before falling back to a URL-only save
RETRY_COOLDOWN_SECONDS = 30.0     # minimum gap between attempts for the same article


def save_url_only_last_resort(article_id: str) -> bool:
    """Last-resort save so a failed article is never silently lost.

    Saves the Apple News URL to Readwise with whatever title we can read from the
    window. The apple.news link always resolves, so the article is at least
    recoverable from Readwise even when full extraction failed.
    """
    apple_url = article_id_to_url(article_id)
    article = build_url_only_article(apple_url)
    article["summary"] = "Saved from Apple News (full extraction failed; link only)."
    if send_to_readwise_reader(article):
        watcher_log(f"   ✅ Last-resort URL-only save succeeded for {article_id}")
        return True
    watcher_log(f"   ❌ Last-resort URL-only save also failed for {article_id}")
    return False


def _send_url_only(real_url: str, failure_message: str) -> bool:
    """Save a URL-only payload for `real_url` to Readwise. False on send failure."""
    article = build_url_only_article(real_url)
    if send_to_readwise_reader(article):
        watcher_log("   ✅ Sent URL to Readwise!")
        show_notification("Apple News → Readwise", f"Saved: {article['title'][:40]}...")
        return True
    watcher_log(failure_message)
    return False


def _send_cached(cached: dict) -> bool:
    """Send a cache-extracted article to Readwise. False on send failure."""
    watcher_log(f"   ⚡ Found in Apple News cache: {len(cached['text'])} chars (no scrape needed)")
    if send_to_readwise_reader(cached):
        watcher_log("   ✅ Sent cached article to Readwise!")
        show_notification("Apple News → Readwise", f"Saved: {cached['title'][:40]}...")
        return True
    watcher_log("   ❌ Failed to send cached article to Readwise")
    return False


def lookup_cached_article(real_url: str) -> dict | None:
    """Return the article body from the on-disk cache by publisher URL, if present.

    The fast, robust path: no UI automation, and it uniquely recovers full body
    text for paywalled subscriber content News has already rendered. Returns None
    on any miss/error so the caller falls back to its normal fetch/scrape path.
    """
    if news_cache is None:
        return None
    try:
        return news_cache.lookup_by_url(real_url)
    except Exception as e:  # defensive: cache must never break processing
        watcher_log(f"   ⚠️  Cache lookup error (ignoring): {e}")
        return None


def lookup_cached_article_by_title(fallback_url: str) -> dict | None:
    """Return a cached article matched by the News window title, if unambiguous.

    For Apple News+ exclusives there is no publisher URL to match on, but the full
    ANF doc is usually still cached. We match on the current window title and only
    accept an unambiguous hit. `fallback_url` (the apple.news URL) becomes the
    saved article's URL. Returns None on miss so the caller can screen-scrape.
    """
    if news_cache is None:
        return None
    window_title = get_news_window_title()
    if not window_title:
        return None
    # Window title is "Article Title - Publisher"; match on the title portion.
    title = window_title.rsplit(" - ", 1)[0] if " - " in window_title else window_title
    try:
        cached = news_cache.lookup_by_title(title, fallback_url=fallback_url)
    except Exception as e:  # defensive
        watcher_log(f"   ⚠️  Cache title lookup error (ignoring): {e}")
        return None
    if cached:
        watcher_log(f"   ⚡ Matched News+ exclusive by title: {title[:50]}")
    return cached


def process_new_article(article_id: str) -> bool | None:
    """Process a newly saved article."""
    watcher_log(f"📰 New saved article: {article_id}")

    # Convert to Apple News URL
    apple_url = article_id_to_url(article_id)
    watcher_log(f"   Apple News URL: {apple_url}")

    # Resolve to publisher URL
    try:
        real_url = resolve_apple_news_url(apple_url)
    except Exception as e:
        watcher_log(f"   ❌ Could not resolve URL: {e}")
        real_url = apple_url  # Fall through to screen scraping

    # Check if it's still an Apple News URL (couldn't resolve)
    if "apple.news" in real_url:
        watcher_log("   ⚠️  Apple News+ exclusive - checking cache by title first...")
        # News+ exclusives have no publisher URL, but the full ANF is often cached.
        cached = lookup_cached_article_by_title(fallback_url=real_url)
        if cached:
            if _send_cached(cached):
                return True
            # Extraction succeeded; only the Readwise send failed. Screen
            # scraping would hit the same send failure, so retry later instead.
            watcher_log("   Cache hit but Readwise send failed — will retry")
            return False
        watcher_log("   Cache miss - attempting News copy fallback...")
        return process_article_from_screen()

    watcher_log(f"   Publisher URL: {real_url}")

    # Fast path: if Apple News already cached the full article on disk, use it.
    # This beats both the publisher fetch and the screen-scrape, and uniquely
    # recovers full body text for paywalled subscriber sites.
    cached = lookup_cached_article(real_url)
    if cached:
        if _send_cached(cached):
            return True
        watcher_log("   Cache hit but Readwise send failed — will retry")
        return False

    # Subscribed sites: the publisher fetch would only hit the paywall, so save
    # the URL directly instead.
    is_subscribed_site = any(site in real_url.lower() for site in SUBSCRIBED_SITES)
    if is_subscribed_site:
        watcher_log("   📰 Subscribed or blocked site - saving publisher URL directly to Readwise...")
        return _send_url_only(real_url, "   ❌ Failed to send blocked-site URL to Readwise")

    # Fetch and clean article
    try:
        article = fetch_and_clean_article(real_url)
        watcher_log(f"   Title: {article['title']}")

        # Check if we got real content or paywall garbage
        text = article.get("text") or article.get("summary", "")
        paywall_indicators = [
            "subscription", "subscribe", "sign in", "log in", "paywall",
            "premium content", "members only", "create an account",
            "payment", "upgrade", "terminate"
        ]
        text_lower = text.lower()
        is_paywall = (
            len(text) < 500 or
            any(indicator in text_lower for indicator in paywall_indicators) or
            any(indicator in text_lower for indicator in BLOCK_PAGE_INDICATORS)
        )

        if is_paywall:
            watcher_log("   ⚠️  Detected paywall or block page - saving publisher URL directly to Readwise...")
            return _send_url_only(real_url, "   ❌ Failed to send blocked-site URL to Readwise")

    except Exception as e:
        watcher_log(f"   ❌ Could not fetch article: {e}")
        watcher_log("   Saving publisher URL directly to Readwise instead...")
        return _send_url_only(real_url, "   ❌ Failed to send fallback URL to Readwise")

    # Send to Readwise
    if send_to_readwise_reader(article):
        watcher_log("   ✅ Sent to Readwise!")
        show_notification("Apple News → Readwise", f"Saved: {article['title'][:40]}...")
        return True
    else:
        watcher_log("   ❌ Failed to send to Readwise")
        return False


def process_article_from_screen() -> bool | None:
    """Extract article text from the News app window without screenshots or background retries."""
    # Check if News is running and has a window
    check_script = 'tell application "System Events" to return exists process "News"'
    result = subprocess.run(["osascript", "-e", check_script], capture_output=True, text=True)
    if result.stdout.strip() != "true":
        watcher_log("   ❌ Apple News is not running")
        return False
    if not is_news_frontmost():
        frontmost_app = get_frontmost_app_name() or "unknown"
        watcher_log(f"   ❌ News is not frontmost for immediate copy fallback (currently {frontmost_app})")
        return False

    try:
        watcher_log("   News is frontmost - attempting News copy fallback...")
        article = get_article_from_news_window()
        if not article.get("text") or len(article["text"]) < 100:
            watcher_log("   ❌ Could not extract enough text from News immediate copy fallback")
            return False

        watcher_log(f"   Title: {article['title']}")
        watcher_log(f"   Content: {len(article['text'])} chars (from News text extraction)")

        if send_to_readwise_reader(article):
            watcher_log("   ✅ Sent to Readwise! (via News text extraction)")
            show_notification("Apple News → Readwise", f"Saved: {article['title'][:40]}...")
            return True
        else:
            watcher_log("   ❌ Failed to send to Readwise")
            return False

    except Exception as e:
        watcher_log(f"   ❌ News text extraction failed: {e}")
        return False


def is_news_running() -> bool:
    """Check if Apple News app is currently running."""
    result = subprocess.run(
        ["osascript", "-e", 'tell application "System Events" to return exists process "News"'],
        capture_output=True, text=True
    )
    return result.stdout.strip() == "true"


def watch_for_saves():
    """Main watch loop - monitors for saved articles."""
    debug_log = DEBUG_LOG_FILE

    def log(msg):
        """Write to debug log with flush."""
        timestamp = datetime.now().strftime('%H:%M:%S')
        line = f"[{timestamp}] {msg}"
        print(line)
        sys.stdout.flush()
        with open(debug_log, "a") as f:
            f.write(line + "\n")

    permission_warning_shown = False

    log("🍎 Apple News → Readwise - Starting watcher")

    # Fail loudly, not silently: without a token every save 401s after the
    # article has already been extracted, and the article is eventually dropped.
    if not get_readwise_token():
        log(
            "❌ No Readwise token found — checked Keychain 'readwise-token', "
            "$READWISE_TOKEN, and .env. Saves will fail until this is fixed."
        )
        show_notification(
            "Apple News → Readwise",
            "❌ No Readwise token found — saves will fail",
        )

    seen_articles = load_seen_articles()
    session_known_articles = set()
    pending_articles = set()
    attempt_counts: dict[str, int] = {}      # article_id -> attempts made this session
    last_attempt_at: dict[str, float] = {}   # article_id -> monotonic time of last attempt
    news_opened_at = 0.0
    log(f"Loaded {len(seen_articles)} seen articles from file")

    if READING_LIST_FILE.exists():
        current_articles = extract_article_ids_from_reading_list(debug_log=debug_log)
        if current_articles is None:
            log("Cannot read Apple News reading-list. Grant Full Disk Access to the app that launched this watcher.")
            current_articles = set()
            permission_warning_shown = True
        log(f"Reading list has {len(current_articles)} articles")
        if not seen_articles:
            seen_articles = current_articles
            save_seen_articles(seen_articles)
            log(f"First run - marked {len(seen_articles)} as seen")

    news_was_running = False
    loop_count = 0

    while True:
        try:
            loop_count += 1

            # Exit when News app closes (will be relaunched by the launcher)
            if not is_news_running():
                if news_was_running:
                    log("News closed - exiting (will restart when News opens)")
                    break
                # Wait for News to open on first run
                time.sleep(5)
                continue

            if not news_was_running:
                log("News opened - watching for saves...")
                news_was_running = True
                news_opened_at = time.time()
                # Reload seen articles in case they were updated externally
                seen_articles = load_seen_articles()
                log(f"Reloaded {len(seen_articles)} seen articles")
                if READING_LIST_FILE.exists():
                    baseline_articles = extract_article_ids_from_reading_list(debug_log=debug_log)
                    if baseline_articles is None:
                        log("Cannot read Apple News reading-list. Grant Full Disk Access to Terminal if the watcher was launched there.")
                        permission_warning_shown = True
                        baseline_articles = set()
                    session_known_articles = set(baseline_articles)
                    pending_articles.clear()
                    log(f"Session baseline has {len(session_known_articles)} saved articles")

            if READING_LIST_FILE.exists():
                current_articles = extract_article_ids_from_reading_list(debug_log=debug_log)
                if current_articles is None:
                    if not permission_warning_shown or loop_count % 30 == 0:
                        log("Cannot read Apple News reading-list. Grant Full Disk Access to Terminal if the watcher was launched there.")
                        permission_warning_shown = True
                    time.sleep(2)
                    continue
                permission_warning_shown = False
                session_known_articles.intersection_update(current_articles)
                pending_articles.intersection_update(current_articles)

                startup_sync_articles = set()
                if news_opened_at and (time.time() - news_opened_at) < STARTUP_SETTLE_SECONDS:
                    startup_sync_articles = current_articles - session_known_articles
                    if startup_sync_articles:
                        session_known_articles.update(startup_sync_articles)
                        log(f"Baselining {len(startup_sync_articles)} saved article(s) during startup sync")

                new_articles = current_articles - session_known_articles

                # Log every 30 iterations (~1 min) to show we're alive
                if loop_count % 30 == 0:
                    log(f"Heartbeat: current={len(current_articles)}, seen={len(seen_articles)}, new={len(new_articles)}, pending={len(pending_articles)}")

                if new_articles:
                    log(f"Found {len(new_articles)} new article(s)!")
                    for article_id in new_articles:
                        session_known_articles.add(article_id)
                        pending_articles.add(article_id)

                if pending_articles:
                    now = time.monotonic()
                    processed_any = False
                    for article_id in sorted(pending_articles):
                        # Respect the per-article cooldown so a failing article is
                        # retried on a gentle cadence rather than every loop.
                        last = last_attempt_at.get(article_id)
                        if last is not None and (now - last) < RETRY_COOLDOWN_SECONDS:
                            continue

                        attempt = attempt_counts.get(article_id, 0) + 1
                        attempt_counts[article_id] = attempt
                        last_attempt_at[article_id] = now
                        processed_any = True

                        log(f"Processing: {article_id} (attempt {attempt}/{MAX_PROCESS_ATTEMPTS})")
                        success = process_new_article(article_id)

                        if success is True:
                            seen_articles.add(article_id)
                            pending_articles.discard(article_id)
                            attempt_counts.pop(article_id, None)
                            last_attempt_at.pop(article_id, None)
                            log(f"Done processing {article_id}")
                        elif attempt < MAX_PROCESS_ATTEMPTS:
                            log(f"Processing failed for {article_id}; will retry after {int(RETRY_COOLDOWN_SECONDS)}s")
                        else:
                            # Out of retries: never drop the article silently. Save the
                            # Apple News link as a last resort and surface the failure.
                            log(f"Processing failed for {article_id} after {attempt} attempts; saving URL-only")
                            saved = save_url_only_last_resort(article_id)
                            seen_articles.add(article_id)
                            pending_articles.discard(article_id)
                            attempt_counts.pop(article_id, None)
                            last_attempt_at.pop(article_id, None)
                            if saved:
                                show_notification(
                                    "Apple News → Readwise",
                                    "⚠️ Saved link only (full text failed)",
                                )
                            else:
                                show_notification(
                                    "Apple News → Readwise",
                                    "❌ Failed to save article — check the log",
                                )

                    if processed_any:
                        save_seen_articles(seen_articles)
                        log(f"Saved {len(seen_articles)} seen articles to file")

            time.sleep(2)

        except KeyboardInterrupt:
            log("Stopping watcher...")
            break
        except Exception as e:
            log(f"ERROR: {e}")
            import traceback
            with open(debug_log, "a") as f:
                traceback.print_exc(file=f)
            time.sleep(5)


def main():
    """Main entry point."""
    # Ensure dependencies are installed
    try:
        import requests
        from bs4 import BeautifulSoup
        from readability import Document
    except ImportError:
        print("Installing dependencies...")
        subprocess.check_call([
            sys.executable, "-m", "pip", "install",
            "requests", "beautifulsoup4", "readability-lxml", "lxml",
            "--quiet"
        ])

    watch_for_saves()


if __name__ == "__main__":
    main()
