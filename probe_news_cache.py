#!/usr/bin/env python3
"""Inspect the local Apple News container from a Terminal session with TCC access."""

from __future__ import annotations

import os
from pathlib import Path


NEWS_ROOT = Path.home() / "Library/Containers/com.apple.news/Data/Library/Application Support/com.apple.news/com.apple.news.public-com.apple.news.private-production"
OUTPUT_FILE = Path(__file__).parent / "news_cache_probe.log"
MAX_DEPTH = 4


def safe_stat(path: Path) -> str:
    try:
        if path.is_file():
            return f"{path.stat().st_size} bytes"
        return "dir"
    except Exception as exc:  # pragma: no cover - diagnostic helper
        return f"stat failed: {exc}"


def depth_for(path: Path) -> int:
    return len(path.relative_to(NEWS_ROOT).parts)


def main() -> int:
    lines: list[str] = []
    lines.append(f"root={NEWS_ROOT}")
    lines.append(f"exists={NEWS_ROOT.exists()}")

    if not NEWS_ROOT.exists():
        OUTPUT_FILE.write_text("\n".join(lines) + "\n")
        return 1

    for root, dirs, files in os.walk(NEWS_ROOT):
        root_path = Path(root)
        depth = depth_for(root_path)
        if depth > MAX_DEPTH:
            dirs[:] = []
            continue

        rel_root = "." if root_path == NEWS_ROOT else str(root_path.relative_to(NEWS_ROOT))
        lines.append(f"[dir] {rel_root}")

        for name in sorted(dirs):
            path = root_path / name
            lines.append(f"  [child-dir] {path.relative_to(NEWS_ROOT)}")

        for name in sorted(files):
            path = root_path / name
            lines.append(f"  [file] {path.relative_to(NEWS_ROOT)} :: {safe_stat(path)}")

    OUTPUT_FILE.write_text("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
