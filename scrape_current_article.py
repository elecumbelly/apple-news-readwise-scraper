#!/usr/bin/env python3
"""
Scrape the currently open Apple News article using Accessibility API.

This extracts text directly from the News app window - works for ALL articles
including Apple News+ exclusives that don't have public URLs.
"""

import re
import subprocess
import sys
import time

# Shared lazy token resolution (Keychain -> env var -> .env) with the watcher.
from watch_likes import get_readwise_token
ASSISTIVE_ACCESS_ERROR = "not allowed assistive access"
NEWS_UI_TEXT_PATTERNS = [
    r'^➔$',
    r'^Sign up',
    r'^Subscribe',
    r'^Share$',
    r'^Save$',
    r'^Follow$',
    r'^\d+\s+min read$',
    r'^Advertisement$',
    r'^Open image',
    r'^Back$',
]


def run_osascript(script: str) -> subprocess.CompletedProcess:
    """Run AppleScript and return the completed process."""
    return subprocess.run(["osascript", "-e", script], capture_output=True, text=True)


def has_assistive_access_error(result: subprocess.CompletedProcess) -> bool:
    """Return True when macOS Accessibility blocks System Events automation."""
    return ASSISTIVE_ACCESS_ERROR in (result.stderr or "").lower()


def require_assistive_access():
    """Exit early with a clear message when Accessibility permission is missing."""
    result = run_osascript('tell application "System Events" to return UI elements enabled')
    if has_assistive_access_error(result):
        print("❌ macOS Accessibility access is blocked for this terminal session.")
        print("Grant Accessibility access to your terminal app in:")
        print("   System Settings > Privacy & Security > Accessibility")
        print("Then quit and reopen the terminal and run the script again.")
        sys.exit(1)


def get_news_window_bounds() -> tuple[int, int, int, int] | None:
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
    result = run_osascript(script)
    if result.returncode != 0:
        return None

    try:
        output = result.stdout.strip().replace(" ", "").replace(",", "")
        parts = output.split("|")
        return int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
    except Exception:
        return None


def get_news_article_focus_points() -> list[tuple[int, int]]:
    """Return a few candidate offsets inside the article pane."""
    bounds = get_news_window_bounds()
    if not bounds:
        return []

    _, _, w, h = bounds
    x_offset = min(max(int(w * 0.58), 220), max(220, w - 120))
    return [
        (x_offset, min(max(int(h * 0.58), 300), max(300, h - 160))),
        (x_offset, min(max(int(h * 0.68), 360), max(360, h - 140))),
        (x_offset, min(max(int(h * 0.78), 420), max(420, h - 120))),
    ]


def focus_news_article_body(offset_x: int, offset_y: int) -> bool:
    """Focus the article content area before using Select All."""
    bounds = get_news_window_bounds()
    if not bounds:
        return False

    x, y, _, _ = bounds
    click_x = x + offset_x
    click_y = y + offset_y
    script = f'''
    tell application "News" to activate
    delay 0.4
    tell application "System Events"
        tell process "News" to set frontmost to true
        click at {{{click_x}, {click_y}}}
        delay 0.2
    end tell
    '''
    result = run_osascript(script)
    return result.returncode == 0


def select_all_and_copy_in_news() -> bool:
    """Run Edit > Select All and Edit > Copy in News."""
    result = run_osascript('''
    tell application "News"
        activate
        delay 0.3
    end tell
    tell application "System Events"
        tell process "News"
            try
                click menu item "Select All" of menu "Edit" of menu bar 1
                delay 0.3
            end try
            click menu item "Copy" of menu "Edit" of menu bar 1
            delay 0.5
        end tell
    end tell
    ''')
    return result.returncode == 0


def get_clipboard_text() -> str:
    """Return plain text from the clipboard."""
    result = subprocess.run(["pbpaste"], capture_output=True, text=True)
    return result.stdout.strip()


def wait_for_clipboard_text(timeout: float = 2.0, interval: float = 0.1) -> str:
    """Poll briefly because Apple News does not populate the clipboard immediately."""
    deadline = time.time() + timeout
    last_text = ""
    while time.time() < deadline:
        text = get_clipboard_text()
        if text:
            return text
        last_text = text
        time.sleep(interval)
    return last_text


