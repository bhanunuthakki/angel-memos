// Angel Memos Capture — content script.
// Injects a floating panel on AngelList pages. Captures ONLY the two things
// the diligence pipeline needs into Downloads/angel-memos/<Company>/:
//   1. the AL memo   — the deal page itself, printed to PDF by the background;
//   2. the deck      — the ONE document row whose name is the pitch deck.
//
// Everything else in the dataroom (closing documents, disclaimers, etc.) is
// deliberately IGNORED. The deck is usually view-only (a clickable table cell
// with no download button), while the junk docs are the ones that DO have
// download buttons — so "download every button" grabbed exactly the wrong set.
// We instead find the deck row by name and either click its download control
// or open its viewer so the background can print it, like the AL memo.

(() => {
  if (document.getElementById("angel-memos-panel")) return;

  const PANEL_ID = "angel-memos-panel";
  const DECK_RE = /\b(pitch\s*deck|deck|presentation)\b/i;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  // Never let the UI hang: if the worker dies mid-capture the message port
  // closes (surfaced as lastError), and as a backstop we also time out.
  const send = (msg, timeoutMs = 90000) =>
    new Promise((resolve) => {
      let settled = false;
      const done = (v) => {
        if (!settled) {
          settled = true;
          resolve(v);
        }
      };
      const timer = setTimeout(
        () => done({ ok: false, error: "timed out waiting for background worker" }),
        timeoutMs
      );
      chrome.runtime.sendMessage(msg, (reply) => {
        clearTimeout(timer);
        if (chrome.runtime.lastError) done({ ok: false, error: chrome.runtime.lastError.message });
        else done(reply || { ok: false, error: "no reply" });
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

    // Step 1: arm the router BEFORE any deck download/viewer can fire.
    setStatus("Arming…");
    const ack = await send({
      kind: "am-arm", company: confirmed.trim(), tier, sourceUrl: location.href,
    });
    if (!ack.ok) { setStatus("Error arming: " + ack.error); return; }

    // Step 2: act ONLY on the deck. Returns { mode, urls }.
    const deck = captureDeck();
    if (deck.mode === "none") {
      setStatus("Capturing page (no deck found)…");
    } else if (deck.mode === "view") {
      setStatus("Opening deck…");
    } else {
      setStatus("Downloading deck…");
    }

    // Step 3: if the deck is view-only, give the viewer a moment and also try
    // to grab an in-page embedded PDF source (some viewers render an <iframe>).
    let attachments = deck.urls;
    if (deck.mode === "view") {
      await sleep(1800);
      attachments = [...attachments, ...collectEmbeddedPdfSrcs()];
    }

    // Step 4: background prints the deal page (+ any deck viewer tab), downloads
    // the deck url(s), waits for everything to settle, then writes job.json LAST.
    const reply = await send({ kind: "am-run", attachments, deckMode: deck.mode });
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

  // Capture ONLY the deck. Datarooms render documents in a <table>: the deck is
  // the row whose name matches DECK_RE. If that row has a download control we
  // click it (the background router files it into the folder); if it's
  // view-only (a clickable cell, no button) we click it to open the viewer so
  // the background prints it. Non-table pages fall back to a deck-named
  // link/button. Returns { mode: 'download'|'view'|'none', urls: string[] }.
  function captureDeck() {
    const cell = findDeckCell();
    if (cell) {
      const row = cell.closest("tr") || cell.parentElement;
      const dl =
        row &&
        row.querySelector('button[aria-label="Download" i], a[download], a[href$=".pdf" i]');
      if (dl) {
        dl.click();
        return { mode: "download", urls: [] };
      }
      // View-only deck cell (cursor:pointer, no download control).
      cell.click();
      return { mode: "view", urls: [] };
    }
    // Fallback for non-dataroom pages: a deck-named link or control.
    const ctrl = findDeckControl();
    if (!ctrl) return { mode: "none", urls: [] };
    const href = ctrl.getAttribute && ctrl.getAttribute("href");
    if (href && /^https?:/i.test(href) && /\.pdf(\?|$)/i.test(href)) {
      return { mode: "download", urls: [href] }; // background downloads it
    }
    ctrl.click();
    return { mode: "view", urls: [] };
  }

  // The deck's document-name cell: a short <td> whose text names a deck.
  function findDeckCell() {
    return (
      [...document.querySelectorAll("td")].find((td) => {
        const t = (td.textContent || "").trim();
        return t.length > 0 && t.length < 45 && DECK_RE.test(t);
      }) || null
    );
  }

  // Non-table fallback: a deck-named <a>/<button>/[role=button], shortest label
  // first (so we click the deck control, not a container mentioning "deck").
  function findDeckControl() {
    const inPanel = (el) => el.closest && el.closest("#" + PANEL_ID);
    const isDeck = (el) => {
      const label = (el.getAttribute("aria-label") || "") + " " + (el.textContent || "");
      return DECK_RE.test(label) && (el.textContent || "").trim().length < 40;
    };
    const candidates = [...document.querySelectorAll('a, button, [role="button"]')].filter(
      (el) => !inPanel(el) && isDeck(el)
    );
    candidates.sort((a, b) => (a.textContent || "").length - (b.textContent || "").length);
    return candidates[0] || null;
  }

  // In-page embedded PDF viewer: an <iframe>/<embed>/<object> whose source is an
  // https PDF/document URL (blob: sources are context-scoped and can't be
  // fetched by the background — those fall to the viewer-tab print path).
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
