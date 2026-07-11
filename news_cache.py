#!/usr/bin/env python3
"""Read full article bodies straight from the Apple News on-disk cache.

When you view an article in Apple News, the app stores the full Apple News Format
(ANF) document — title, body paragraphs, headings, captions — as JSON in the
shared asset store. For articles News has rendered, this lets us recover the
complete text (including paywalled content the user is subscribed to) WITHOUT
driving the News UI, the clipboard, or screenshots.

The join key is `metadata.canonicalURL` inside each ANF doc, which holds the real
publisher URL. There is no direct mapping from a saved reading-list article ID to
its asset file (different id spaces), so callers look up by the resolved publisher
URL via `lookup_by_url`.

Coverage is partial by nature: only recently-viewed articles are cached, and News
may evict assets at any time. `lookup_by_url` returns None on a miss; the caller
is expected to fall back to its existing fetch/scrape path.

Public API:
    lookup_by_url(url) -> dict | None
        Returns {"title", "html", "text", "summary", "author", "url"} or None.
    lookup_by_title(title, fallback_url="") -> dict | None
        Same, matched by normalized window title — the watcher's path for Apple
        News+ exclusives that have no publisher URL. Refuses ambiguous titles.
    normalize_url(url) -> str / normalize_title(title) -> str
        Canonicalization used on both sides of each match (exported for tests).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

# Location of the Apple News asset store. Guarded at import: if Apple changes the
# container layout (it has before), this simply won't exist and every lookup
# returns None rather than raising.
ASSET_STORE = (
    Path.home()
    / "Library/Containers/com.apple.news/Data/Library/Caches/News/shared-assets-assetstore"
)

# Roles whose `text` is genuine article body content. Allowlist (not denylist):
# the asset store is full of recirculation modules and chrome whose text would
# otherwise pollute the body. Verified against the live cache.
BODY_TEXT_ROLES = {
    "title",
    "intro",
    "body",
    "heading",
    "heading1",
    "heading2",
    "heading3",
    "heading4",
    "heading5",
    "heading6",
    "caption",
    "byline",
    "author",
    "pullquote",
}

# Heading-like roles render as <h2> in the reconstructed HTML; everything else in
# the allowlist renders as <p>.
HEADING_ROLES = {
    "title",
    "heading",
    "heading1",
    "heading2",
    "heading3",
    "heading4",
    "heading5",
    "heading6",
}

# Container roles that hold OTHER articles' content (related-article cards, link
# modules, subscription chrome). We prune these subtrees entirely — descending
# into them would mix unrelated headlines into the body. Verified: an
# `article_link` container nests an `article_title` from a different article.
PRUNE_CONTAINER_ROLES = {
    "article_link",
    "article_thumbnail",
    "link_button",
    "subscription_button",
    "logo",
}


def normalize_url(url: str) -> str:
    """Canonicalize a URL for matching publisher URL against ANF canonicalURL.

    Handles the mismatches that otherwise sink the hit rate: scheme (http/https),
    host case, a leading ``www.``, query strings (Apple adds non-deterministic
    cache-buster params like ``?dbf=...``; publishers add utm tags), fragments,
    and a trailing slash. Both the index and the lookup pass through here so they
    agree by construction.
    """
    if not url:
        return ""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip().lower()

    scheme = "https"  # collapse http/https
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]

    path = (parts.path or "").rstrip("/")  # trailing slash, incl. a lone root "/"

    # Drop query and fragment entirely. For the publishers seen in the cache the
    # path alone uniquely identifies the article.
    return urlunsplit((scheme, host, path, "", ""))


# Punctuation/casing-insensitive title key, for matching an Apple News window
# title against an ANF doc title when there is no publisher URL (News+ exclusives).
_TITLE_STRIP = re.compile(r"[^a-z0-9 ]+")


def normalize_title(title: str) -> str:
    """Normalize an article title for fuzzy-but-safe matching.

    Lowercase, drop punctuation, collapse whitespace. Two titles that normalize
    equal are treated as the same article; ambiguous (colliding) titles are NOT
    matched (see _scan_index), so this only ever resolves an exact-ish title.
    """
    if not title:
        return ""
    # Replace each punctuation run with a space (so "a:b" -> "a b", not "ab"),
    # then collapse whitespace runs to single spaces.
    return " ".join(_TITLE_STRIP.sub(" ", title.lower()).split())


def _iter_body_text(doc: dict):
    """Yield (role, text) for genuine body components, pruning recirculation.

    Recurses through nested containers/sections (body text lives deep), but does
    not descend into containers whose role is in PRUNE_CONTAINER_ROLES.
    """
    def walk(node):
        if isinstance(node, dict):
            role = node.get("role")
            if role in PRUNE_CONTAINER_ROLES:
                return  # prune this whole subtree
            text = node.get("text")
            if role in BODY_TEXT_ROLES and isinstance(text, str) and text.strip():
                yield role, text
            children = node.get("components")
            if isinstance(children, list):
                for child in children:
                    yield from walk(child)
        elif isinstance(node, list):
            for item in node:
                yield from walk(item)

    components = doc.get("components")
    if isinstance(components, list):
        for component in components:
            yield from walk(component)


def _escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


# ANF body text can carry inline formatting markers; collapse runs of whitespace
# but preserve the visible characters. Apple uses real Unicode punctuation already.
_WS = re.compile(r"[ \t ]+")


def _clean(text: str) -> str:
    return _WS.sub(" ", text).strip()


def _doc_metadata(doc: dict) -> dict:
    """The doc's metadata dict, or {} — tolerates a non-dict `metadata` value."""
    md = doc.get("metadata")
    return md if isinstance(md, dict) else {}


