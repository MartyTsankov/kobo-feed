#!/usr/bin/env python3
"""Turn feed.xml (links only) into what KOReader reads.

Runs in GitHub Actions after every send. The send page only ever edits
feed.xml; this script only writes kobo.xml, opds.xml, articles/ and pdfs/,
so the two never fight over the same file.

For each item in feed.xml:
  - Web pages: fetch from GitHub's servers with a normal browser identity and
    pull out the article body (reader-mode style). The text goes into kobo.xml
    (the RSS feed for KOReader's news downloader).
  - PDFs and arXiv papers: download the actual PDF into pdfs/ and list it in
    opds.xml (an OPDS catalog that KOReader syncs into a folder), so you read
    the real PDF with its layout and math intact.
  - Each result is judged and cached in articles/<id>.json:
      ok       full article text
      pdf      saved as a PDF
      partial  only a short excerpt came through (e.g. an abstract page)
      failed   nothing usable
    On the Kobo, items that aren't full text say so in their title:
    [PDF], [Excerpt] or [Link only]. The send page reads the cache to tell
    you right after sending.
"""
import hashlib
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import format_datetime, parsedate_to_datetime
from html import escape

import pymupdf
import requests
import trafilatura

INBOX = "feed.xml"
OUT = "kobo.xml"
OPDS = "opds.xml"
CACHE = "articles"
PDF_DIR = "pdfs"
CACHE_VERSION = 3        # bump to re-process everything once
MIN_WORDS = 600          # below this, a web page counts as an excerpt, not the piece
MAX_TRIES = 2            # runs to try a link that failed for a lasting reason (404, no text)
MAX_RETRIES = 8          # runs to retry a link that failed for a temporary reason (network, 429, 5xx)
RETRY_AFTER = 20 * 60    # seconds between retries (the workflow also runs hourly)
TRANSIENT_HTTP = {408, 425, 429, 500, 502, 503, 504, 520, 522, 524}
MAX_CHARS = 600_000      # keep the feed a sane size
MAX_PDF_BYTES = 60 * 1024 * 1024

REPO = os.environ.get("GITHUB_REPOSITORY", "MartyTsankov/kobo-feed")
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}/main"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/pdf,*/*;q=0.8",
           "Accept-Language": "en-US,en;q=0.9"}

# New-style (2301.01234) and old-style (math/9404236, hep-th/9711200) arXiv ids.
ARXIV = re.compile(
    r"https?://(?:www\.|export\.)?arxiv\.org/(?:abs|pdf|html)/"
    r"((?:[a-z\-]+(?:\.[A-Z]{2})?/\d{7})|(?:\d{4}\.\d{4,5}))(?:v\d+)?", re.I)


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


def count_words(html: str) -> int:
    return len(re.sub(r"<[^>]+>", " ", html or "").split())


def body_only(html: str) -> str:
    m = re.search(r"<body[^>]*>(.*)</body>", html, re.S | re.I)
    return (m.group(1) if m else html).strip()


def html_to_article(text: str, url: str):
    out = trafilatura.extract(
        text, url=url, output_format="html", include_formatting=True,
        include_links=True, include_tables=True, include_images=False,
        include_comments=False, favor_recall=True,
    )
    return body_only(out) if out else None


class Transient(str):
    """An error message for a failure that is likely temporary (worth retrying later)."""


def fetch(url):
    """GET with up to 3 attempts for temporary failures. Returns (response, None) or (None, error).
    The error is a Transient when retrying later may succeed."""
    waits = [5, 20]
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=60, allow_redirects=True)
        except requests.RequestException as e:
            err = Transient(f"couldn't reach the site ({type(e).__name__})")
        else:
            if r.status_code < 400:
                return r, None
            if r.status_code not in TRANSIENT_HTTP:
                return None, f"the site answered HTTP {r.status_code}"
            err = Transient(f"the site answered HTTP {r.status_code}"
                            + (" (too many requests)" if r.status_code == 429 else ""))
            ra = r.headers.get("retry-after", "")
            if ra.isdigit() and int(ra) <= 60 and attempt < 2:
                waits[attempt] = max(waits[attempt], int(ra))
        if attempt < 2:
            time.sleep(waits[attempt])
    return None, err


def is_pdf(r) -> bool:
    return "pdf" in r.headers.get("content-type", "").lower() or r.content[:5] == b"%PDF-"


def save_pdf(i: str, data: bytes):
    """Validate and store a PDF. Returns (fields, None) or (None, reason)."""
    if len(data) > MAX_PDF_BYTES:
        return None, f"the PDF is too large ({len(data) // (1024 * 1024)} MB)"
    try:
        pages = pymupdf.open(stream=data, filetype="pdf").page_count
    except Exception:
        return None, "the PDF couldn't be opened"
    os.makedirs(PDF_DIR, exist_ok=True)
    path = f"{PDF_DIR}/{i}.pdf"
    with open(path, "wb") as f:
        f.write(data)
    return {"file": path, "pages": pages, "bytes": len(data)}, None


