// Angel Memos Capture — content script.
// Captures exactly TWO artifacts into Downloads/angel-memos/<Company>/:
//   1. AL memo — the deal page printed to PDF (panel hidden during print);
//   2. Deck    — the one dataroom row named like a pitch deck.
// Everything else in the dataroom (closing docs, disclaimers) is ignored.
//
// Deck mechanics (verified against a live dataroom): the deck row is usually
// VIEW-ONLY — a clickable <td> with no download control. Clicking it opens an
// in-page PSPDFKit overlay, and the page fetches the actual PDF from a signed
// S3 URL. That fetch shows up in resource timing, so we click the deck, poll
// performance entries for the document URL, and hand it to the background to
// download (chrome.downloads needs no CORS). Rows WITH download buttons are
// the junk docs — never clicked.
//
// Capture phases (each a message to the background):
//   am-arm        arm the download-router for this company
//   am-print-page print the deal page (panel hidden here — fixes panel-in-PDF)
//   am-deck-url   download the deck's signed S3 URL as "<Company> deck.pdf"
//   am-finalize   wait for downloads to settle, write job.json LAST

(() => {
  if (document.getElementById("angel-memos-panel")) return;

  const PANEL_ID = "angel-memos-panel";
  const DECK_RE = /\b(pitch\s*deck|deck|presentation)\b/i;
  const DOC_FETCH_TIMEOUT_MS = 15000; // deck PDF fetch must appear within this
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  // Belt-and-braces: even if the panel were visible during a print, print
  // media excludes it.
  const printCss = document.createElement("style");
  printCss.textContent = `@media print { #${PANEL_ID} { display: none !important; } }`;
  document.documentElement.appendChild(printCss);

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
    const companyName = confirmed.trim();

    // Phase 0: arm the router before anything can download.
    setStatus("Arming…");
    const ack = await send({
      kind: "am-arm", company: companyName, tier, sourceUrl: location.href,
    });
    if (!ack.ok) { setStatus("Error arming: " + ack.error); return; }

    // Phase 1: print the AL memo with the panel hidden and BEFORE the deck
    // overlay opens, so the memo PDF shows neither.
    panel.style.display = "none";
    const printed = await send({ kind: "am-print-page" }, 30000);
    panel.style.display = "";
    setStatus(printed.ok ? "Memo saved. Deck…" : "Memo print failed. Deck…");

    // Phase 2: the deck, and only the deck.
    const deckResult = await captureDeck(companyName);
    setStatus(deckResult.note);

    // Phase 3: finalize — background waits for downloads, writes job.json LAST.
    const reply = await send({ kind: "am-finalize" });
    const deckSuffix = deckResult.gotDeck ? "" : " (NO DECK — see console)";
    setStatus(reply.ok ? `Saved ${reply.count} file(s) ✓${deckSuffix}` : "Failed: " + reply.error);
  }

  panel.append(
    title,
    mkButton("Save + Quick Research", "quick"),
    mkButton("Save only", "none"),
    status
  );
  document.documentElement.appendChild(panel);

  // ---------------------------------------------------------------------------
  // Deck capture.
  // ---------------------------------------------------------------------------

  async function captureDeck(companyName) {
    const cell = findDeckCell();
    const control = cell || findDeckControl();
    if (!control) {
      console.warn("[angel-memos] no deck row/control found on this page");
      return { gotDeck: false, note: "No deck found…" };
    }

    // If the deck's own row has a download control, use it (router files it).
    const row = control.closest ? control.closest("tr") : null;
    const dl =
      row &&
      row.querySelector('button[aria-label="Download" i], a[download], a[href$=".pdf" i]');
    if (dl) {
      dl.click();
      return { gotDeck: true, note: "Downloading deck…" };
    }

    // View-only deck: click it open, then watch resource timing for the
    // document fetch (a non-image fetch from S3/AngelList file storage).
    setStatus("Opening deck…");
    performance.setResourceTimingBufferSize(4000);
    const sinceTs = performance.now();
    control.click();

    const url = await pollForDeckDocUrl(sinceTs);
    closeDeckOverlay();
    if (!url) {
      console.warn(
        "[angel-memos] deck viewer opened but no document fetch appeared in " +
          "resource timing within " + DOC_FETCH_TIMEOUT_MS + "ms — deck not captured"
      );
      return { gotDeck: false, note: "Deck URL not found…" };
    }
    const saved = await send({
      kind: "am-deck-url",
      url,
      name: `${companyName} deck.pdf`,
    });
    if (!saved.ok) {
      console.warn("[angel-memos] deck download failed:", saved.error);
      return { gotDeck: false, note: "Deck download failed…" };
    }
    return { gotDeck: true, note: "Deck saved…" };
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
  // first (so we hit the deck control, not a container mentioning "deck").
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

  // The deck document is fetched (by PSPDFKit's loader in the top document)
  // from file storage — observed shape: a `fetch` to an S3 bucket with a UUID
  // path and no file extension, signed via query string. Poll for the first
  // such entry that appeared after the deck click.
  function pollForDeckDocUrl(sinceTs) {
    const deadline = performance.now() + DOC_FETCH_TIMEOUT_MS;
    const isImageOrAsset = (pathname) =>
      /\.(png|jpe?g|gif|webp|svg|ico|css|js|woff2?|mp4)([?#]|$)/i.test(pathname);
    const check = () => {
      const entries = performance.getEntriesByType("resource");
      const candidates = entries.filter((e) => {
        if (e.startTime < sinceTs - 1000) return false;
        if (e.initiatorType !== "fetch" && e.initiatorType !== "xmlhttprequest") return false;
        let u;
        try {
          u = new URL(e.name);
        } catch {
          return false;
        }
        const fileHost =
          /\.amazonaws\.com$/i.test(u.hostname) || /cloudfront\.net$/i.test(u.hostname);
        return fileHost && !isImageOrAsset(u.pathname);
      });
      return candidates.length ? candidates[candidates.length - 1].name : null;
    };
    return new Promise((resolve) => {
      const tick = () => {
        const url = check();
        if (url) return resolve(url);
        if (performance.now() > deadline) return resolve(null);
        setTimeout(tick, 400);
      };
      setTimeout(tick, 600);
    });
  }

  // Close the PSPDFKit overlay so the page is back to normal after capture.
  function closeDeckOverlay() {
    const overlays = [...document.querySelectorAll("div")].filter((d) => {
      const s = getComputedStyle(d);
      return (
        s.position === "fixed" && parseInt(s.zIndex || "0", 10) > 100 &&
        d.offsetHeight > 300 && d.id !== PANEL_ID
      );
    });
    for (const overlay of overlays) {
      const close = overlay.querySelector(
        'button[aria-label*="close" i], [role="button"][aria-label*="close" i]'
      );
      if (close) {
        close.click();
        return;
      }
    }
    document.dispatchEvent(
      new KeyboardEvent("keydown", { key: "Escape", code: "Escape", bubbles: true })
    );
  }

  function guessCompanyName() {
    const h1 = document.querySelector("h1");
    if (h1 && h1.textContent.trim()) return cleanup(h1.textContent);
    return cleanup(document.title.split(/[|\-–—]/)[0]);
  }

  function cleanup(text) {
    return text.replace(/\s+/g, " ").trim().slice(0, 80);
  }
})();
