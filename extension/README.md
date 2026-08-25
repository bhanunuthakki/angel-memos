# Angel Memos Capture (Chrome extension)

One-click capture of an AngelList deal into the angel-memos pipeline.

## What it does

On any AngelList page a small panel appears (bottom-right):

- **Save + Quick Research** — capture the deal AND ask the watcher to run
  the quick tier (diligence + scorecard) automatically.
- **Save only** — capture without triggering research.

Capture writes the **AL memo and, when the deal provides one, the deck** into
`Downloads/angel-memos/<Company>/`. A missing deck is valid and does not block
finalization or ingestion. Everything else in the dataroom (closing documents,
disclaimers, etc.) is deliberately ignored.

Captures are deliberately single-flight. Wait for the panel to show the saved
checkmark before starting another deal in a different tab. If a capture is
already running, the second tab shows which company must finish first; it never
reassigns the first capture's downloads to the second company's folder.

1. **AL memo** — the deal page printed to PDF (`angellist - <Company>.pdf`)
   via Chrome's debugger API, with the panel hidden and *before* the deck
   viewer opens, so neither appears in the print.
2. **Deck** — the extension finds the *one* dataroom document row whose name
   is the pitch deck (`/deck|pitch|presentation/`) and acts only on it:
   - if that row has a download control, it clicks it and the background
     download-router files the result into the company folder;
   - if the deck is **view-only** (a clickable table cell, no download
     button — the common case), it clicks the deck open. The in-page
     PSPDFKit viewer fetches the actual PDF from a signed file-storage URL;
     the extension waits up to 60 seconds for that request, picks the URL out
     of the page's resource timing, and the background downloads it directly
     as `<Company> deck.pdf`, then closes the overlay. A deck that opens in a
     real new tab is printed from that tab as a fallback.
3. `job.json` is written **last** — the watcher treats it as the "drop is
   complete" marker, so it lands only after the memo + deck settle.

The company-folder prompt prefers AngelList's page metadata/title and ignores
generic headings such as `Overview` or `Investment Memo`. Confirm the proposed
name before starting the capture. Placeholder values made only of punctuation,
such as `----`, are rejected.

The junk-document rows (the ones that *do* have download buttons) are never
clicked, so closing docs and disclaimers don't come through.

> **If the deck doesn't land:** the panel says "NO DECK" and the reason is in
> the consoles — the page console (F12 on the deal tab) and the service-worker
> console ("Inspect views: service worker" on the extension card) both log
> `[angel-memos] …` lines. A deck rendered from a canvas/image stream with no
> fetchable document URL still can't be auto-captured; open it and use the
> browser's Print → Save as PDF into the company folder for that one.

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
