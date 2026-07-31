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
const POLL_MS = 500; // quiescence poll; also the service-worker keepalive beat

// --- Crash-safety (v0.4.3) ---------------------------------------------------
// job.json is the drop-completeness marker, written LAST. It used to live or
// die with the service worker's in-memory `active`: an MV3 worker is killed
// after ~30s idle and setTimeout callbacks do NOT reset that idle timer, so a
// capture that sat in waitForQuiescence lost its state and the drop was left
// with the PDFs but no job.json — invisible to `angel-memos ingest` forever.
//
// Two mechanisms close that hole:
//   1. State is mirrored into chrome.storage.session, so a restarted worker
//      can pick the capture back up instead of throwing "capture not armed".
//   2. An alarm (alarms DO wake a terminated worker; timers do not) acts as
//      the backstop that finalizes an abandoned capture.
const STATE_KEY = "capture";
const GUARD_ALARM = "am-finalize-guard";
// Comfortably past MAX_RUN_MS and the content script's 60s deck fetch, so the
// guard only ever fires for a capture that genuinely died.
const GUARD_DELAY_MINUTES = 2;
// Never resurrect a capture older than this; a stale record must not attach
// itself to an unrelated later drop.
const CAPTURE_TTL_MS = 10 * 60 * 1000;

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
  active.seen.add(item.id);
  active.lastActivity = Date.now();
  persist();
});

chrome.downloads.onChanged.addListener((delta) => {
  if (!active || !delta.state) return;
  const state = delta.state.current;
  if (state === "complete" || state === "interrupted") {
    if (active.pending.delete(delta.id) && state === "complete") active.completed += 1;
    active.lastActivity = Date.now();
    persist();
  }
});

// --- Viewer-tab fallback: a tab opened by the capture tab gets printed. ------
// (The common dataroom deck is an in-page overlay handled via am-deck-url;
// this path only fires for decks that open in a real new tab.)

chrome.tabs.onCreated.addListener((tab) => {
  if (!active || tab.openerTabId !== active.tabId || tab.id == null) return;
  active.viewerTabs.add(tab.id);
  active.lastActivity = Date.now();
  persist();
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
    if (active) {
      active.deckCaptured = true;
      await persist();
    }
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
  // Every phase after am-arm runs in a worker that may have been restarted
  // since, so rehydrate `active` before dispatching rather than failing with
  // "capture not armed" and stranding the drop.
  const respond = (run) => {
    Promise.resolve()
      .then(async () => {
        if (!active && msg.kind !== "am-arm") active = await restore();
        return run();
      })
      .then((value) => sendResponse(Object.assign({ ok: true }, value || {})))
      .catch((err) => sendResponse({ ok: false, error: String(err) }));
    return true; // async sendResponse
  };
  switch (msg.kind) {
    case "am-arm":
      return respond(() => arm(msg, sender.tab));
    case "am-print-page":
      return respond(() => printMemo(sender.tab));
    case "am-deck-url":
      return respond(() => downloadDeckUrl(msg));
    case "am-finalize":
      return respond(() => finalize());
    case "am-run": // stale content script (pre-0.4) — print + finalize
      console.warn("[angel-memos] legacy am-run — reload the AngelList tab");
      return respond(() => printMemo(sender.tab).then(() => finalize()));
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
    seen: new Set(),
    viewerTabs: new Set(),
    completed: 0,
    deckCaptured: false,
    finalizing: false,
  };
  await persist();
  // Arm the backstop before anything can fail: from here on, this capture
  // gets a job.json even if the worker never survives to write one normally.
  await chrome.alarms.create(GUARD_ALARM, { delayInMinutes: GUARD_DELAY_MINUTES });
  console.log(`[angel-memos] armed for "${company}" -> ${ROOT}/${dir}/`);
}

// --- Persistence + crash recovery. -------------------------------------------

function persist() {
  if (!active) return Promise.resolve();
  const snapshot = {
    company: active.company,
    dir: active.dir,
    tier: active.tier,
    sourceUrl: active.sourceUrl,
    tabId: active.tabId,
    startedAt: active.startedAt,
    lastActivity: active.lastActivity,
    // Sets aren't structured-cloneable into storage; round-trip as arrays.
    pending: [...active.pending],
    seen: [...active.seen],
    viewerTabs: [...active.viewerTabs],
    completed: active.completed,
    deckCaptured: active.deckCaptured,
    finalizing: active.finalizing,
  };
  return chrome.storage.session
    .set({ [STATE_KEY]: snapshot })
    .catch((err) => console.warn("[angel-memos] persist failed:", err));
}

async function restore() {
  let raw;
  try {
    raw = (await chrome.storage.session.get(STATE_KEY))[STATE_KEY];
  } catch (err) {
    console.warn("[angel-memos] restore failed:", err);
    return null;
  }
  if (!raw) return null;
  if (Date.now() - raw.startedAt > CAPTURE_TTL_MS) {
    await clearPersisted();
    return null;
  }
  return Object.assign({}, raw, {
    pending: new Set(raw.pending || []),
    seen: new Set(raw.seen || []),
    viewerTabs: new Set(raw.viewerTabs || []),
  });
}

async function clearPersisted() {
  await chrome.storage.session.remove(STATE_KEY).catch(() => {});
  await chrome.alarms.clear(GUARD_ALARM).catch(() => {});
}

// The backstop. Alarms wake a terminated service worker; setTimeout does not.
// If this fires, the capture never finalized normally — write job.json from
// the persisted state so the drop becomes ingestible.
chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name !== GUARD_ALARM) return;
  if (!active) active = await restore();
  if (!active || active.finalizing) {
    await clearPersisted();
    return;
  }
  console.warn(`[angel-memos] guard fired — finalizing abandoned "${active.company}"`);
  // Downloads have long since settled by now; don't re-wait for quiescence.
  active.pending.clear();
  active.viewerTabs.clear();
  try {
    await finalize();
  } catch (err) {
    console.warn("[angel-memos] guard finalize failed:", err);
    await clearPersisted();
  }
});

