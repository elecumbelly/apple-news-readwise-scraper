#!/usr/bin/env python3
"""Search the local Apple News cache for a string from a Terminal session with TCC access."""

from __future__ import annotations

import sys
from pathlib import Path


NEWS_ROOT = Path.home() / "Library/Containers/com.apple.news/Data/Library/Application Support/com.apple.news/com.apple.news.public-com.apple.news.private-production"
OUTPUT_FILE = Path(__file__).parent / "news_cache_search.log"


def search_bytes(path: Path, needle: bytes) -> tuple[int, str] | None:
    try:
        data = path.read_bytes()
    except Exception:
        return None

    idx = data.lower().find(needle.lower())
    if idx == -1:
        return None

    start = max(0, idx - 120)
    end = min(len(data), idx + len(needle) + 220)
    snippet = data[start:end].decode("utf-8", errors="ignore").replace("\x00", " ")
    return idx, " ".join(snippet.split())


def main() -> int:
    if len(sys.argv) < 2:
        OUTPUT_FILE.write_text("usage: search_news_cache.py <needle>\n")
        return 2

    needle = sys.argv[1].encode("utf-8")
    lines = [f"needle={sys.argv[1]}"]

    if not NEWS_ROOT.exists():
        lines.append("root missing")
        OUTPUT_FILE.write_text("\n".join(lines) + "\n")
        return 1

    matches = 0
    for path in sorted(NEWS_ROOT.rglob("*")):
        if not path.is_file():
            continue
        hit = search_bytes(path, needle)
        if not hit:
            continue
        matches += 1
        idx, snippet = hit
        lines.append(f"[match] {path.relative_to(NEWS_ROOT)} @ {idx}")
        lines.append(f"  {snippet}")
        if matches >= 40:
            break

    if matches == 0:
        lines.append("no matches")

    OUTPUT_FILE.write_text("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
