// Angel Memos Capture — content script.
// Injects a small floating panel on AngelList pages. On click it captures the
// deal into Downloads/angel-memos/<Company>/:
//   1. arms the background download-router for this company,
//   2. clicks the dataroom document download buttons (deck/docs are JS buttons
//      with aria-label="Download"/"Download all", NOT <a href> links),
//   3. if the deck is VIEW-ONLY (no download control), opens the deck viewer
//      so the background can print it to PDF like the AL memo,
//   4. hands any real <a href> attachments and embedded PDF viewer sources to
//      the background too,
//   5. background prints the deal page + any viewer tab, then writes job.json
//      LAST as the drop-completeness marker.

(() => {
  if (document.getElementById("angel-memos-panel")) return;

  const PANEL_ID = "angel-memos-panel";
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const send = (msg) =>
    new Promise((resolve) => {
      chrome.runtime.sendMessage(msg, (reply) => {
        if (chrome.runtime.lastError) resolve({ ok: false, error: chrome.runtime.lastError.message });
        else resolve(reply || { ok: false, error: "no reply" });
      });
    });

  const panel = document.createElement("div");
  panel.id = PANEL_ID;
  panel.style.cssText = [
    "position:fixed", "bottom:18px", "right:18px", "z-index:2147483647",
    "background:#111827", "color:#f9fafb", "border-radius:8px",
    "padding:10px 12px", "font:12px/1.4 system-ui,sans-serif",
    "box-shadow:0 4px 14px rgba(0,0,0,.35)", "display:flex",
    "flex-direction:column", "gap:6px", "min-width:190px",
  ].join(";");

  const title = document.createElement("div");
  title.textContent = "Angel Memos";
  title.style.cssText = "font-weight:700;letter-spacing:.04em";

  const status = document.createElement("div");
  status.style.cssText = "color:#9ca3af;min-height:14px";
  const setStatus = (msg) => { status.textContent = msg; };

  const mkButton = (label, tier) => {
    const btn = document.createElement("button");
    btn.textContent = label;
    btn.style.cssText = [
      "background:#2563eb", "color:#fff", "border:none", "border-radius:5px",
      "padding:6px 8px", "cursor:pointer", "font:inherit", "font-weight:600",
    ].join(";");
    if (tier === "none") btn.style.background = "#374151";
    btn.addEventListener("click", () => { void capture(tier); });
    return btn;
  };

  async function capture(tier) {
    const company = guessCompanyName();
    const confirmed = window.prompt("Company folder name:", company);
    if (!confirmed) return;

    // Step 1: arm the router BEFORE any page download can fire.
    setStatus("Arming…");
    const ack = await send({
      kind: "am-arm", company: confirmed.trim(), tier, sourceUrl: location.href,
    });
    if (!ack.ok) { setStatus("Error arming: " + ack.error); return; }

    // Step 2: click the dataroom document download buttons (deck + docs).
    const clicked = clickDataroomDownloads();

    // Step 3: if nothing obviously downloaded the deck, open the deck viewer
    // so the background can print it to PDF (the view-only-deck case).
    const deckOpened = openDeckViewer();

    // Step 4: give the viewer a moment to open, then gather anchor links and
    // any embedded PDF viewer sources.
    setStatus(clicked ? `Downloading ${clicked} doc(s)…` : "Capturing…");
    await sleep(deckOpened ? 1800 : 300);
    const attachments = [...collectAttachmentLinks(), ...collectEmbeddedPdfSrcs()];

    // Step 5: background prints the page (+ any viewer tab) and finalizes.
    const reply = await send({ kind: "am-run", attachments, docClicks: clicked, deckOpened });
    setStatus(reply.ok ? `Saved ${reply.count} file(s) ✓` : "Failed: " + reply.error);
  }

  panel.append(
    title,
    mkButton("Save + Quick Research", "quick"),
    mkButton("Save only", "none"),
    status
  );
  document.documentElement.appendChild(panel);

  function guessCompanyName() {
    const h1 = document.querySelector("h1");
    if (h1 && h1.textContent.trim()) return cleanup(h1.textContent);
    return cleanup(document.title.split(/[|\-–—]/)[0]);
  }

  function cleanup(text) {
    return text.replace(/\s+/g, " ").trim().slice(0, 80);
  }

  // Dataroom documents are download buttons (aria-label "Download"/"Download
  // all"), no href. Prefer "Download all" (grabs the deck + everything as a
  // zip the watcher unpacks); else click each per-document button. Returns
  // the count clicked.
  function clickDataroomDownloads() {
    const all = document.querySelector('button[aria-label="Download all" i]');
    if (all) {
      all.click();
      return 1;
    }
    const perDoc = [...document.querySelectorAll('button[aria-label="Download" i]')];
    for (const btn of perDoc) btn.click();
    return perDoc.length;
  }

  // View-only decks have no download control — they open in a viewer (a new
  // tab or an in-page embed). Click the deck control so the background can
  // capture the resulting PDF. Returns whether a deck control was clicked.
  function openDeckViewer() {
    const inPanel = (el) => el.closest && el.closest("#" + PANEL_ID);
    const looksLikeDeck = (el) => {
      const label = (el.getAttribute("aria-label") || "") + " " + (el.textContent || "");
      return /\bdeck\b|\bpitch deck\b|view deck/i.test(label);
    };
    const candidates = [
      ...document.querySelectorAll('a, button, [role="button"]'),
    ].filter((el) => !inPanel(el) && looksLikeDeck(el));
    // Shortest label first — avoids clicking a big container that merely
    // contains the word "deck".
    candidates.sort(
      (a, b) => (a.textContent || "").length - (b.textContent || "").length
    );
    if (candidates[0]) {
      candidates[0].click();
      return true;
    }
    return false;
  }

  // Real anchor attachments (standard deal pages that DO use links).
  function collectAttachmentLinks() {
    const urls = new Set();
    for (const a of document.querySelectorAll("a[href]")) {
      const href = a.href;
      if (!href || !href.startsWith("http")) continue;
      const lower = href.toLowerCase();
      if (
        lower.includes(".pdf") ||
        lower.includes("/attachments/") ||
        (lower.includes("document") && lower.includes("download"))
      ) {
        urls.add(href);
      }
    }
    return [...urls];
  }

  // Embedded PDF viewers: an <iframe>/<embed>/<object> whose source is an
  // https PDF/document URL. (blob: sources are context-scoped and can't be
  // fetched by the background — those fall to the viewer-tab print path.)
  function collectEmbeddedPdfSrcs() {
    const urls = new Set();
    for (const el of document.querySelectorAll("iframe[src], embed[src], object[data]")) {
      const src = el.getAttribute("src") || el.getAttribute("data") || "";
      if (!/^https?:/i.test(src)) continue;
      if (/\.pdf(\?|$)/i.test(src) || /document|attachment|dataroom|file/i.test(src)) {
        urls.add(src);
      }
    }
    return [...urls];
  }
})();
