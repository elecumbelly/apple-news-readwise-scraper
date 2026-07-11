#!/usr/bin/env python3
"""
Apple News to Readwise Reader Scraper

This script:
1. Gets the current article URL from Apple News (via "Open in Safari")
2. Fetches and cleans the article content
3. Sends it to Readwise Reader

Usage:
    python scrape_to_readwise.py

Or run via the companion AppleScript/Automator workflow.
"""

import subprocess
import sys
import time

# Shared lazy token resolution (Keychain -> env var -> .env) with the watcher.
from watch_likes import get_readwise_token

# Check and install dependencies
def ensure_dependencies():
    """Ensure required packages are installed."""
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
        import requests
        from bs4 import BeautifulSoup
        from readability import Document
    return True

def run_applescript(script: str) -> str:
    """Run AppleScript and return output."""
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True
    )
    return result.stdout.strip()

def show_notification(title: str, message: str):
    """Show macOS notification."""
    script = f'''
    display notification "{message}" with title "{title}"
    '''
    run_applescript(script)

def get_url_from_apple_news() -> str:
    """
    Get the article URL by triggering "Open in Safari" from Apple News,
    then grabbing the URL from Safari.
    """
    # First, make sure Apple News is frontmost
    run_applescript('tell application "News" to activate')
    time.sleep(0.3)

    # Click "Open in Safari" from File menu
    script = '''
    tell application "System Events"
        tell process "News"
            click menu item "Open in Safari" of menu "File" of menu bar 1
        end tell
    end tell
    '''
    run_applescript(script)

    # Wait for Safari to open and load
    time.sleep(1.5)

    # Get the URL from Safari
    url = run_applescript('tell application "Safari" to get URL of front document')

    return url

def get_url_from_clipboard() -> str:
    """Get URL from clipboard as fallback."""
    result = subprocess.run(["pbpaste"], capture_output=True, text=True)
    text = result.stdout.strip()

    # Check if it looks like a URL
    if text.startswith("http://") or text.startswith("https://"):
        return text

    # Check for apple.news:// links
    if text.startswith("apple.news://"):
        # Convert to web URL
        return text.replace("apple.news://", "https://apple.news/")

    return None

def resolve_apple_news_url(url: str) -> str:
    """
    Resolve Apple News redirect URLs to the actual article URL.
    """
    import requests

    if "apple.news" in url:
        try:
            # Follow redirects to get the real URL
            response = requests.head(url, allow_redirects=True, timeout=10)
            return response.url
        except Exception as e:
            print(f"Error resolving URL: {e}")
            return url

    return url

def fetch_and_clean_article(url: str) -> dict:
    """
    Fetch an article and extract clean reader-view content.
    """
    import requests
    from bs4 import BeautifulSoup
    from readability import Document

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
    except Exception as e:
        raise Exception(f"Failed to fetch article: {e}")

    # Use readability to extract main content
    doc = Document(response.text)
    title = doc.title()
    html_content = doc.summary()

    # Clean up with BeautifulSoup
    soup = BeautifulSoup(html_content, "lxml")

    # Extract text for summary
    text_content = soup.get_text(separator="\n", strip=True)

    # Get first 500 chars for summary
    summary = text_content[:500] + "..." if len(text_content) > 500 else text_content

    # Try to extract author
    author = None
    original_soup = BeautifulSoup(response.text, "lxml")

    # Common author meta tags
    author_meta = original_soup.find("meta", {"name": "author"})
    if author_meta:
        author = author_meta.get("content")

    if not author:
        author_meta = original_soup.find("meta", {"property": "article:author"})
        if author_meta:
            author = author_meta.get("content")

    return {
        "title": title,
        "html": html_content,
        "summary": summary,
        "author": author,
        "url": url
    }

def send_to_readwise_reader(article: dict) -> bool:
    """
    Send article to Readwise Reader using their API.

    API Docs: https://readwise.io/reader_api
    """
    import requests

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

    payload = {
        "url": article["url"],
        "html": article.get("html"),
        "title": article.get("title"),
        "summary": article.get("summary"),
        "author": article.get("author"),
        "saved_using": "Apple News Scraper"
    }

    # Remove None values
    payload = {k: v for k, v in payload.items() if v is not None}

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=15)

        if response.status_code in (200, 201):
            return True
        else:
            print(f"Readwise API error: {response.status_code}")
            print(response.text)
            return False

    except Exception as e:
        print(f"Error sending to Readwise: {e}")
        return False

def main():
    """Main function."""
    ensure_dependencies()

    print("🍎 Apple News to Readwise Scraper")
    print("-" * 40)

    # Try to get URL from Apple News
    print("Getting article URL from Apple News...")

    try:
        url = get_url_from_apple_news()
    except Exception as e:
        print(f"Could not get URL from Apple News: {e}")
        print("Trying clipboard...")
        url = get_url_from_clipboard()

    if not url:
        print("❌ No URL found. Make sure you have an article open in Apple News.")
        show_notification("Apple News Scraper", "No article URL found")
        sys.exit(1)

    print(f"Found URL: {url}")

    # Resolve Apple News redirect URLs
    if "apple.news" in url:
        print("Resolving Apple News redirect...")
        url = resolve_apple_news_url(url)
        print(f"Resolved to: {url}")

    # Fetch and clean the article
    print("Fetching article content...")
    try:
        article = fetch_and_clean_article(url)
        print(f"Title: {article['title']}")
        if article.get('author'):
            print(f"Author: {article['author']}")
    except Exception as e:
        print(f"❌ Failed to fetch article: {e}")
        show_notification("Apple News Scraper", f"Failed to fetch: {e}")
        sys.exit(1)

    # Send to Readwise
    print("Sending to Readwise Reader...")
    success = send_to_readwise_reader(article)

    if success:
        print("✅ Article saved to Readwise Reader!")
        show_notification("Apple News Scraper", f"Saved: {article['title'][:50]}...")
    else:
        print("❌ Failed to save to Readwise")
        show_notification("Apple News Scraper", "Failed to save to Readwise")
        sys.exit(1)

if __name__ == "__main__":
    main()
