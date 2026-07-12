// Angel Memos Capture — content script.
// Injects a small floating panel on AngelList pages. On click it captures
// the deal into Downloads/angel-memos/<Company>/:
//   1. arms the background download-router for this company,
//   2. clicks the dataroom's document download buttons (the deck + docs are
//      JS buttons with aria-label="Download"/"Download all", NOT <a href>
//      links — so they must be clicked, not scraped),
//   3. hands any real <a href> attachments to the background too,
//   4. background prints the page to PDF and writes job.json LAST.

(() => {
  if (document.getElementById("angel-memos-panel")) return;

  const panel = document.createElement("div");
  panel.id = "angel-memos-panel";
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

  const mkButton = (label, tier) => {
    const btn = document.createElement("button");
    btn.textContent = label;
    btn.style.cssText = [
      "background:#2563eb", "color:#fff", "border:none", "border-radius:5px",
      "padding:6px 8px", "cursor:pointer", "font:inherit", "font-weight:600",
    ].join(";");
    if (tier === "none") btn.style.background = "#374151";
    btn.addEventListener("click", () => capture(tier));
    return btn;
  };

  const setStatus = (msg) => { status.textContent = msg; };

  const capture = (tier) => {
    const company = guessCompanyName();
    const confirmed = window.prompt("Company folder name:", company);
    if (!confirmed) return;
    setStatus("Arming…");
    // Step 1: arm the background download-router BEFORE any page download
    // can fire, so button-triggered downloads get routed into the folder.
    chrome.runtime.sendMessage(
      { kind: "am-arm", company: confirmed.trim(), tier, sourceUrl: location.href },
      (ack) => {
        if (chrome.runtime.lastError || !ack || !ack.ok) {
          setStatus("Error arming: " + (chrome.runtime.lastError?.message || "no reply"));
          return;
        }
        // Step 2: click the dataroom document download buttons.
        const clicked = clickDataroomDownloads();
        // Step 3: also hand over any real anchor attachments.
        const attachments = collectAttachmentLinks();
        setStatus(clicked ? `Downloading ${clicked} doc(s)…` : "Capturing page…");
        // Step 4: background prints the page + finalizes (job.json last).
        chrome.runtime.sendMessage(
          { kind: "am-run", attachments, docClicks: clicked },
          (reply) => {
            if (chrome.runtime.lastError) {
              setStatus("Error: " + chrome.runtime.lastError.message);
              return;
            }
            setStatus(
              reply && reply.ok
                ? `Saved ${reply.count} file(s) ✓`
                : "Failed: " + (reply ? reply.error : "no reply")
            );
          }
        );
      }
    );
  };

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

  // AngelList datarooms expose each document (deck, closing docs, etc.) as a
  // button with aria-label "Download" or "Download all" — no href. Prefer
  // "Download all" (one click grabs the deck + everything, as a zip the
  // watcher unpacks); otherwise click each per-document button. Returns the
  // number of buttons clicked.
  function clickDataroomDownloads() {
    const all = document.querySelector('button[aria-label="Download all" i]');
    if (all) {
      all.click();
      return 1;
    }
    const perDoc = [
      ...document.querySelectorAll('button[aria-label="Download" i]'),
    ];
    for (const btn of perDoc) btn.click();
    return perDoc.length;
  }

  // Real anchor attachments (for standard deal pages that DO use links).
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
})();
