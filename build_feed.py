#!/usr/bin/env python3
"""Build feed.xml for KOReader from Low Noise's Kobo queue.

Usage: python3 build_feed.py <dir-of-kobo-json-docs> [feed.xml]

Each JSON file is one document from the Low Noise artifact's "kobo" collection:
  {"title", "url", "source", "why", "queuedAt", "status"}
(ArtifactData's out_dir may wrap it as {"data": {...}}; both shapes work.)
"""
import glob
import json
import os
import re
import sys
from datetime import datetime, timezone
from email.utils import format_datetime
from xml.sax.saxutils import escape

MAX_ITEMS = 60

# Always present so the feed never has a single entry (KOReader issue #12953).
STARTERS = [
    {"title": "Twelve Virtues of Rationality (pinned)", "url": "https://www.yudkowsky.net/rational/virtues",
     "source": "Eliezer Yudkowsky, 2006", "why": "Pinned so this feed never has fewer than two entries.",
     "queuedAt": "2026-09-22T00:00:01Z"},
    {"title": "You and Your Research (pinned)", "url": "https://www.cs.virginia.edu/~robins/YouAndYourResearch.html",
     "source": "Richard Hamming, 1986", "why": "Pinned so this feed never has fewer than two entries.",
     "queuedAt": "2026-09-22T00:00:00Z"},
]


def kobo_friendly(url: str) -> str:
    """Point at versions of pages that render without JavaScript."""
    m = re.match(r"https?://(www\.)?lesswrong\.com/(posts/.*)", url)
    if m:
        return "https://www.greaterwrong.com/" + m.group(2)
    m = re.match(r"https?://(www\.)?alignmentforum\.org/(posts/.*)", url)
    if m:
        return "https://www.greaterwrong.com/" + m.group(2)
    m = re.match(r"https?://arxiv\.org/(abs|pdf)/([0-9]{4}\.[0-9]{4,5})(v\d+)?", url)
    if m:
        return f"https://arxiv.org/html/{m.group(2)}"
    return url


def parse_time(s: str) -> datetime:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return datetime(2026, 1, 1, tzinfo=timezone.utc)


def load(dir_):
    items = []
    for f in glob.glob(os.path.join(dir_, "**", "*.json"), recursive=True):
        with open(f) as fh:
            d = json.load(fh)
        d = d.get("data", d) if isinstance(d, dict) else None
        if not d or not str(d.get("url", "")).startswith(("http://", "https://")):
            continue
        items.append(d)
    return items


def build(items):
    seen, rows = set(), []
    for d in sorted(items, key=lambda x: parse_time(x.get("queuedAt", "")), reverse=True):
        link = kobo_friendly(d["url"])
        if link in seen:
            continue
        seen.add(link)
        rows.append({**d, "link": link})
    rows = rows[:MAX_ITEMS]
    for st in STARTERS:
        if st["url"] not in seen:
            rows.append({**st, "link": st["url"]})

    now = format_datetime(datetime.now(timezone.utc))
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0">',
        "<channel>",
        "<title>Low Noise: To Read</title>",
        "<link>https://claude.ai/artifact/5v2H1mSXF712tzV6kdVmGb</link>",
        "<description>Articles sent from Low Noise to Kobo.</description>",
        f"<lastBuildDate>{now}</lastBuildDate>",
    ]
    for r in rows:
        desc = " · ".join(x for x in [r.get("source", ""), r.get("why", "")] if x)
        out += [
            "<item>",
            f"<title>{escape(r.get('title') or r['link'])}</title>",
            f"<link>{escape(r['link'])}</link>",
            f"<guid isPermaLink=\"true\">{escape(r['link'])}</guid>",
            f"<pubDate>{format_datetime(parse_time(r.get('queuedAt', '')))}</pubDate>",
            f"<description>{escape(desc)}</description>",
            "</item>",
        ]
    out += ["</channel>", "</rss>", ""]
    return "\n".join(out), len(rows)


if __name__ == "__main__":
    src = sys.argv[1]
    dst = sys.argv[2] if len(sys.argv) > 2 else "feed.xml"
    xml, n = build(load(src))
    with open(dst, "w") as fh:
        fh.write(xml)
    print(f"wrote {dst} with {n} items")
