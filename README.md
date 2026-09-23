# kobo-feed

A personal RSS feed for KOReader. Anything sent with **Send to Kobo** (from Low Noise, a phone share sheet, or a browser bookmark) is added to `feed.xml` right away. KOReader's news downloader then pulls it the next time you sync.

- `feed.xml`: the feed KOReader reads. Two pinned items keep it from ever having just one entry, which KOReader has had trouble with (koreader/koreader#12953).
- `send.html`: the send page, served by GitHub Pages. It uses a GitHub token saved in your browser to add an item to `feed.xml`.
- `build_feed.py`: optional. Rebuilds `feed.xml` from scratch if it ever gets damaged.

## One-time setup

1. **Turn on Pages.** Go to Settings → Pages → Build and deployment → Source: *Deploy from a branch* → `main` / root. After a minute, the send page is live at `https://USERNAME.github.io/kobo-feed/send.html`.
2. **Make a token.** Go to GitHub → Settings → Developer settings → Fine-grained tokens → Generate. Under *Repository access*, choose "Only select repositories" and pick `kobo-feed`. Under *Permissions*, set **Contents: Read and write** and leave everything else as "No access". Set whatever expiry you're comfortable with.
3. **Save the token on each device.** Open the send page once on each device you'll send from. It asks for the token once and keeps it in that browser only.
4. **Add the feed to KOReader.** Go to Tools → News downloader → Settings → Edit news feeds → add:
   - URL: `https://raw.githubusercontent.com/USERNAME/kobo-feed/main/feed.xml`
   - Download full article: **on**
   - Limit: 10 or so
   Then choose *Sync news feeds* whenever you want new articles.

## Sending from anywhere

**iPhone (Shortcuts app):** make a new shortcut.
1. Open the shortcut's details, turn on *Show in Share Sheet*, and set it to accept **URLs** and **Safari web pages**.
2. Add the action **URL Encode** with *Shortcut Input*.
3. Add the action **Open URLs** with `https://USERNAME.github.io/kobo-feed/send.html?url=` followed by the *URL Encoded Text* variable.
Name it "Send to Kobo". In Safari: Share → Send to Kobo.

**Laptop (bookmarklet):** add a bookmark with this as its URL:

```
javascript:window.open('https://USERNAME.github.io/kobo-feed/send.html?url='+encodeURIComponent(location.href)+'&title='+encodeURIComponent(document.title))
```

**Android:** save the same bookmarklet in Chrome and run it by typing its name into the address bar on the page you want. Or open `send.html` and paste the link.

## Notes

- Items appear on the Kobo about 5 minutes after sending, because GitHub caches raw files briefly.
- LessWrong and Alignment Forum links are swapped for their GreaterWrong mirror, and arXiv links for the HTML version of the paper. Both render better on e-ink.
- The feed is public: anyone with the URL can see the list of links. The token only lives in your own browsers. If a device is lost, revoke the token on GitHub.
