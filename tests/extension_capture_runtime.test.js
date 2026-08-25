const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const repoRoot = path.resolve(__dirname, "..");
const backgroundSource = fs.readFileSync(
  path.join(repoRoot, "extension", "background.js"),
  "utf8"
);
const contentSource = fs.readFileSync(
  path.join(repoRoot, "extension", "content.js"),
  "utf8"
);

function event() {
  const listeners = [];
  return {
    listeners,
    addListener(listener) {
      listeners.push(listener);
    },
  };
}

function loadBackground(overrides = {}) {
  const stored = {};
  const downloads = [];
  const chrome = {
    runtime: {
      id: "angel-memos-test",
      onMessage: event(),
      getPlatformInfo: async () => ({}),
    },
    downloads: {
      onDeterminingFilename: event(),
      onCreated: event(),
      onChanged: event(),
      download: async (options) => {
        downloads.push(options);
        return downloads.length;
      },
      search: async () => [],
    },
    tabs: {
      onCreated: event(),
      onUpdated: event(),
      remove: async () => {},
    },
    alarms: {
      onAlarm: event(),
      create: async () => {},
      clear: async () => true,
    },
    storage: {
      session: {
        get: async (key) => ({ [key]: stored[key] }),
        set: async (values) => Object.assign(stored, values),
        remove: async (key) => delete stored[key],
      },
    },
    debugger: {
      attach: async () => {},
      detach: async () => {},
      sendCommand: async () => ({ data: "pdf" }),
    },
  };
  for (const [group, values] of Object.entries(overrides)) {
    Object.assign(chrome[group], values);
  }

  const context = {
    chrome,
    URL,
    Date,
    Promise,
    Set,
    Map,
    console: { log() {}, warn() {} },
    btoa,
    unescape,
    encodeURIComponent,
    clearTimeout() {},
    setTimeout(callback) {
      queueMicrotask(callback);
      return 1;
    },
  };
  vm.createContext(context);
  vm.runInContext(
    `${backgroundSource}\n;globalThis.__hooks = {\n` +
      "  arm, withArmLock, ensureJobMarker, startDownload,\n" +
      "  getActive: () => active, setActive: (value) => { active = value; }\n" +
      "};",
    context
  );
  return { chrome, context, downloads, stored, hooks: context.__hooks };
}

function sendMessage(chrome, msg, tabId) {
  return new Promise((resolve) => {
    const keepAlive = chrome.runtime.onMessage.listeners[0](
      msg,
      { tab: { id: tabId } },
      resolve
    );
    assert.equal(keepAlive, true);
  });
}

test("concurrent arm messages acquire one capture atomically", async () => {
  let releaseFirstGet;
  let getCount = 0;
  const { chrome, hooks } = loadBackground({
    storage: {
      session: {
        get: async () => {
          getCount += 1;
          if (getCount === 1) {
            return new Promise((resolve) => {
              releaseFirstGet = () => resolve({});
            });
          }
          return {};
        },
        set: async () => {},
        remove: async () => {},
      },
    },
  });

  const first = sendMessage(chrome, { kind: "am-arm", company: "Acme" }, 10);
  const second = sendMessage(chrome, { kind: "am-arm", company: "Beta" }, 20);
  await new Promise((resolve) => setImmediate(resolve));
  releaseFirstGet();
  const replies = await Promise.all([first, second]);

  assert.equal(replies.filter((reply) => reply.ok).length, 1);
  assert.match(replies.find((reply) => !reply.ok).error, /capture already running/);
  assert.equal(hooks.getActive().company, "Acme");
  assert.equal(hooks.getActive().tabId, 10);
});

test("punctuation-only company names are rejected at the background boundary", async () => {
  const { chrome, hooks } = loadBackground();

  const reply = await sendMessage(
    chrome,
    { kind: "am-arm", company: "----" },
    10
  );

  assert.equal(reply.ok, false);
  assert.match(reply.error, /valid company name/);
  assert.equal(hooks.getActive(), null);
});

test("page-owned downloads are never routed into the active company", () => {
  const { chrome, hooks } = loadBackground();
  hooks.setActive({ dir: "Acme" });
  let suggestion = "not-called";

  chrome.downloads.onDeterminingFilename.listeners[0](
    {
      url: "https://files.amazonaws.com/other-company-deck.pdf",
      filename: "other-company-deck.pdf",
    },
    (value) => {
      suggestion = value;
    }
  );

  assert.equal(suggestion, undefined);
});

test("job marker retry reconciles the id and keeps a deterministic filename", async () => {
  const states = new Map([
    [41, ["interrupted"]],
    [1, ["in_progress", "complete"]],
  ]);
  const { chrome, downloads, hooks } = loadBackground({
    downloads: {
      search: async ({ id }) => {
        const sequence = states.get(id) || [];
        const state = sequence.length > 1 ? sequence.shift() : sequence[0];
        return state ? [{ id, state }] : [];
      },
    },
  });
  hooks.setActive({
    company: "Acme",
    dir: "Acme",
    tier: "quick",
    sourceUrl: "https://example.test/acme",
    tabId: 10,
    startedAt: Date.now(),
    lastActivity: Date.now(),
    pending: new Set(),
    seen: new Set(),
    viewerTabs: new Set(),
    completed: 2,
    deckCaptured: true,
    finalizing: true,
    jobDownloadId: 41,
    jobTerminal: false,
  });

  const markerUrl = "data:application/json;base64,e30=";
  await hooks.ensureJobMarker(markerUrl);

  assert.equal(downloads.length, 1);
  assert.equal(downloads[0].filename, "angel-memos/Acme/job.json");
  assert.equal(downloads[0].conflictAction, "overwrite");
  assert.equal(hooks.getActive().jobDownloadId, 1);

  let suggestion;
  chrome.downloads.onDeterminingFilename.listeners[0](
    { url: markerUrl, byExtensionId: chrome.runtime.id },
    (value) => {
      suggestion = value;
    }
  );
  assert.equal(suggestion.filename, "angel-memos/Acme/job.json");
  assert.equal(suggestion.conflictAction, "overwrite");
});

test("company cleanup rejects the sentinel and repairs generic title prefixes", () => {
  const start = contentSource.indexOf("  function cleanupCompanyCandidate");
  const end = contentSource.indexOf("\n})();", start);
  assert.ok(start >= 0 && end > start);
  const context = {};
  vm.createContext(context);
  vm.runInContext(
    "const GENERIC_COMPANY_HEADINGS = new Set([\"overview\", \"investment memo\"]);\n" +
      contentSource.slice(start, end) +
      "\n;globalThis.cleanCompany = cleanupCompanyCandidate;",
    context
  );

  assert.equal(context.cleanCompany("Company name required"), "");
  assert.equal(context.cleanCompany("----"), "");
  assert.equal(context.cleanCompany("Overview | Acme"), "Acme");
  assert.equal(context.cleanCompany("Investment Memo Together AI"), "Together AI");
});
