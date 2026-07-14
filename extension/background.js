// Angel Memos Capture — background service worker.
//
// Capture protocol (phases driven by content.js):
//   am-arm        arm the download-router + viewer-tab watcher for a company
//   am-print-page print the sender's tab (the AL memo) to PDF and save it
//   am-deck-url   download the deck's signed file-storage URL as
//                 "<Company> deck.pdf" (chrome.downloads needs no CORS)
//   am-finalize   wait for all downloads to settle, write job.json LAST
//   am-run        legacy single-shot (stale content script) — print + finalize
//
// Router note: an onDeterminingFilename listener OVERRIDES the filename path
// passed to chrome.downloads.download() — that's what sent our own files to
// the Downloads root in v0.2/0.3. So the router must explicitly suggest the
// full angel-memos/<Company>/ path for OUR downloads too, not just for the
// page-initiated ones it exists to catch.
//
// Chrome extensions can only write under ~/Downloads; the `angel-memos watch`
// daemon then moves each completed drop into the Drive Evaluation folder.

const ROOT = "angel-memos";
const QUIET_MS = 3000; // no new download/tab activity for this long => done
const MIN_RUN_MS = 2000; // never finalize sooner than this after am-finalize
const MAX_RUN_MS = 45000; // hard cap so a stuck download/tab can't hang forever
const PRINT_TIMEOUT_MS = 15000; // debugger print must not hang the capture

// Hosts the deck URL may point at (it comes from page resource timing).
const DECK_URL_HOSTS = /(\.amazonaws\.com|\.cloudfront\.net|\.angellist\.com|\.angel\.co)$/i;

// In-memory capture state. A capture is a short burst, so keeping this in the
// (possibly ephemeral) service worker for its duration is fine.
let active = null;
// active = {
//   company, dir, tier, sourceUrl, tabId,
//   startedAt, lastActivity, finalizing,
//   pending:Set<number>, viewerTabs:Set<number>, completed:number
// }

// Intended paths for downloads WE start: exact-URL map + FIFO fallback (our
// downloads are awaited sequentially, so FIFO order holds).
const pendingByUrl = new Map();
const pendingNameQueue = [];

// --- Download router: registered once, synchronously, at top level. ---------

chrome.downloads.onDeterminingFilename.addListener((item, suggest) => {
  if (!active) {
    suggest();
    return;
  }
  if (item.byExtensionId === chrome.runtime.id) {
    // OUR download: re-suggest the intended folder path (the listener's very
    // existence discards the path given to download() — see header note).
    // Shift the FIFO on EVERY own-download event so it stays aligned with the
    // push in startDownload; byUrl is the primary match, FIFO the fallback.
    const queued = pendingNameQueue.shift();
    const want =
      pendingByUrl.get(item.url) || pendingByUrl.get(item.finalUrl) || queued;
    pendingByUrl.delete(item.url);
    if (item.finalUrl) pendingByUrl.delete(item.finalUrl);
    const filename =
      want || `${ROOT}/${active.dir}/${sanitizeFile(basename(item.filename || ""))}`;
    suggest({ filename, conflictAction: "uniquify" });
    return;
  }
  if (!isAngelListItem(item)) {
    suggest();
    return;
  }
  // Page-initiated download during a capture (e.g. a deck row's own button).
  const base = sanitizeFile(basename(item.filename || item.url));
  suggest({ filename: `${ROOT}/${active.dir}/${base}`, conflictAction: "uniquify" });
});

chrome.downloads.onCreated.addListener((item) => {
  if (!active) return;
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

// --- Viewer-tab fallback: a tab opened by the capture tab gets printed. ------
// (The common dataroom deck is an in-page overlay handled via am-deck-url;
// this path only fires for decks that open in a real new tab.)

chrome.tabs.onCreated.addListener((tab) => {
  if (!active || tab.openerTabId !== active.tabId || tab.id == null) return;
  active.viewerTabs.add(tab.id);
  active.lastActivity = Date.now();
});

chrome.tabs.onUpdated.addListener(async (tabId, info, tab) => {
  if (!active || !active.viewerTabs.has(tabId) || info.status !== "complete") return;
  active.viewerTabs.delete(tabId);
  active.lastActivity = Date.now();
  try {
    const b64 = await withTimeout(printPageToPdf(tabId), PRINT_TIMEOUT_MS, "deck print");
    const name = deckFilename(tab && tab.title);
    await startDownload({
      url: "data:application/pdf;base64," + b64,
      filename: `${ROOT}/${active.dir}/${name}`,
    });
  } catch (err) {
    console.warn("[angel-memos] deck viewer print failed:", err);
  } finally {
    try {
      await chrome.tabs.remove(tabId);
    } catch (_) {
      /* tab already gone */
    }
    if (active) active.lastActivity = Date.now();
  }
});

// --- Message handlers. -------------------------------------------------------

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg) return false;
  const respond = (promise) => {
    promise
      .then((value) => sendResponse(Object.assign({ ok: true }, value || {})))
      .catch((err) => sendResponse({ ok: false, error: String(err) }));
    return true; // async sendResponse
  };
  switch (msg.kind) {
    case "am-arm":
      return respond(arm(msg, sender.tab));
    case "am-print-page":
      return respond(printMemo(sender.tab));
    case "am-deck-url":
      return respond(downloadDeckUrl(msg));
    case "am-finalize":
      return respond(finalize());
    case "am-run": // stale content script (pre-0.4) — print + finalize
      console.warn("[angel-memos] legacy am-run — reload the AngelList tab");
      return respond(
        printMemo(sender.tab).then(() => finalize())
      );
    default:
      return false;
  }
});

