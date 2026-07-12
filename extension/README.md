# Angel Memos Capture (Chrome extension)

One-click capture of an AngelList deal into the angel-memos pipeline.

## What it does

On any AngelList page a small panel appears (bottom-right):

- **Save + Quick Research** — capture the deal AND ask the watcher to run
  the quick tier (diligence + scorecard) automatically.
- **Save only** — capture without triggering research.

Capture does three things into `Downloads/angel-memos/<Company>/`:
1. prints the deal page to PDF (`angellist - <Company>.pdf`);
2. grabs the dataroom documents (the **deck**, closing docs, etc.). These
   are JS **buttons** (`aria-label="Download"` / `"Download all"`), *not*
   links — so the extension clicks them and a background download-router
   reroutes the resulting files into the company folder. "Download all"
   arrives as a zip, which the watcher unpacks on ingest;
3. writes `job.json` **last** — the watcher treats it as the "drop is
   complete" marker, so it's only written after all downloads settle.

Standard deal pages that expose real `<a href>` attachment links still work
too — those are downloaded directly.

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

- The extension only reads pages you click the button on, only on
  AngelList domains, and downloads only same-page links.
- It never sees or handles your AngelList credentials — it rides the
  session in your normal logged-in tab.
- Everything stays local: page PDF and attachments go to your Downloads
  folder; nothing is sent anywhere else.
