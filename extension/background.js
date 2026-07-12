// Angel Memos Capture — background service worker.
//
// Receives a capture request from the content script and:
//   1. Prints the deal page to PDF via the debugger API
//      -> angel-memos/<Company>/angellist - <Company>.pdf
//   2. Downloads every attachment link
//      -> angel-memos/<Company>/<original name>
//   3. After ALL downloads settle, writes job.json LAST — the watcher
//      treats job.json as the "drop is complete" marker, so order matters.
//
// Chrome extensions can only write under ~/Downloads; the `angel-memos
// watch` daemon moves the drop into the Drive Evaluation folder.

const ROOT = "angel-memos";

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || msg.kind !== "angel-memos-capture") return false;
  capture(msg, sender.tab)
    .then((count) => sendResponse({ ok: true, count }))
    .catch((err) => sendResponse({ ok: false, error: String(err) }));
  return true; // async sendResponse
});

async function capture(msg, tab) {
  const company = sanitize(msg.company);
  if (!company) throw new Error("empty company name");
  const dir = ROOT + "/" + company;

  const downloadIds = [];

  // 1. Page -> PDF (the AngelList memo itself).
  const pdfBase64 = await printPageToPdf(tab.id);
  downloadIds.push(
    await startDownload({
      url: "data:application/pdf;base64," + pdfBase64,
      filename: dir + "/angellist - " + company + ".pdf",
    })
  );

  // 2. Attachments (deduped by the content script).
  for (const url of msg.attachments || []) {
    try {
      downloadIds.push(
        await startDownload({ url, filename: dir + "/" + basename(url) })
      );
    } catch (err) {
      console.warn("attachment failed to start:", url, err);
    }
  }

  await Promise.all(downloadIds.map(waitForDownload));

  // 3. job.json LAST — completeness marker for the watcher.
  const job = {
    company: msg.company.trim(),
    tier: msg.tier === "quick" ? "quick" : "none",
    source_url: msg.sourceUrl || "",
  };
  const encoded = btoa(unescape(encodeURIComponent(JSON.stringify(job, null, 2))));
  const jobId = await startDownload({
    url: "data:application/json;base64," + encoded,
    filename: dir + "/job.json",
  });
  await waitForDownload(jobId);

  return downloadIds.length + 1;
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

function waitForDownload(downloadId) {
  return new Promise((resolve, reject) => {
    const listener = (delta) => {
      if (delta.id !== downloadId || !delta.state) return;
      if (delta.state.current === "complete") {
        chrome.downloads.onChanged.removeListener(listener);
        resolve();
      } else if (delta.state.current === "interrupted") {
        chrome.downloads.onChanged.removeListener(listener);
        reject(new Error("download " + downloadId + " interrupted"));
      }
    };
    chrome.downloads.onChanged.addListener(listener);
    // In case the download already finished before the listener attached.
    chrome.downloads.search({ id: downloadId }, (items) => {
      const item = items && items[0];
      if (!item) return;
      if (item.state === "complete") {
        chrome.downloads.onChanged.removeListener(listener);
        resolve();
      } else if (item.state === "interrupted") {
        chrome.downloads.onChanged.removeListener(listener);
        reject(new Error("download " + downloadId + " interrupted"));
      }
    });
  });
}

function sanitize(name) {
  // Windows-safe folder name: strip reserved characters and edge dots.
  return (name || "")
    .replace(/[<>:"/\\|?*]/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/\.+$/, "")
    .slice(0, 80);
}

function basename(url) {
  try {
    const path = new URL(url).pathname;
    const last = decodeURIComponent(path.split("/").filter(Boolean).pop() || "");
    const safe = sanitize(last);
    return safe || "attachment.pdf";
  } catch {
    return "attachment.pdf";
  }
}
