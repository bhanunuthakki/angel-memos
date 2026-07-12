// Angel Memos Capture — background service worker.
//
// Capture flow (driven by content.js):
//   am-arm  -> arm the download-router for a company (must happen BEFORE any
//              page-initiated download fires) and return an ack.
//   am-run  -> print the deal page to PDF, download any anchor attachments,
//              then wait for the dataroom's button-triggered downloads to
//              settle, and write job.json LAST as the completeness marker.
//
// Why a router: AngelList dataroom documents (the deck, closing docs, etc.)
// download via JS buttons, not links. The page initiates those downloads
// itself, so we can't set their path at call time — instead we catch them in
// onDeterminingFilename and rewrite the path into angel-memos/<Company>/.
//
// Chrome extensions can only write under ~/Downloads; the `angel-memos watch`
// daemon then moves each completed drop into the Drive Evaluation folder.

const ROOT = "angel-memos";
const QUIET_MS = 3500; // no new download activity for this long => done
const MIN_RUN_MS = 4000; // never finalize sooner than this after am-run
const MAX_RUN_MS = 120000; // hard cap so a stuck download can't hang forever

// In-memory capture state. A capture is a short burst, so keeping this in the
// (possibly ephemeral) service worker for its duration is fine.
let active = null;
// active = {
//   company, dir, startedAt, lastActivity, buttonsDone,
//   pending:Set<number>, completed:number, finalize:Function|null
// }

// --- Download router: registered once, synchronously, at top level. ---------

chrome.downloads.onDeterminingFilename.addListener((item, suggest) => {
  if (!active) {
    suggest();
    return;
  }
  // Our own downloads (page PDF, anchors, job.json) already carry a filename.
  if (item.byExtensionId === chrome.runtime.id) {
    suggest();
    return;
  }
  if (!isAngelListItem(item)) {
    suggest();
    return;
  }
  const base = sanitizeFile(basename(item.filename || item.url));
  suggest({ filename: `${ROOT}/${active.dir}/${base}`, conflictAction: "uniquify" });
});

chrome.downloads.onCreated.addListener((item) => {
  if (!active) return;
  // Track everything that starts during a capture (ours + rerouted page docs).
  active.pending.add(item.id);
  active.lastActivity = Date.now();
});

chrome.downloads.onChanged.addListener((delta) => {
  if (!active || !delta.state) return;
  const state = delta.state.current;
  if (state === "complete" || state === "interrupted") {
    if (active.pending.delete(delta.id) && state === "complete") active.completed += 1;
    active.lastActivity = Date.now();
  }
});

// --- Message handlers. -------------------------------------------------------

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg) return false;
  if (msg.kind === "am-arm") {
    arm(msg)
      .then(() => sendResponse({ ok: true }))
      .catch((err) => sendResponse({ ok: false, error: String(err) }));
    return true;
  }
  if (msg.kind === "am-run") {
    run(msg, sender.tab)
      .then((count) => sendResponse({ ok: true, count }))
      .catch((err) => sendResponse({ ok: false, error: String(err) }));
    return true; // async sendResponse
  }
  return false;
});

async function arm(msg) {
  const company = (msg.company || "").trim();
  const dir = sanitizeFolder(company);
  if (!dir) throw new Error("empty company name");
  const now = Date.now();
  active = {
    company,
    dir,
    tier: msg.tier === "quick" ? "quick" : "none",
    sourceUrl: msg.sourceUrl || "",
    startedAt: now,
    lastActivity: now,
    buttonsDone: false,
    pending: new Set(),
    completed: 0,
  };
}

async function run(msg, tab) {
  if (!active) throw new Error("capture not armed");
  const dir = active.dir;

  // 1. Deal page -> PDF (the AngelList memo/narrative itself).
  try {
    const pdfBase64 = await printPageToPdf(tab.id);
    await startDownload({
      url: "data:application/pdf;base64," + pdfBase64,
      filename: `${ROOT}/${dir}/angellist - ${active.company}.pdf`,
    });
  } catch (err) {
    console.warn("page PDF failed:", err);
  }

  // 2. Any real anchor attachments (standard deal pages).
  for (const url of msg.attachments || []) {
    try {
      await startDownload({ url, filename: `${ROOT}/${dir}/${basename(url)}` });
    } catch (err) {
      console.warn("attachment failed to start:", url, err);
    }
  }

  // 3. The dataroom document downloads (deck etc.) were already clicked by the
  //    content script; they arrive via the router. Wait for everything to
  //    settle, then write job.json LAST.
  active.buttonsDone = true;
  active.lastActivity = Date.now();
  await waitForQuiescence();

  const job = {
    company: active.company,
    tier: active.tier,
    source_url: active.sourceUrl,
  };
  const encoded = btoa(unescape(encodeURIComponent(JSON.stringify(job, null, 2))));
  await startDownload({
    url: "data:application/json;base64," + encoded,
    filename: `${ROOT}/${dir}/job.json`,
  });

  const count = active.completed;
  active = null;
  return count;
}

// Resolve once no download has started/finished for QUIET_MS and nothing is
// pending — but never before MIN_RUN_MS (so slow dataroom fetches have time to
// begin) and never after MAX_RUN_MS (so a stuck download can't hang forever).
function waitForQuiescence() {
  return new Promise((resolve) => {
    const check = () => {
      if (!active) return resolve();
      const now = Date.now();
      const sinceRun = now - active.startedAt;
      const idle = now - active.lastActivity;
      if (sinceRun > MAX_RUN_MS) return resolve();
      if (sinceRun > MIN_RUN_MS && active.pending.size === 0 && idle > QUIET_MS) {
        return resolve();
      }
      setTimeout(check, 800);
    };
    setTimeout(check, 800);
  });
}

async function printPageToPdf(tabId) {
  const target = { tabId };
  await chrome.debugger.attach(target, "1.3");
  try {
    const result = await chrome.debugger.sendCommand(target, "Page.printToPDF", {
      printBackground: true,
      preferCSSPageSize: false,
    });
    return result.data;
  } finally {
    await chrome.debugger.detach(target).catch(() => {});
  }
}

function startDownload(options) {
  return chrome.downloads.download(
    Object.assign({ conflictAction: "uniquify", saveAs: false }, options)
  );
}

// --- Helpers. ----------------------------------------------------------------

function isAngelListItem(item) {
  const hay = `${item.url || ""} ${item.finalUrl || ""} ${item.referrer || ""}`;
  return /angellist\.com/i.test(hay);
}

function sanitizeFolder(name) {
  return (name || "")
    .replace(/[<>:"/\\|?*]/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/\.+$/, "")
    .slice(0, 80);
}

function sanitizeFile(name) {
  const cleaned = (name || "")
    .replace(/[<>:"/\\|?*]/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/^\.+/, "");
  return cleaned || "document.pdf";
}

function basename(pathOrUrl) {
  const raw = String(pathOrUrl || "");
  try {
    const path = new URL(raw).pathname;
    const last = decodeURIComponent(path.split("/").filter(Boolean).pop() || "");
    return sanitizeFile(last);
  } catch {
    // Not a URL (e.g. a browser-suggested filename path) — take the last segment.
    const last = raw.split(/[\\/]/).filter(Boolean).pop() || "";
    return sanitizeFile(last);
  }
}
