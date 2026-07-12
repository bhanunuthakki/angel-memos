# Angel Memos Capture (Chrome extension)

One-click capture of an AngelList deal into the angel-memos pipeline.

## What it does

On any AngelList page a small panel appears (bottom-right):

- **Save + Quick Research** — capture the deal AND ask the watcher to run
  the quick tier (diligence + scorecard) automatically.
- **Save only** — capture without triggering research.

Capture writes **only two things** into `Downloads/angel-memos/<Company>/` —
the memo and the deck. Everything else in the dataroom (closing documents,
disclaimers, etc.) is deliberately ignored.

1. **AL memo** — the deal page itself, printed to PDF
   (`angellist - <Company>.pdf`) via Chrome's debugger API.
2. **Deck** — the extension finds the *one* dataroom document row whose name
   is the pitch deck (`/deck|pitch|presentation/`) and acts only on it:
   - if that row has a download control, it clicks it and a background
     download-router files the result into the company folder;
   - if the deck is **view-only** (a clickable table cell with no download
     button — the common case), it clicks the deck open; when the viewer
     opens in a new tab the background prints *that* tab to PDF, same as the
     AL memo. In-page embedded PDF viewers (`<iframe>`/`<embed>` with an
     https source) are captured by URL.
3. `job.json` is written **last** — the watcher treats it as the "drop is
   complete" marker, so it lands only after the memo + deck settle.

The junk-document rows (the ones that *do* have download buttons) are never
clicked, so closing docs and disclaimers no longer come through.

> **Deck capture is best-effort and needs a live test.** Datarooms vary in
> how they open the deck (new-tab viewer, embedded `<iframe>`, or a
> canvas/image renderer with no PDF URL — that last case still can't be
> auto-captured). After reloading the extension, capture one deal and confirm
> a deck PDF lands in the folder; if not, open the deck and use the browser's
> own Print → Save as PDF into the company folder. The service-worker console
> logs `[angel-memos] capturing "…" (deck: view|download|none)` so you can see
> which path fired.

## Install (load unpacked)

1. Open `chrome://extensions`, enable **Developer mode**.
2. **Load unpacked** → select this `extension/` folder.
3. Visit an AngelList deal page; the panel appears bottom-right.

Note: printing the page to PDF uses Chrome's debugger API, so Chrome
shows a brief "started debugging this browser" banner during capture.
That is expected and it detaches immediately after.

## The other half: the watcher

Captures land in `~/Downloads/angel-memos/` and stay there until moved.
Run the watcher (or a one-shot ingest) to move drops into the Drive
Evaluation folder and run quick research for `tier: quick` jobs:

```powershell
angel-memos watch            # daemon: ingest + auto quick research
angel-memos ingest           # one-shot: move drops, no research
```

To auto-start the watcher at login, register it with Task Scheduler:

```powershell
schtasks /Create /TN "angel-memos-watch" /SC ONLOGON /TR "\"$env:USERPROFILE\.venv-or-uv-path\angel-memos.exe\" watch"
```

(or simply keep a terminal tab running `angel-memos watch`).

## Security notes

- The extension only acts on pages where you click its button, only on
  AngelList domains, and captures only the memo page + the deck.
- It never sees or handles your AngelList credentials — it rides the
  session in your normal logged-in tab.
- Everything stays local: page PDF and attachments go to your Downloads
  folder; nothing is sent anywhere else.