async function printMemo(tab) {
  if (!active) throw new Error("capture not armed");
  if (!tab || tab.id == null) throw new Error("no sender tab");
  const b64 = await withTimeout(printPageToPdf(tab.id), PRINT_TIMEOUT_MS, "page print");
  // Sanitize the leaf name: a raw company name with an illegal char (":", "?",
  // etc.) makes chrome.downloads silently reject the memo, so the drop lands
  // with NO angellist*.pdf and diligence can't run.
  const memoLeaf = sanitizeFile(`angellist - ${active.company}`);
  await startDownload({
    url: "data:application/pdf;base64," + b64,
    filename: `${ROOT}/${active.dir}/${memoLeaf}.pdf`,
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
  active.deckCaptured = true;
  await persist();
  console.log(`[angel-memos] deck download started: ${name}`);
}

async function finalize() {
  if (!active) throw new Error("capture not armed");
  // The guard alarm and an am-finalize message can both land; job.json must
  // be written exactly once.
  if (active.finalizing) return { count: active.completed };
  active.finalizing = true;
  active.lastActivity = Date.now();
  await persist();
  await waitForQuiescence();

  const job = {
    // Write the sanitized folder name so job.company and the drop/Evaluation
    // folder always agree (ingest re-sanitizes defensively regardless).
    company: active.dir,
    tier: active.tier,
    source_url: active.sourceUrl,
    // Observability for the watcher; ingest still derives missing_deck from the
    // actual files, so this is a hint, not the source of truth.
    deck_captured: active.deckCaptured,
    pending_at_finalize: active.pending.size,
  };
  const encoded = btoa(unescape(encodeURIComponent(JSON.stringify(job, null, 2))));
  await startDownload({
    url: "data:application/json;base64," + encoded,
    filename: `${ROOT}/${active.dir}/job.json`,
  });

  const count = active.completed;
  console.log(`[angel-memos] done "${active.company}": ${count} file(s) saved`);
  active = null;
  await clearPersisted();
  return { count };
}

// Resolve once no download/tab activity for QUIET_MS with nothing pending and
// no viewer tab still loading — bounded below by MIN_RUN_MS and above by
// MAX_RUN_MS so a stuck download can never hang the capture.
function waitForQuiescence() {
  const startedWaiting = Date.now();
  return new Promise((resolve) => {
    const check = async () => {
      if (!active) return resolve();
      // Reconcile against the download manager instead of trusting that every
      // completion delta was observed. A missed onChanged used to leave an id
      // stuck in `pending` forever, forcing the full MAX_RUN_MS wait — which
      // is precisely the window in which the worker got killed.
      // The API call doubles as the keepalive: each chrome.* call resets the
      // ~30s idle timer, whereas a bare setTimeout chain does not.
      await reconcilePending();
      if (!active) return resolve();
      const now = Date.now();
      const waited = now - startedWaiting;
      const idle = now - active.lastActivity;
      if (waited > MAX_RUN_MS) return resolve();
      const quiet =
        active.pending.size === 0 && active.viewerTabs.size === 0 && idle > QUIET_MS;
      if (waited > MIN_RUN_MS && quiet) return resolve();
      setTimeout(check, POLL_MS);
    };
    setTimeout(check, POLL_MS);
  });
}

// Drop any pending id the download manager reports as settled.
async function reconcilePending() {
  if (!active || active.pending.size === 0) {
    // Still touch a chrome API so the idle timer resets during a quiet wait.
    await chrome.runtime.getPlatformInfo().catch(() => {});
    return;
  }
  for (const id of [...active.pending]) {
    let item;
    try {
      item = (await chrome.downloads.search({ id }))[0];
    } catch {
      continue;
    }
    // A vanished record can never produce a completion event; treat it as
    // settled rather than waiting on it forever.
    if (!item || item.state === "complete" || item.state === "interrupted") {
      if (active.pending.delete(id) && item && item.state === "complete") {
        active.completed += 1;
      }
    }
  }
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