def is_article_text_candidate(text: str) -> bool:
    """Return True when a string looks like article body text rather than UI text."""
    text = text.strip()
    if len(text) < 20:
        return False
    return not any(re.match(pattern, text, re.IGNORECASE) for pattern in NEWS_UI_TEXT_PATTERNS)


def get_article_via_clipboard() -> str:
    """Get article text by selecting all and copying to clipboard via menu."""
    last_text = ""

    scroll_top = '''
    tell application "System Events"
        tell process "News"
            set frontmost to true
            key code 115
        end tell
    end tell
    '''
    scroll_down = '''
    tell application "System Events"
        tell process "News"
            set frontmost to true
            key code 121
        end tell
    end tell
    '''

    run_osascript(scroll_top)
    time.sleep(0.4)

    for _ in range(7):
        visible_text = [p for p in extract_visible_text() if is_article_text_candidate(p)]
        if len(visible_text) >= 4 or sum(len(text) for text in visible_text) >= 250:
            break
        run_osascript(scroll_down)
        time.sleep(0.25)

    for offset_x, offset_y in get_news_article_focus_points() or [(None, None)]:
        run_osascript('set the clipboard to ""')
        if offset_x is not None and offset_y is not None:
            focus_news_article_body(offset_x, offset_y)
        select_all_and_copy_in_news()
        last_text = wait_for_clipboard_text()
        if len(last_text) >= 100:
            return last_text

    return last_text


def extract_visible_text() -> list:
    """Extract currently visible static text from the News window."""
    text_script = '''
    tell application "System Events"
        tell process "News"
            set allText to ""
            set allElements to entire contents of window 1
            repeat with elem in allElements
                try
                    if class of elem is static text then
                        set elemValue to value of elem
                        if elemValue is not missing value and elemValue is not "" then
                            set allText to allText & elemValue & "

|||PARA|||

"
                        end if
                    end if
                end try
            end repeat
            return allText
        end tell
    end tell
    '''
    result = run_osascript(text_script)
    raw_text = result.stdout.strip()
    return [p.strip() for p in raw_text.split("|||PARA|||") if p.strip()]


def get_clipboard_preview(text: str) -> str:
    """Return a short preview for debugging clipboard capture."""
    normalized = " ".join(text.split())
    if len(normalized) <= 200:
        return normalized
    return normalized[:200] + "..."


def collect_article_by_scrolling() -> list:
    """Scroll through article and collect text at each position."""
    all_paragraphs = []
    seen_text = set()

    # Scroll to top first
    scroll_top = '''
    tell application "System Events"
        tell process "News"
            set frontmost to true
            key code 115  -- Home key
        end tell
    end tell
    '''
    run_osascript(scroll_top)
    time.sleep(0.5)

    # Collect text at top
    for p in extract_visible_text():
        if p not in seen_text:
            seen_text.add(p)
            all_paragraphs.append(p)

    # Scroll down and collect
    scroll_down = '''
    tell application "System Events"
        tell process "News"
            key code 121  -- Page Down
        end tell
    end tell
    '''

    prev_count = 0
    no_new_count = 0

    for i in range(30):  # Max 30 page-downs
        run_osascript(scroll_down)
        time.sleep(0.2)

        for p in extract_visible_text():
            if p not in seen_text:
                seen_text.add(p)
                all_paragraphs.append(p)

        # Stop if no new content for 3 scrolls
        if len(all_paragraphs) == prev_count:
            no_new_count += 1
            if no_new_count >= 3:
                break
        else:
            no_new_count = 0
            prev_count = len(all_paragraphs)

    # Scroll back to top
    run_osascript(scroll_top)

    return all_paragraphs