def _doc_str(value) -> str:
    """The value if it is a non-empty string, else "" — tolerates junk types."""
    return value if isinstance(value, str) else ""


def _build_article(doc: dict, matched_url: str) -> dict | None:
    """Turn an ANF doc into a Readwise-ready article dict, or None if too thin."""
    md = _doc_metadata(doc)
    title = _doc_str(doc.get("title")) or _doc_str(md.get("title"))

    paragraphs = []
    html_parts = []
    clean_title = _clean(title) if title else None

    for role, raw in _iter_body_text(doc):
        text = _clean(raw)
        if not text:
            continue
        # The doc title usually also appears as a `title` component. We render the
        # title once as the <h1>, so drop any title-role component that repeats it.
        if role == "title" and clean_title and text == clean_title:
            continue
        if role in HEADING_ROLES:
            html_parts.append(f"<h2>{_escape_html(text)}</h2>")
        else:
            html_parts.append(f"<p>{_escape_html(text)}</p>")
        paragraphs.append(text)

    full_text = "\n\n".join(paragraphs).strip()
    # Guard against matching a stub (e.g. an issue table-of-contents with a URL
    # but no real prose). Require a minimum body length.
    if len(full_text) < 400:
        return None

    if not title:
        title = paragraphs[0][:120]

    html = f"<article><h1>{_escape_html(title)}</h1>{''.join(html_parts)}</article>"
    summary = full_text[:500] + ("..." if len(full_text) > 500 else "")

    author = None
    for role, raw in _iter_body_text(doc):
        if role in ("byline", "author"):
            author = _clean(raw)
            break

    # Prefer the doc's own canonical URL; fall back to whatever the caller matched
    # on (e.g. the apple.news URL for a News+ exclusive). Never emit an empty URL —
    # Readwise uses it as the dedup key.
    url = _doc_str(_doc_metadata(doc).get("canonicalURL")) or matched_url or None

    return {
        "title": title,
        "html": html,
        "text": full_text,
        "summary": summary,
        "author": author,
        "url": url,
    }


# Sentinel marking a normalized title shared by 2+ distinct articles. Such titles
# are ambiguous, so we refuse to resolve them rather than risk the wrong article.
_AMBIGUOUS = object()


