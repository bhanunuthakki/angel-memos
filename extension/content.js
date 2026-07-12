// Angel Memos Capture — content script.
// Injects a small floating panel on AngelList pages. On click it scrapes
// the company name + attachment links and asks the background worker to
// download everything into Downloads/angel-memos/<Company>/, writing
// job.json last as the "drop is complete" marker for `angel-memos watch`.

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

  const capture = (tier) => {
    const company = guessCompanyName();
    const confirmed = window.prompt("Company folder name:", company);
    if (!confirmed) return;
    status.textContent = "Capturing…";
    chrome.runtime.sendMessage(
      {
        kind: "angel-memos-capture",
        company: confirmed.trim(),
        tier,
        sourceUrl: location.href,
        attachments: collectAttachmentLinks(),
      },
      (reply) => {
        if (chrome.runtime.lastError) {
          status.textContent = "Error: " + chrome.runtime.lastError.message;
          return;
        }
        status.textContent = reply && reply.ok
          ? `Saved ${reply.count} file(s) ✓`
          : "Failed: " + (reply ? reply.error : "no reply");
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
    // Deal pages usually lead with the company in the first h1; fall back
    // to the document title's first segment.
    const h1 = document.querySelector("h1");
    if (h1 && h1.textContent.trim()) return cleanup(h1.textContent);
    return cleanup(document.title.split(/[|\-–—]/)[0]);
  }

  function cleanup(text) {
    return text.replace(/\s+/g, " ").trim().slice(0, 80);
  }

  function collectAttachmentLinks() {
    // Attachment/data-room links: same-page anchors to PDFs or AL's
    // attachment endpoints. Deduplicated; signed S3 URLs pass through.
    const urls = new Set();
    for (const a of document.querySelectorAll("a[href]")) {
      const href = a.href;
      if (!href || !href.startsWith("http")) continue;
      const lower = href.toLowerCase();
      if (
        lower.includes(".pdf") ||
        lower.includes("/attachments/") ||
        lower.includes("document") && lower.includes("download")
      ) {
        urls.add(href);
      }
    }
    return [...urls];
  }
})();