def fail(err) -> dict:
    return {"status": "retrying" if isinstance(err, Transient) else "failed", "reason": str(err)}


def alternates(link: str):
    """Other addresses for the same piece, tried if the first one can't be reached."""
    m = re.match(r"https?://(?:www\.)?greaterwrong\.com/(posts/.*)", link)
    if m:
        return ["https://www.lesswrong.com/" + m.group(1)]
    return []


def process(i: str, link: str) -> dict:
    m = ARXIV.match(link)
    if m:  # arXiv: always the real PDF (math survives, any age of paper)
        r, err = fetch(f"https://arxiv.org/pdf/{m.group(1)}")
        if err:
            return fail(err)
        if not is_pdf(r):
            return {"status": "failed", "reason": "arXiv didn't return a PDF"}
        saved, err = save_pdf(i, r.content)
        if not saved:
            return {"status": "failed", "reason": err}
        return {"status": "pdf", **saved, "title": arxiv_title(m.group(1)) or pdf_title(r.content)}

    r, err, used = None, None, link
    for url in [link] + alternates(link):
        r, err = fetch(url)
        if not err:
            used = url
            break
    if err:
        return fail(err)
    if is_pdf(r):
        saved, err = save_pdf(i, r.content)
        if not saved:
            return {"status": "failed", "reason": err}
        return {"status": "pdf", **saved, "title": pdf_title(r.content)}

    r.encoding = r.encoding or r.apparent_encoding
    title = page_title(r.text, used)
    html = html_to_article(r.text, used)
    w = count_words(html)
    if w >= MIN_WORDS:
        return {"status": "ok", "html": html[:MAX_CHARS], "words": w, "title": title}
    if w > 0:
        return {"status": "partial", "html": html[:MAX_CHARS], "words": w, "title": title,
                "reason": f"only {w} words came through, probably an abstract or summary rather than the full piece"}
    return {"status": "failed", "title": title, "reason": "no readable text found (the page may need JavaScript)"}


def needs_title(title: str, link: str) -> bool:
    t = (title or "").strip()
    return not t or t == link or t.startswith(("http://", "https://"))


def page_title(html_text: str, url: str):
    try:
        meta = trafilatura.extract_metadata(html_text, default_url=url)
        if meta and meta.title:
            return meta.title.strip()
    except Exception:
        pass
    m = re.search(r"<title[^>]*>(.*?)</title>", html_text or "", re.S | re.I)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else None


def pdf_title(data: bytes):
    try:
        t = (pymupdf.open(stream=data, filetype="pdf").metadata or {}).get("title") or ""
        return t.strip() or None
    except Exception:
        return None


def arxiv_title(aid: str):
    r, err = fetch(f"https://arxiv.org/abs/{aid}")
    if err:
        return None
    m = re.search(r'<meta name="citation_title" content="([^"]+)"', r.text)
    return m.group(1).strip() if m else page_title(r.text, f"https://arxiv.org/abs/{aid}")


def cdata(s: str) -> str:
    return "<![CDATA[" + s.replace("]]>", "]]]]><![CDATA[>") + "]]>"