def _scan_index() -> tuple[dict[str, Path], dict[str, object]]:
    """Build (url_index, title_index) from the asset store in one pass.

    url_index:   {normalized_canonicalURL -> asset Path}
    title_index: {normalized_title -> asset Path | _AMBIGUOUS}

    Tolerant by design: unreadable files, non-JSON blobs (images/fonts), and docs
    without components are skipped silently. A News schema change degrades to
    fewer/zero entries, never an exception.
    """
    url_index: dict[str, Path] = {}
    title_index: dict[str, object] = {}
    if not ASSET_STORE.is_dir():
        return url_index, title_index

    for path in ASSET_STORE.iterdir():
        if not path.is_file():
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if data[:2] != b'{"':  # cheap pre-filter before JSON parse
            continue
        try:
            doc = json.loads(data)
        except (ValueError, UnicodeDecodeError):
            continue
        if not isinstance(doc, dict) or "components" not in doc:
            continue
        md = _doc_metadata(doc)

        url = _doc_str(md.get("canonicalURL"))
        if url:
            norm = normalize_url(url)
            if norm:
                url_index[norm] = path  # last writer wins; fine for our purposes

        title_key = normalize_title(_doc_str(doc.get("title")))
        if title_key:
            existing = title_index.get(title_key)
            if existing is None:
                title_index[title_key] = path
            elif existing is not _AMBIGUOUS and existing != path:
                # A second, different file with the same title -> ambiguous.
                title_index[title_key] = _AMBIGUOUS

    return url_index, title_index


# Module-level in-memory indexes. Lifetime is the process (bounded by the News
# session), so no disk persistence and no TTL. Rebuilt lazily and on a miss.
_URL_INDEX: dict[str, Path] | None = None
_TITLE_INDEX: dict[str, object] | None = None


def _ensure_indexes(force: bool = False) -> None:
    global _URL_INDEX, _TITLE_INDEX
    if _URL_INDEX is None or _TITLE_INDEX is None or force:
        _URL_INDEX, _TITLE_INDEX = _scan_index()


def _reset_for_tests() -> None:
    """Drop the in-memory indexes so the next lookup rescans. Test-only."""
    global _URL_INDEX, _TITLE_INDEX
    _URL_INDEX = None
    _TITLE_INDEX = None


def _read_and_build(path: Path, matched_url: str) -> dict | None:
    """Read an asset file and build an article, or None if unreadable/stub."""
    try:
        doc = json.loads(path.read_bytes())
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return _build_article(doc, matched_url=matched_url)


def lookup_by_url(url: str) -> dict | None:
    """Return a cached article for `url`, or None if not in the News cache.

    Looks up the normalized URL in the in-memory index. On a miss, rescans the
    asset store once (a new article may have been cached since the index was
    built) and retries. ANF docs are immutable once written, so a stale hit can
    only mean the file was evicted — caught when we try to read it.
    """
    if not url:
        return None
    target = normalize_url(url)

    for force in (False, True):  # try cached index, then one fresh rescan
        _ensure_indexes(force=force)
        assert _URL_INDEX is not None
        path = _URL_INDEX.get(target)
        if path is None:
            if force:
                return None
            continue
        article = _read_and_build(path, matched_url=url)
        if article is not None:
            return article
        # Evicted/truncated or a stub with no real body: drop and rescan once.
        _URL_INDEX.pop(target, None)
        if force:
            return None

    return None


def lookup_by_title(title: str, fallback_url: str = "") -> dict | None:
    """Return a cached article whose title matches `title`, or None.

    For Apple News+ exclusives there is no publisher URL to match on, but the
    full ANF doc is still cached. This resolves it by (normalized) title. Refuses
    ambiguous titles shared by more than one article, so it never returns the
    wrong body. `fallback_url` (e.g. the apple.news URL) is used as the article's
    URL when the doc itself carries no canonicalURL. Same rescan-once-on-miss
    behavior as lookup_by_url.
    """
    if not title:
        return None
    target = normalize_title(title)
    if not target:
        return None

    for force in (False, True):
        _ensure_indexes(force=force)
        assert _TITLE_INDEX is not None
        path = _TITLE_INDEX.get(target)
        if path is None or path is _AMBIGUOUS:
            if force:
                return None
            continue
        article = _read_and_build(path, matched_url=fallback_url)
        if article is not None:
            return article
        _TITLE_INDEX.pop(target, None)
        if force:
            return None

    return None
