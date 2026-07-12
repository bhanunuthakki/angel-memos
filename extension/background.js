// Angel Memos Capture — background service worker.
//
// Capture flow (driven by content.js):
//   am-arm  -> arm the download-router + viewer-tab watcher for a company
//              (must happen BEFORE any page download / viewer tab opens) and
//              return an ack.
//   am-run  -> print the deal page to PDF, download anchor attachments +
//              embedded PDF sources, wait for the dataroom's button-triggered
//              downloads AND any deck-viewer tab to settle, then write job.json
//              LAST as the completeness marker.
//
// Three capture mechanisms, because datarooms expose documents three ways:
//   - real <a href> links               -> downloaded directly
//   - JS download buttons ("Download")  -> clicked by content, caught by the
//                                          onDeterminingFilename router
//   - VIEW-ONLY deck (no download)      -> content clicks it open; if it opens
//                                          in a new tab we print that tab to
//                                          PDF here, same as the AL memo.
//
// Chrome extensions can only write under ~/Downloads; the `angel-memos watch`
// daemon then moves each completed drop into the Drive Evaluation folder.

const ROOT = "angel-memos";
const QUIET_MS = 3500; // no new download/tab activity for this long => done
const MIN_RUN_MS = 3000; // never finalize sooner than this after am-run
const MAX_RUN_MS = 45000; // hard cap so a stuck download/tab can't hang forever
const PRINT_TIMEOUT_MS = 15000; // debugger print must not hang the capture

// In-memory capture state. A capture is a short burst, so keeping this in the
// (possibly ephemeral) service worker for its duration is fine.
let active = null;
// active = {
//   company, dir, tier, sourceUrl, tabId,
//   startedAt, lastActivity, buttonsDone,
//   pending:Set<number>, viewerTabs:Set<number>, completed:number
// }

// --- Download router: registered once, synchronously, at top level. ---------

chrome.downloads.onDeterminingFilename.addListener((item, suggest) => {
  if (!active) {
    suggest();
    return;
  }
  // Our own downloads (page/deck PDFs, anchors, job.json) already carry a path.
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

// --- Deck-viewer tab watcher: a tab opened by the capture tab is the deck. --

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
    console.warn("deck viewer print failed:", err);
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
  if (msg.kind === "am-arm") {
    arm(msg, sender.tab)
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
  // Legacy shim: a stale content script (from before an extension reload, when
  // the tab wasn't refreshed) sends the old single-message format. Handle it
  // so the capture doesn't hang silently. Reload the page to get the new flow.
  if (msg.kind === "angel-memos-capture") {
    console.warn("[angel-memos] legacy capture message — reload the tab for full deck capture");
    arm(msg, sender.tab)
      .then(() => run({ attachments: msg.attachments || [], docClicks: 0 }, sender.tab))
      .then((count) => sendResponse({ ok: true, count }))
      .catch((err) => sendResponse({ ok: false, error: String(err) }));
    return true;
  }
  return false;
});

async function arm(msg, tab) {
  const company = (msg.company || "").trim();
  const dir = sanitizeFolder(company);
  if (!dir) throw new Error("empty company name");
  const now = Date.now();
  active = {
    company,
    dir,
    tier: msg.tier === "quick" ? "quick" : "none",
    sourceUrl: msg.sourceUrl || "",
    tabId: tab ? tab.id : null,
    startedAt: now,
    lastActivity: now,
    buttonsDone: false,
    pending: new Set(),
    viewerTabs: new Set(),
    completed: 0,
  };
}

async function run(msg, tab) {
  if (!active) throw new Error("capture not armed");
  const dir = active.dir;
  console.log(
    `[angel-memos] capturing "${active.company}" -> ${dir} ` +
      `(deck: ${msg.deckMode || "n/a"}, urls: ${(msg.attachments || []).length})`
  );

  // 1. Deal page -> PDF (the AngelList memo/narrative itself). Time-boxed so a
  //    hung debugger attach (e.g. DevTools open, another debugger attached)
  //    can never freeze the whole capture.
  try {
    const pdfBase64 = await withTimeout(
      printPageToPdf(tab.id),
      PRINT_TIMEOUT_MS,
      "page print"
    );
    await startDownload({
      url: "data:application/pdf;base64," + pdfBase64,
      filename: `${ROOT}/${dir}/angellist - ${active.company}.pdf`,
    });
  } catch (err) {
    console.warn("[angel-memos] page PDF failed (continuing):", err);
  }

  // 2. Anchor attachments + embedded PDF viewer sources.
  for (const url of msg.attachments || []) {
    try {
      await startDownload({ url, filename: `${ROOT}/${dir}/${basename(url)}` });
    } catch (err) {
      console.warn("attachment failed to start:", url, err);
    }
  }

  // 3. The dataroom download buttons were clicked by the content script (their
  //    downloads arrive via the router) and any deck viewer tab is being
  //    printed by the tab watcher. Wait for everything to settle, then write
  //    job.json LAST.
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
  console.log(`[angel-memos] done "${active.company}": ${count} file(s) saved`);
  active = null;
  return count;
}

// Resolve once no download/tab activity for QUIET_MS with nothing pending and
// no viewer tab still loading — but never before MIN_RUN_MS (slow dataroom
// fetches / viewer loads need time) nor after MAX_RUN_MS (stuck-work backstop).
function waitForQuiescence() {
  return new Promise((resolve) => {
    const check = () => {
      if (!active) return resolve();
      const now = Date.now();
      const sinceRun = now - active.startedAt;
      const idle = now - active.lastActivity;
      if (sinceRun > MAX_RUN_MS) return resolve();
      const quiet =
        active.pending.size === 0 && active.viewerTabs.size === 0 && idle > QUIET_MS;
      if (sinceRun > MIN_RUN_MS && quiet) return resolve();
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
  return /angellist\.com/i.test(hay);
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