def iso(pub: str) -> str:
    try:
        return parsedate_to_datetime(pub).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main():
    tree = ET.parse(INBOX)
    channel = tree.getroot().find("channel")
    items = channel.findall("item")
    now = time.time()
    changed = False
    feed_title = channel.findtext("title", "Low Noise: To Read")

    rss = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">',
        "<channel>",
        f"<title>{escape(feed_title)}</title>",
        f"<link>{escape(channel.findtext('link', ''))}</link>",
        "<description>Full-text feed for KOReader, built from feed.xml.</description>",
        f"<lastBuildDate>{format_datetime(datetime.now(timezone.utc))}</lastBuildDate>",
    ]
    pdf_entries, keep_pdfs = [], set()

    for it in items:
        link = (it.findtext("link") or "").strip()
        title = it.findtext("title") or link
        desc = it.findtext("description") or ""
        pub = it.findtext("pubDate") or ""
        if not link:
            continue
        i = item_id(link)
        c = load_cache(i) or {}
        if c.get("v") != CACHE_VERSION:
            c = {"v": CACHE_VERSION, "link": link, "tries": 0}
        limit = MAX_RETRIES if c.get("status") in (None, "retrying") else MAX_TRIES
        due = (c.get("status") not in ("ok", "pdf", "partial")
               and c.get("tries", 0) < limit and now - c.get("last", 0) > RETRY_AFTER)
        if due:
            res = process(i, link)
            for k in ("html", "words", "reason", "file", "pages", "bytes", "title"):
                c.pop(k, None)
            c.update(res, tries=c.get("tries", 0) + 1, last=now,
                     checked=datetime.now(timezone.utc).isoformat(timespec="seconds"))
            if c["status"] == "retrying" and c["tries"] >= MAX_RETRIES:
                c["status"] = "failed"
            detail = (f"{c.get('pages')} pages" if c["status"] == "pdf" else f"{c.get('words', 0)} words")
            print(f"{c['status'].upper():8} {detail:>12}  {link}" + (f"  ({c['reason']})" if c.get("reason") else ""))
            save_cache(i, c)
            changed = True

        if needs_title(title, link) and c.get("title"):
            title = c["title"]
        status = c.get("status", "pending")
        if status in ("pending", "retrying"):
            continue  # appears on the Kobo once it's resolved
        source_line = f'<p><small>Source: <a href="{escape(link)}">{escape(link)}</a></small></p>'
        if status == "ok":
            shown, content = title, c["html"]
        elif status == "pdf":
            keep_pdfs.add(c["file"])
            pdf_entries.append((iso(pub), i, title, desc, c))
            shown = "[PDF] " + title
            content = (f"<p><strong>This one is a PDF ({c.get('pages', '?')} pages).</strong> "
                       "Get it from the <em>Low Noise PDFs</em> OPDS catalog: in KOReader's file browser, "
                       "tap the search icon, choose OPDS catalog, and sync.</p>"
                       f"<p>{escape(desc)}</p>")
        elif status == "partial":
            shown = "[Excerpt] " + title
            content = (f"<p><strong>Only an excerpt came through ({c.get('words', 0)} words).</strong> "
                       "This is probably an abstract or summary, not the full piece. "
                       f"Read it at: <a href=\"{escape(link)}\">{escape(link)}</a></p><hr/>" + c["html"])
        else:
            shown = "[Link only] " + title
            reason = c.get("reason", "it hasn't been fetched yet")
            content = (f"<p><strong>The text couldn't be added:</strong> {escape(reason)}.</p>"
                       f"<p>{escape(desc)}</p><p>Read it at: <a href=\"{escape(link)}\">{escape(link)}</a></p>")
        body = cdata(content + source_line)
        rss += [
            "<item>",
            f"<title>{escape(shown)}</title>",
            f"<link>{escape(link)}</link>",
            f'<guid isPermaLink="true">{escape(link)}</guid>',
            f"<pubDate>{escape(pub)}</pubDate>",
            # Full text in BOTH fields: some KOReader versions read only <description>.
            f"<description>{body}</description>",
            f"<content:encoded>{body}</content:encoded>",
            "</item>",
        ]
    rss += ["</channel>", "</rss>", ""]

    # OPDS acquisition catalog, newest first (KOReader's sync requires that order).
    pdf_entries.sort(key=lambda e: e[0], reverse=True)
    newest = pdf_entries[0][0] if pdf_entries else "2026-01-01T00:00:00Z"
    kind = "application/atom+xml;profile=opds-catalog;kind=acquisition"
    opds = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:dc="http://purl.org/dc/terms/" '
        'xmlns:opds="http://opds-spec.org/2010/catalog">',
        f"<id>urn:kobo-feed:{escape(REPO)}:pdfs</id>",
        "<title>Low Noise PDFs</title>",
        f"<updated>{newest}</updated>",
        "<author><name>kobo-feed</name></author>",
        f'<link rel="self" href="{RAW_BASE}/{OPDS}" type="{kind}"/>',
        f'<link rel="start" href="{RAW_BASE}/{OPDS}" type="{kind}"/>',
    ]
    for when, i, title, desc, c in pdf_entries:
        opds += [
            "<entry>",
            f"<title>{escape(title)}</title>",
            f"<id>urn:kobo-feed:{i}</id>",
            f"<updated>{when}</updated>",
            f"<summary>{escape(desc)}</summary>",
            f'<link rel="http://opds-spec.org/acquisition" href="{RAW_BASE}/{c["file"]}" type="application/pdf"/>',
            "</entry>",
        ]
    opds += ["</feed>", ""]

    # Remove PDFs whose items have left feed.xml (the send page keeps the newest 60).
    os.makedirs(PDF_DIR, exist_ok=True)
    open(os.path.join(PDF_DIR, ".gitkeep"), "a").close()
    for name in os.listdir(PDF_DIR):
        path = f"{PDF_DIR}/{name}"
        if name.endswith(".pdf") and path not in keep_pdfs:
            os.remove(path)
            changed = True

    strip = lambda s: re.sub(r"<lastBuildDate>.*?</lastBuildDate>", "", s)
    for path, text, cmp in ((OUT, "\n".join(rss), strip), (OPDS, "\n".join(opds), lambda s: s)):
        old = open(path).read() if os.path.exists(path) else ""
        if cmp(text) != cmp(old) or changed:
            with open(path, "w") as f:
                f.write(text)
    print(f"{len(items)} items; {len(pdf_entries)} PDFs in the catalog")


if __name__ == "__main__":
    sys.exit(main())
