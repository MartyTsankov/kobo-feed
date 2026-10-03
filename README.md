# kobo-feed

A personal reading feed for KOReader. Anything sent with **Send to Kobo** (from Low Noise, a phone share sheet, or a browser bookmark) is added to `feed.xml` right away. About a minute later, a GitHub Action processes it:

- **Web articles:** the Action pulls out the article text and writes it into `kobo.xml`, the RSS feed for KOReader's news downloader. The Kobo never has to contact the article's website.
- **PDFs and arXiv papers:** the Action saves the real PDF into `pdfs/` and lists it in `opds.xml`, an OPDS catalog that KOReader syncs into a folder. You read the actual PDF, with its layout and math intact.

- `feed.xml`: the inbox of links. The send page writes here. Two pinned items keep it from ever having just one entry, which KOReader has had trouble with (koreader/koreader#12953).
- `kobo.xml`: **the RSS feed KOReader reads**, with the full text of each web article. Only the Action writes it.
- `opds.xml` + `pdfs/`: **the PDF catalog KOReader syncs**. PDFs whose links have left `feed.xml` are deleted automatically.
- `articles/`: the Action's record of each link (full text, PDF, excerpt or failed), so each link is fetched only once.
- `send.html`: the send page, served by GitHub Pages. It uses a GitHub token saved in your browser to add an item to `feed.xml`.
- `fulltext.py` + `.github/workflows/fulltext.yml`: the Action. It runs after every send, and once an hour to retry any link that failed.
- `build_feed.py`: optional. Rebuilds `feed.xml` from scratch if it ever gets damaged.

## One-time setup

1. **Turn on Pages.** Go to Settings → Pages → Build and deployment → Source: *Deploy from a branch* → `main` / root. After a minute, the send page is live at `https://USERNAME.github.io/kobo-feed/send.html`.
2. **Make a token.** Go to GitHub → Settings → Developer settings → Fine-grained tokens → Generate. Under *Repository access*, choose "Only select repositories" and pick `kobo-feed`. Under *Permissions*, set **Contents: Read and write** and leave everything else as "No access". Set whatever expiry you're comfortable with.
3. **Save the token on each device.** Open the send page once on each device you'll send from. It asks for the token once and keeps it in that browser only.
4. **Add the feed to KOReader.** Go to Tools → News downloader → Settings → Edit news feeds → add:
   - URL: `https://raw.githubusercontent.com/USERNAME/kobo-feed/main/kobo.xml`
   - Download full article: **off** (the text is already in the feed)
   - Limit: 10 or so
   Then choose *Sync news feeds* whenever you want new articles.
5. **Add the PDF catalog to KOReader.** In the file browser, tap the search (magnifying glass) icon and choose **OPDS catalog**. Tap the + icon at the top left to add a catalog:
   - Name: `Low Noise PDFs`
   - URL: `https://raw.githubusercontent.com/USERNAME/kobo-feed/main/opds.xml`
   Then, in the OPDS menu's settings, choose a **Sync folder** (e.g. `Low Noise PDFs`), set **File types to sync** to `pdf`, and turn on sync for this catalog. *Sync all* downloads any new PDFs. You can also open the catalog and tap one PDF to download just that one.

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

- Items appear on the Kobo about 5–6 minutes after sending: about 1 minute for the Action, plus up to 5 minutes of GitHub's cache on raw files.
- After you send something, the send page waits for the Action and tells you what happened: **full text**, **saved as a PDF**, only an **excerpt**, or **no text**.
- In the news feed, anything that isn't full article text is labeled in its title: **[PDF]** (sync the PDF catalog to get it), **[Excerpt]** (only a short piece came through, e.g. an abstract page) or **[Link only]** (nothing could be fetched).
- arXiv links always become the real PDF, including old IDs like `math/9404236`.
- Temporary problems (a site that's down or slow, or "too many requests") are retried automatically: a few times within the same run, then every hour for about eight hours. Those items show up on the Kobo once they come through, so a link you've sent is never lost. If GreaterWrong can't be reached, the Action falls back to the original LessWrong page.
- The repo's **Actions** tab logs `OK`, `PDF`, `PARTIAL` or `FAILED` for each link.
- If an Action run fails with a 403 on `git push`, go to Settings → Actions → General → Workflow permissions and choose "Read and write permissions".
- LessWrong and Alignment Forum links are swapped for their GreaterWrong mirror, which renders better on e-ink.
- The feed, the catalog and the PDFs are public: anyone with the URL can see them. The token only lives in your own browsers. If a device is lost, revoke the token on GitHub.
