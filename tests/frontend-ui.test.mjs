// Exercise the actual browser module with a small DOM and controllable network/clock.
// No browser dependency or server documents are needed for admission UI checks.
import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { setImmediate } from "node:timers/promises";

class Element {
  constructor() {
    this.children = [];
    this.listeners = new Map();
    this.classList = { toggle() {}, add() {}, remove() {} };
    this.value = "";
  }
  addEventListener(name, listener) { this.listeners.set(name, listener); }
  setAttribute() {}
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  click() {}
  remove() {}
  async fire(name, event = {}) {
    await this.listeners.get(name)?.(event);
    // Input handlers intentionally launch the async operation without awaiting it.
    for (let i = 0; i < 10; i++) await setImmediate();
  }
}

test("429 pauses uploads and fixes across documents, keeps reads/downloads, and never retries POST", async () => {
  const elements = new Map();
  const get = id => {
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
  };
  const saved = new Map();
  for (const key of ["document", "window", "location", "history", "localStorage", "fetch", "setTimeout", "clearTimeout", "Date"]) {
    saved.set(key, Object.getOwnPropertyDescriptor(globalThis, key));
  }
  const RealDate = Date;
  let now = 100000;
  let sequence = 0;
  const timers = new Map();
  const requests = [];
  const documents = new Map();
  let uploads = 0;
  const pageLocation = { href: "http://localhost/" };
  const json = (data, status = 200, headers = {}) => new Response(JSON.stringify(data), { status, headers });
  const file = () => new File(["generated test data"], "test.docx");
  const chooseFile = () => get("file-input").fire("change", { target: { files: [file()], value: "" } });
  const select = key => get("document-select").fire("change", { target: { value: key } });

  try {
    Object.assign(globalThis, {
      document: {
        body: new Element(), getElementById: get,
        createElement: () => new Element(), createTextNode: text => text,
        querySelectorAll: () => [],
      },
      window: { addEventListener() {} },
      location: pageLocation,
      history: { replaceState(_, __, url) { pageLocation.href = String(url); } },
      localStorage: { getItem: () => null, setItem() {} },
      Date: class extends RealDate { static now() { return now; } },
      setTimeout(callback, delay) { const id = ++sequence; timers.set(id, { callback, at: now + delay }); return id; },
      clearTimeout(id) { timers.delete(id); },
      async fetch(path, init = {}) {
        requests.push({ path, method: init.method || "GET" });
        if (path === "/api/session") return json({ csrf_token: "test-token", max_upload_bytes: 20971520, max_concurrent_jobs: 2 });
        if (path === "/api/documents") {
          uploads++;
          if (uploads >= 3) return json({ error: { code: "rate_limited", message: "Слишком много запросов" } }, 429, { "Retry-After": "3600" });
          const data = { document_id: `test-${uploads}`, filename: `test-${uploads}.docx`,
            state: "checked", current_version: "original", checker_version: "1", ruleset_version: "1",
            report: { standard: "СТП 01–2017", findings: [] }, warnings: [], limitations: [], download_url: null };
          documents.set(data.document_id, data);
          return json(data, 201);
        }
        const id = path.split("/")[3];
        const data = documents.get(id);
        assert.ok(data, `Unexpected request ${path}`);
        if (path.endsWith("/download")) return new Response("corrected test file");
        if (path.endsWith("/fix")) {
          Object.assign(data, { state: "fixed", current_version: "corrected", download_url: `/api/documents/${id}/download` });
        }
        return json(data);
      },
    });
    const source = (await readFile(new URL("../frontend/assets/app.js", import.meta.url), "utf8"))
      .replace('"./report.mjs"', JSON.stringify(new URL("../frontend/assets/report.mjs", import.meta.url).href));
    await import("data:text/javascript;base64," + Buffer.from(source).toString("base64"));
    await chooseFile();
    await select("upload-1");
    await get("fix").fire("click");
    await chooseFile();
    await chooseFile(); // This request receives the rate limit.
    assert.equal(get("file-input").disabled, true);
    assert.match(get("document-message").textContent, /Слишком много запросов/);
    assert.match(get("document-message").textContent, /3600/);

    await select("upload-2");
    assert.equal(get("fix").disabled, true);
    const posts = requests.filter(item => item.method === "POST").length;
    // Also invoke handlers directly: disabled buttons alone must not be the guard.
    await get("fix").fire("click");
    await get("upload-zone").fire("drop", { preventDefault() {}, dataTransfer: { files: [file()] } });
    assert.equal(requests.filter(item => item.method === "POST").length, posts);
    await get("refresh").fire("click");
    assert.equal(requests.at(-1).path, "/api/documents/test-2");
    await select("upload-1");
    assert.equal(get("download").disabled, false);
    await get("download").fire("click");
    assert.equal(requests.at(-1).path, "/api/documents/test-1/download");

    now += 3600021;
    const due = [...timers].filter(([, timer]) => timer.at <= now);
    for (const [id, timer] of due) { timers.delete(id); timer.callback(); }
    assert.equal(requests.filter(item => item.method === "POST").length, posts);
    assert.equal(get("file-input").disabled, false);
    assert.equal(get("global-message").textContent, "");
    await select("upload-2");
    assert.equal(get("fix").disabled, false);
  } finally {
    for (const [key, descriptor] of saved) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else delete globalThis[key];
    }
  }
});
