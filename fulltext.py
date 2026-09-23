#!/usr/bin/env python3
"""Turn feed.xml (links only) into kobo.xml (links + full article text).

Runs in GitHub Actions after every send. The send page only ever edits
feed.xml; this script only writes kobo.xml and articles/, so the two never
fight over the same file.

For each item in feed.xml:
  - fetch the page from GitHub's servers with a normal browser identity,
  - pull out the article body (reader-mode style) with trafilatura,
  - cache it in articles/<id>.json so each link is fetched only once,
  - write it into kobo.xml as <content:encoded><![CDATA[ ... ]]>.
KOReader (with "Download full article" OFF) reads that text directly, so the
Kobo never has to contact the article's website.
"""
import hashlib
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import format_datetime
from html import escape

import requests
import trafilatura

INBOX = "feed.xml"
OUT = "kobo.xml"
CACHE = "articles"
MAX_TRIES = 3            # give up on a link after this many failed runs
RETRY_AFTER = 30 * 60    # seconds between retries of a failed link
MAX_CHARS = 400_000      # keep the feed a sane size

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
           "Accept-Language": "en-US,en;q=0.9"}


def item_id(link: str) -> str:
    return hashlib.sha1(link.encode()).hexdigest()[:16]


def load_cache(i):
    p = os.path.join(CACHE, i + ".json")
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return None


def save_cache(i, data):
    os.makedirs(CACHE, exist_ok=True)
    with open(os.path.join(CACHE, i + ".json"), "w") as f:
        json.dump(data, f, ensure_ascii=False)


def body_only(html: str) -> str:
    m = re.search(r"<body[^>]*>(.*)</body>", html, re.S | re.I)
    return (m.group(1) if m else html).strip()


def extract(link: str):
    """Return (html, None) on success or (None, reason) on failure."""
    try:
        r = requests.get(link, headers=HEADERS, timeout=40, allow_redirects=True)
    except requests.RequestException as e:
        return None, f"couldn't reach the site ({type(e).__name__})"
    if r.status_code >= 400:
        return None, f"the site answered HTTP {r.status_code}"
    ctype = r.headers.get("content-type", "")
    if "pdf" in ctype:
        return None, "the link is a PDF, which can't be turned into feed text"
    r.encoding = r.encoding or r.apparent_encoding
    out = trafilatura.extract(
        r.text, url=link, output_format="html", include_formatting=True,
        include_links=True, include_tables=True, include_images=False,
        include_comments=False, favor_recall=True,
    )
    if not out or len(re.sub(r"<[^>]+>", "", out).strip()) < 400:
        return None, "no readable article text found (the page may need JavaScript)"
    return body_only(out)[:MAX_CHARS], None


def cdata(s: str) -> str:
    return "<![CDATA[" + s.replace("]]>", "]]]]><![CDATA[>") + "]]>"


def main():
    tree = ET.parse(INBOX)
    channel = tree.getroot().find("channel")
    items = channel.findall("item")
    now = time.time()
    changed_cache = False

    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">',
        "<channel>",
        f"<title>{escape(channel.findtext('title', 'Low Noise: To Read'))}</title>",
        f"<link>{escape(channel.findtext('link', ''))}</link>",
        "<description>Full-text feed for KOReader, built from feed.xml.</description>",
        f"<lastBuildDate>{format_datetime(datetime.now(timezone.utc))}</lastBuildDate>",
    ]

    for it in items:
        link = (it.findtext("link") or "").strip()
        title = it.findtext("title") or link
        desc = it.findtext("description") or ""
        pub = it.findtext("pubDate") or ""
        if not link:
            continue
        i = item_id(link)
        c = load_cache(i) or {"link": link, "tries": 0}
        if c.get("status") != "ok" and c.get("tries", 0) < MAX_TRIES and now - c.get("last", 0) > RETRY_AFTER:
            html, why = extract(link)
            c["tries"] = c.get("tries", 0) + 1
            c["last"] = now
            if html:
                c.update(status="ok", html=html)
                print(f"ok     {link}")
            else:
                c.update(status="failed", reason=why)
                print(f"FAILED {link}: {why}")
            save_cache(i, c)
            changed_cache = True

        if c.get("status") == "ok":
            content = c["html"]
        else:
            reason = c.get("reason", "it hasn't been fetched yet")
            content = (f"<p><em>The full text couldn't be added automatically: {escape(reason)}.</em></p>"
                       f"<p>{escape(desc)}</p><p>Read it at: <a href=\"{escape(link)}\">{escape(link)}</a></p>")
        source_line = f'<p><small>Source: <a href="{escape(link)}">{escape(link)}</a></small></p>'

        parts += [
            "<item>",
            f"<title>{escape(title)}</title>",
            f"<link>{escape(link)}</link>",
            f'<guid isPermaLink="true">{escape(link)}</guid>',
            f"<pubDate>{escape(pub)}</pubDate>",
            f"<description>{escape(desc)}</description>",
            f"<content:encoded>{cdata(content + source_line)}</content:encoded>",
            "</item>",
        ]

    parts += ["</channel>", "</rss>", ""]
    new = "\n".join(parts)
    old = open(OUT).read() if os.path.exists(OUT) else ""
    # Ignore lastBuildDate when deciding whether anything changed.
    strip = lambda s: re.sub(r"<lastBuildDate>.*?</lastBuildDate>", "", s)
    if strip(new) != strip(old) or changed_cache:
        with open(OUT, "w") as f:
            f.write(new)
        print(f"wrote {OUT} with {len(items)} items")
    else:
        print("no changes")


if __name__ == "__main__":
    sys.exit(main())
