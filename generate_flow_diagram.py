#!/usr/bin/env python3
"""Generate a PNG diagram of the Apple News -> Readwise flow."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


OUT_PATH = Path(__file__).parent / "apple_news_readwise_flow.png"
WIDTH = 2400
HEIGHT = 1800
BG = "#f6f8fb"
TEXT = "#102033"
LINE = "#30465f"
ARROW = "#5a738f"


def load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Helvetica.ttc",
        "/System/Library/Fonts/Supplemental/Tahoma.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except Exception:
            continue
    return ImageFont.load_default()


TITLE_FONT = load_font(46, bold=True)
SUB_FONT = load_font(22)
BOX_FONT = load_font(24)
SMALL_FONT = load_font(20)


@dataclass
class Box:
    key: str
    text: str
    x: int
    y: int
    w: int
    h: int
    fill: str


BOXES = [
    Box("launch", "LaunchAgent / daemon", 80, 120, 340, 110, "#dfe9ff"),
    Box("open", "News opens", 490, 120, 240, 110, "#dfe9ff"),
    Box("watcher", "Start watcher in hidden Terminal session", 800, 120, 520, 110, "#dfe9ff"),
    Box("baseline", "Watcher baselines current saved articles", 1390, 120, 470, 110, "#dfe9ff"),

    Box("bookmark", "You click bookmark on a new article", 800, 310, 430, 110, "#e7f6e8"),
    Box("reading_list", "Apple News reading-list changes", 1290, 310, 390, 110, "#e7f6e8"),
    Box("detect", "Watcher detects new article ID", 1740, 310, 390, 110, "#e7f6e8"),

    Box("title_cache", "News+ exclusive: cache lookup by window title", 240, 530, 430, 110, "#e7e0ff"),
    Box("resolved", "Publisher URL found?", 830, 530, 320, 110, "#ffe1d6"),
    Box("resolve", "Resolve to publisher URL", 1280, 530, 300, 110, "#fff4d6"),
    Box("apple_url", "Convert article ID to apple.news URL", 1700, 530, 400, 110, "#fff4d6"),

    Box("copy", "News copy fallback (Edit > Select All / Copy)", 240, 760, 430, 110, "#e5f3ff"),
    Box("url_cache", "Cache lookup by publisher URL", 830, 760, 380, 110, "#e7e0ff"),
    Box("subscribed", "Subscribed or blocked site?", 1280, 760, 300, 110, "#ffe1d6"),
    Box("url_send", "Save publisher URL only to Readwise", 1700, 760, 340, 110, "#ffe6ef"),

    Box("fail", "Extraction failed: retry up to 3 times (30s apart), then save link only + notify", 240, 980, 560, 130, "#fde8e8"),
    Box("fetch", "Fetch and clean publisher webpage", 880, 980, 340, 110, "#e5f3ff"),
    Box("usable", "Usable article content?", 1310, 980, 300, 110, "#ffe1d6"),

    Box("full_send", "Send full article to Readwise", 880, 1200, 360, 110, "#e8f7ff"),

    Box("notify", "macOS notification:\nApple News → Readwise", 920, 1400, 520, 120, "#eef0ff"),
    Box("close", "News closes", 80, 1600, 240, 100, "#f1f1f1"),
    Box("exit", "watch_likes.py exits", 400, 1600, 300, 100, "#f1f1f1"),
    Box("idle", "daemon stays idle until News opens again", 780, 1600, 510, 100, "#f1f1f1"),
]


CONNECTIONS = [
    ("launch", "open", ""),
    ("open", "watcher", ""),
    ("watcher", "baseline", ""),
    ("baseline", "bookmark", ""),
    ("bookmark", "reading_list", ""),
    ("reading_list", "detect", ""),
    ("detect", "apple_url", ""),
    ("apple_url", "resolve", ""),
    ("resolve", "resolved", ""),
    ("resolved", "title_cache", "no (News+)"),
    ("resolved", "url_cache", "yes"),
    ("title_cache", "full_send", "hit"),
    ("title_cache", "copy", "miss"),
    ("url_cache", "full_send", "hit"),
    ("url_cache", "subscribed", "miss"),
    ("subscribed", "url_send", "yes"),
    ("subscribed", "fetch", "no"),
    ("fetch", "usable", ""),
    ("usable", "full_send", "yes"),
    ("usable", "url_send", "paywalled / blocked"),
    ("copy", "full_send", "enough text"),
    ("copy", "fail", "too short"),
    ("url_send", "notify", ""),
    ("full_send", "notify", ""),
    ("fail", "notify", ""),
    ("open", "close", ""),
    ("close", "exit", ""),
    ("exit", "idle", ""),
    ("idle", "launch", ""),
]


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    parts = []
    for paragraph in text.split("\n"):
        if not paragraph:
            parts.append("")
            continue
        words = paragraph.split()
        current = words[0]
        for word in words[1:]:
            trial = f"{current} {word}"
            if draw.textbbox((0, 0), trial, font=font)[2] <= max_width:
                current = trial
            else:
                parts.append(current)
                current = word
        parts.append(current)
    return parts


def draw_box(draw: ImageDraw.ImageDraw, box: Box):
    radius = 22
    draw.rounded_rectangle((box.x, box.y, box.x + box.w, box.y + box.h), radius=radius, fill=box.fill, outline=LINE, width=3)
    lines = wrap_text(draw, box.text, BOX_FONT, box.w - 40)
    line_height = draw.textbbox((0, 0), "Ag", font=BOX_FONT)[3] + 6
    total_h = len(lines) * line_height
    start_y = box.y + (box.h - total_h) / 2 - 2
    for idx, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=BOX_FONT)
        tx = box.x + (box.w - (bbox[2] - bbox[0])) / 2
        ty = start_y + idx * line_height
        draw.text((tx, ty), line, fill=TEXT, font=BOX_FONT)


def center_right(box: Box) -> tuple[int, int]:
    return box.x + box.w, box.y + box.h // 2


def center_left(box: Box) -> tuple[int, int]:
    return box.x, box.y + box.h // 2


def center_bottom(box: Box) -> tuple[int, int]:
    return box.x + box.w // 2, box.y + box.h


def center_top(box: Box) -> tuple[int, int]:
    return box.x + box.w // 2, box.y


def draw_arrow(draw: ImageDraw.ImageDraw, start: tuple[int, int], end: tuple[int, int], label: str = ""):
    sx, sy = start
    ex, ey = end

    if abs(ex - sx) > abs(ey - sy):
        mx = (sx + ex) // 2
        points = [(sx, sy), (mx, sy), (mx, ey), (ex, ey)]
    else:
        my = (sy + ey) // 2
        points = [(sx, sy), (sx, my), (ex, my), (ex, ey)]

    draw.line(points, fill=ARROW, width=5)

    head = 12
    if abs(ex - points[-2][0]) >= abs(ey - points[-2][1]):
        if ex >= points[-2][0]:
            draw.polygon([(ex, ey), (ex - head, ey - head // 2), (ex - head, ey + head // 2)], fill=ARROW)
        else:
            draw.polygon([(ex, ey), (ex + head, ey - head // 2), (ex + head, ey + head // 2)], fill=ARROW)
    else:
        if ey >= points[-2][1]:
            draw.polygon([(ex, ey), (ex - head // 2, ey - head), (ex + head // 2, ey - head)], fill=ARROW)
        else:
            draw.polygon([(ex, ey), (ex - head // 2, ey + head), (ex + head // 2, ey + head)], fill=ARROW)

    if label:
        lx = (points[1][0] + points[2][0]) // 2
        ly = (points[1][1] + points[2][1]) // 2 - 26
        pad = 8
        bbox = draw.textbbox((0, 0), label, font=SMALL_FONT)
        draw.rounded_rectangle(
            (lx - (bbox[2] - bbox[0]) / 2 - pad, ly - pad, lx + (bbox[2] - bbox[0]) / 2 + pad, ly + (bbox[3] - bbox[1]) + pad),
            radius=12,
            fill="#ffffff",
            outline="#d0d7e2",
        )
        draw.text((lx - (bbox[2] - bbox[0]) / 2, ly), label, font=SMALL_FONT, fill=TEXT)


def pick_points(src: Box, dst: Box) -> tuple[tuple[int, int], tuple[int, int]]:
    if dst.x >= src.x + src.w:
        return center_right(src), center_left(dst)
    if src.x >= dst.x + dst.w:
        return center_left(src), center_right(dst)
    if dst.y >= src.y + src.h:
        return center_bottom(src), center_top(dst)
    return center_top(src), center_bottom(dst)


def main():
    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)

    draw.text((80, 32), "Apple News -> Readwise Flow", font=TITLE_FONT, fill=TEXT)
    draw.text((80, 86), "Cache-first: article bodies come from the News on-disk cache when available; failures retry 3x, then fall back to a link-only save.", font=SUB_FONT, fill="#46576d")

    box_map = {box.key: box for box in BOXES}
    for box in BOXES:
        draw_box(draw, box)

    for src_key, dst_key, label in CONNECTIONS:
        src = box_map[src_key]
        dst = box_map[dst_key]
        start, end = pick_points(src, dst)
        draw_arrow(draw, start, end, label)

    img.save(OUT_PATH)
    print(OUT_PATH)


if __name__ == "__main__":
    main()