async function arm(msg, tab) {
  const company = (msg.company || "").trim();
  const dir = sanitizeFolder(company);
  if (!dir) throw new Error("empty company name");
  const now = Date.now();
  pendingByUrl.clear();
  pendingNameQueue.length = 0;
  active = {
    company,
    dir,
    tier: msg.tier === "quick" ? "quick" : "none",
    sourceUrl: msg.sourceUrl || "",
    tabId: tab ? tab.id : null,
    startedAt: now,
    lastActivity: now,
    pending: new Set(),
    viewerTabs: new Set(),
    completed: 0,
  };
  console.log(`[angel-memos] armed for "${company}" -> ${ROOT}/${dir}/`);
}

async function printMemo(tab) {
  if (!active) throw new Error("capture not armed");
  if (!tab || tab.id == null) throw new Error("no sender tab");
  const b64 = await withTimeout(printPageToPdf(tab.id), PRINT_TIMEOUT_MS, "page print");
  await startDownload({
    url: "data:application/pdf;base64," + b64,
    filename: `${ROOT}/${active.dir}/angellist - ${active.company}.pdf`,
  });
  console.log(`[angel-memos] memo printed for "${active.company}"`);
}

async function downloadDeckUrl(msg) {
  if (!active) throw new Error("capture not armed");
  const url = String(msg.url || "");
  let host;
  try {
    host = new URL(url).hostname;
  } catch {
    throw new Error("invalid deck url");
  }
  if (!/^https:/i.test(url) || !DECK_URL_HOSTS.test(host)) {
    throw new Error(`deck url host not allowed: ${host}`);
  }
  const name = sanitizeFile(msg.name || "deck.pdf");
  await startDownload({ url, filename: `${ROOT}/${active.dir}/${name}` });
  console.log(`[angel-memos] deck download started: ${name}`);
}

async function finalize() {
  if (!active) throw new Error("capture not armed");
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
    filename: `${ROOT}/${active.dir}/job.json`,
  });

  const count = active.completed;
  console.log(`[angel-memos] done "${active.company}": ${count} file(s) saved`);
  active = null;
  return { count };
}

// Resolve once no download/tab activity for QUIET_MS with nothing pending and
// no viewer tab still loading — bounded below by MIN_RUN_MS and above by
// MAX_RUN_MS so a stuck download can never hang the capture.
function waitForQuiescence() {
  const startedWaiting = Date.now();
  return new Promise((resolve) => {
    const check = () => {
      if (!active) return resolve();
      const now = Date.now();
      const waited = now - startedWaiting;
      const idle = now - active.lastActivity;
      if (waited > MAX_RUN_MS) return resolve();
      const quiet =
        active.pending.size === 0 && active.viewerTabs.size === 0 && idle > QUIET_MS;
      if (waited > MIN_RUN_MS && quiet) return resolve();
      setTimeout(check, 500);
    };
    setTimeout(check, 500);
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
  // Record the intended path BEFORE starting, so the router can re-suggest it
  // (see header note about onDeterminingFilename overriding download paths).
  if (options.filename) {
    pendingByUrl.set(options.url, options.filename);
    pendingNameQueue.push(options.filename);
  }
  return chrome.downloads.download(
    Object.assign({ conflictAction: "uniquify", saveAs: false }, options)
  );
}

// Reject if `promise` doesn't settle within `ms`, so an unbounded await
// (e.g. a debugger attach that never resolves) can't hang the capture.
function withTimeout(promise, ms, label) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`${label} timed out after ${ms}ms`)), ms);
    promise.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (err) => {
        clearTimeout(timer);
        reject(err);
      }
    );
  });
}

// --- Helpers. ----------------------------------------------------------------

function isAngelListItem(item) {
  const hay = `${item.url || ""} ${item.finalUrl || ""} ${item.referrer || ""}`;
  return /angellist\.com|amazonaws\.com/i.test(hay);
}

function deckFilename(title) {
  const base = sanitizeFile((title || "deck").replace(/\s*\|\s*angellist.*$/i, ""));
  return /\.pdf$/i.test(base) ? base : `${base}.pdf`;
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
    const last = raw.split(/[\\/]/).filter(Boolean).pop() || "";
    return sanitizeFile(last);
  }
}