def get_article_from_news_window() -> dict:
    """Extract article content from the Apple News window using Accessibility API."""

    # Get window title (contains article title)
    title_script = '''
    tell application "System Events"
        tell process "News"
            return name of window 1
        end tell
    end tell
    '''
    result = run_osascript(title_script)
    window_title = result.stdout.strip()

    # Parse title - format is "Article Title - Publisher"
    if " - " in window_title:
        parts = window_title.rsplit(" - ", 1)
        title = parts[0]
        publisher = parts[1] if len(parts) > 1 else None
    else:
        title = window_title
        publisher = None

    # Try clipboard method first (Cmd+A, Cmd+C) - gets full article
    clipboard_text = get_article_via_clipboard()
    print(f"Clipboard capture length: {len(clipboard_text)} characters")
    if clipboard_text:
        print(f"Clipboard preview: {get_clipboard_preview(clipboard_text)}")

    if clipboard_text and len(clipboard_text) > 200:
        # Clipboard worked - use it
        full_text = clipboard_text
        content_paragraphs = [p.strip() for p in clipboard_text.split("\n\n") if p.strip()]
    else:
        # Fall back to scrolling and collecting via accessibility
        print("Clipboard capture was too short; falling back to accessibility text extraction.")
        paragraphs = collect_article_by_scrolling()

        # Filter out UI elements and keep article content
        ui_patterns = [
            r'^➔$',
            r'^Sign up',
            r'^Subscribe',
            r'^Share',
            r'^Save',
            r'^Follow',
            r'^\d+ min read$',
            r'^Advertisement$',
        ]

        content_paragraphs = []
        for p in paragraphs:
            if len(p) < 20:
                continue
            if any(re.match(pattern, p, re.IGNORECASE) for pattern in ui_patterns):
                continue
            content_paragraphs.append(p)

        full_text = "\n\n".join(content_paragraphs)

    # Create summary from first ~500 chars
    summary = full_text[:500] + "..." if len(full_text) > 500 else full_text

    return {
        "title": title,
        "author": publisher,  # Using publisher as author
        "text": full_text,
        "summary": summary,
        "html": f"<article><h1>{title}</h1>{''.join(f'<p>{p}</p>' for p in content_paragraphs)}</article>",
    }


def send_to_readwise_reader(article: dict) -> bool:
    """Send article to Readwise Reader."""
    import requests
    import hashlib

    token = get_readwise_token()
    if not token:
        print("❌ No Readwise token found (checked Keychain 'readwise-token', "
              "$READWISE_TOKEN, and .env) — cannot save.")
        return False

    url = "https://readwise.io/api/v3/save/"
    headers = {
        "Authorization": f"Token {token}",
        "Content-Type": "application/json"
    }

    # Create a unique fake URL based on title hash
    title_hash = hashlib.md5(article['title'].encode(), usedforsecurity=False).hexdigest()[:12]
    fake_url = f"https://applenews.local/{title_hash}"

    payload = {
        "url": fake_url,
        "html": article.get("html"),
        "title": article.get("title"),
        "summary": article.get("summary"),
        "author": article.get("author"),
        "saved_using": "Apple News Screen Scraper"
    }
    payload = {k: v for k, v in payload.items() if v is not None}

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=15)
        if response.status_code not in (200, 201):
            print(f"Readwise response: {response.status_code} - {response.text[:200]}")
        return response.status_code in (200, 201)
    except Exception as e:
        print(f"Error sending to Readwise: {e}")
        return False


def show_notification(title: str, message: str):
    """Show macOS notification."""
    subprocess.run([
        "osascript", "-e",
        f'display notification "{message}" with title "{title}"'
    ], capture_output=True)


def main():
    print("🍎 Apple News Article Scraper (Accessibility API)")
    print("=" * 50)
    require_assistive_access()

    # Check if News is running
    check_script = 'tell application "System Events" to return exists process "News"'
    result = run_osascript(check_script)
    if result.stdout.strip() != "true":
        print("❌ Apple News is not running. Please open an article first.")
        sys.exit(1)

    print("Extracting article from News window...")

    try:
        article = get_article_from_news_window()
        print(f"Title: {article['title']}")
        if article.get('author'):
            print(f"Publisher: {article['author']}")
        print(f"Content length: {len(article['text'])} characters")
        print(f"\nFirst 200 chars:\n{article['text'][:200]}...")
    except Exception as e:
        print(f"❌ Failed to extract article: {e}")
        sys.exit(1)

    if len(article["text"]) < 100:
        print("❌ Extraction produced no usable article text; not sending an empty entry.")
        sys.exit(1)

    # Send to Readwise
    print("\nSending to Readwise Reader...")

    # Need requests for sending
    try:
        import requests
    except ImportError:
        print("Installing requests...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "requests", "--quiet"])
        import requests

    if send_to_readwise_reader(article):
        print("✅ Article saved to Readwise Reader!")
        show_notification("Apple News → Readwise", f"Saved: {article['title'][:40]}...")
    else:
        print("❌ Failed to save to Readwise")
        sys.exit(1)


if __name__ == "__main__":
    main()
