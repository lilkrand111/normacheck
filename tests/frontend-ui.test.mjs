// State transitions used by the UI; all documents and network responses are synthetic.
import test from 'node:test';
import assert from 'node:assert/strict';
import { DocumentStore, isBusy } from '../frontend/assets/documents.mjs';
import { DocumentAPI } from '../frontend/assets/api.mjs';
import { reportSelection } from '../frontend/assets/views.mjs';

const findings = [
  { rule_id: 'F03', status: 'error', actual: '1,5', expected: '1,0', standard_reference: 'Уточнение', locations: [{ fragment_start: 'Первый абзац' }] },
  { rule_id: 'F02', status: 'review', actual: 'Шрифт', expected: 'Проверить', standard_reference: '2.1', locations: [] },
];
const data = (id = 'one', version = 'original') => ({ document_id: id, filename: `${id}.docx`, state: version === 'original' ? 'checked' : 'fixed', current_version: version, checker_version: '1', ruleset_version: '1', report: { standard: 'СТП 01–2017', findings: structuredClone(findings) }, warnings: [], limitations: [], download_url: version === 'corrected' ? `/api/documents/${id}/download` : null });
const file = () => new File(['synthetic'], 'test.docx');
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
function setup(overrides = {}, storage) {
  const memory = new Map();
  const api = { session: { max_upload_bytes: 1024, max_concurrent_jobs: 2 }, async connect() { return this.session; }, async upload() { return data(); }, async get(id) { return data(id); }, async fix(id) { return data(id, 'corrected'); }, ...overrides };
  return new DocumentStore({ api, storage: storage || { getItem: key => memory.get(key), setItem: (key, value) => memory.set(key, value) } });
}

test('parallel uploads respect admission; completion does not replace another document', async () => {
  const first = deferred(), second = deferred(); let calls = 0;
  const store = setup({ upload: () => (++calls === 1 ? first : second).promise });
  const a = store.upload(file()), b = store.upload(file());
  assert.equal(store.activeCount, 2);
  assert.throws(() => store.upload(file()), /обработчики заняты/);
  second.resolve(data('two')); await b.done;
  assert.equal(b.data.document_id, 'two'); assert.equal(isBusy(a), true);
  first.resolve(data('one')); await a.done;
  assert.equal(store.documents.size, 2); assert.equal(a.data.document_id, 'one');
  assert.equal(store.activeCount, 0); store.dispose();
});

test('429/503 pause all new mutations, allow reads, and never replay POST after time expires', async () => {
  for (const status of [429, 503]) {
    let uploads = 0, fixes = 0, reads = 0;
    const store = setup({ async upload() { uploads++; throw Object.assign(new Error('Ограничение сервера'), { status, retryAfter: 3600 }); }, async get(id) { reads++; return data(id); }, async fix() { fixes++; return data('one', 'corrected'); } });
    try {
      const checked = store.entry('one', 'one.docx'); await store.adopt(checked, data());
      const failed = store.upload(file()); await failed.done;
      assert.equal(store.paused, true); assert.match(failed.error, /3600/);
      assert.throws(() => store.upload(file()), /приостановлены/);
      await store.fix(checked); assert.equal(fixes, 0);
      await store.refresh(checked); assert.equal(reads, 1);
      store.pausedUntil = Date.now() - 1;
      assert.equal(store.admissionError(), ''); assert.equal(uploads, 1); assert.equal(fixes, 0);
      await store.fix(checked); assert.equal(fixes, 1);
    } finally { store.dispose(); }
  }
});

test('manual marks survive the same report and failed fix, but reset for the corrected report', async () => {
  let shouldFail = true;
  const store = setup({ async fix() { if (shouldFail) throw new TypeError('offline'); return data('one', 'corrected'); } });
  const entry = store.entry('one', 'one.docx'); await store.adopt(entry, data());
  store.mark(entry, 0, true); entry.filters.query = 'Первый'; entry.selectedFinding = 0;
  await store.fix(entry);
  assert.deepEqual([...entry.marks], [0]); assert.equal(entry.filters.query, 'Первый'); assert.match(entry.error, /Соединение/);
  shouldFail = false; await store.fix(entry);
  assert.equal(entry.data.current_version, 'corrected'); assert.equal(entry.marks.size, 0); assert.equal(entry.filters.query, '');
  assert.equal(entry.data.download_url, '/api/documents/one/download'); store.dispose();
});

test('fix preflight prevents duplicate mutation when another tab has finished', async () => {
  let posts = 0;
  const store = setup({ async get() { return data('one', 'corrected'); }, async fix() { posts++; } });
  const entry = store.entry('one', 'one.docx'); await store.adopt(entry, data());
  await store.fix(entry); assert.equal(posts, 0); assert.equal(entry.data.current_version, 'corrected'); store.dispose();
});

test('uncertain upload is retained for explicit retry and is never repeated automatically', async () => {
  let calls = 0;
  const store = setup({ async upload() { calls++; throw new TypeError('offline'); } });
  const entry = store.upload(file()); await entry.done;
  assert.match(entry.error, /мог сохраниться/); assert.match(entry.error, /ещё одну копию/);
  assert.ok(entry.file); assert.equal(calls, 1); assert.equal(isBusy(entry), false); store.dispose();
});

test('missing access clears private report and marks; broken storage keeps marks in memory', async () => {
  const store = setup({}, { getItem() { throw new Error('denied'); }, setItem() { throw new Error('denied'); } });
  const entry = store.entry('one', 'one.docx'); await store.adopt(entry, data());
  store.mark(entry, 0, true); assert.equal(entry.storageUnavailable, true); assert.equal(entry.marks.size, 1);
  await store.adopt(entry, data()); assert.equal(entry.marks.size, 1);
  await store.error(entry, { status: 404 }); assert.equal(entry.data, null); assert.equal(entry.marks.size, 0);
  assert.match(entry.error, /не означает удаления/); store.dispose();
});

test('empty report, no search matches, and hidden matches remain distinct; selection follows visibility', async () => {
  const store = setup(), entry = store.entry('one', 'one.docx'); await store.adopt(entry, data());
  assert.equal(reportSelection(entry).visible.length, 2); assert.equal(entry.selectedFinding, 0);
  store.mark(entry, 0, true); entry.filters.hideDone = true;
  assert.equal(reportSelection(entry).visible.length, 1); assert.equal(entry.selectedFinding, 1);
  entry.filters.category = 'F03'; assert.equal(reportSelection(entry).hiddenByMarks, true); assert.equal(entry.selectedFinding, null);
  entry.filters.query = 'missing'; assert.equal(reportSelection(entry).hiddenByMarks, false);
  await store.adopt(entry, { ...data(), report: { standard: 'СТП', findings: [] } });
  assert.equal(reportSelection(entry).findings.length, 0); store.dispose();
});

test('API uses same-origin cookie/CSRF, multipart upload and only the server-approved download URL', async () => {
  const originalFetch = globalThis.fetch, calls = [];
  globalThis.fetch = async (path, init = {}) => {
    calls.push({ path, init });
    if (path.endsWith('/download')) return new Response('copy', { headers: { 'Content-Disposition': "attachment; filename*=UTF-8''%D0%BA%D0%BE%D0%BF%D0%B8%D1%8F.docx" } });
    return new Response(JSON.stringify(path === '/api/session' ? { csrf_token: 'token' } : data()));
  };
  try {
    const api = new DocumentAPI(); await api.connect(); await api.upload(file()); await api.fix('one');
    for (const call of calls) assert.equal(call.init.credentials, 'same-origin');
    for (const call of calls.filter(c => c.init.method === 'POST')) assert.equal(call.init.headers.get('X-CSRF-Token'), 'token');
    assert.ok(calls[1].init.body instanceof FormData); assert.equal(calls[1].init.headers.has('Content-Type'), false);
    assert.equal((await api.download(data('one', 'corrected'))).name, 'копия.docx');
    const count = calls.length;
    await assert.rejects(api.download({ ...data(), download_url: 'https://example.com/file' }), /недоступна/);
    assert.equal(calls.length, count);
  } finally { globalThis.fetch = originalFetch; }
});

test('actual app separates file choice from POST, preserves the selected document and restores URLs', async () => {
  const { readFile } = await import('node:fs/promises');
  const { setImmediate } = await import('node:timers/promises');
  const saved = new Map(['document', 'window', 'location', 'history', 'fetch', 'setTimeout', 'clearTimeout'].map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  const elements = new Map();
  class Element {
    constructor() { this.children = []; this.listeners = new Map(); this.dataset = {}; this.value = ''; this.hidden = false; this.scrollTop = 0; this.classList = { toggle() {}, add() {}, remove() {} }; }
    setAttribute() {}
    addEventListener(name, callback) { this.listeners.set(name, callback); }
    append(...nodes) { for (const node of nodes) { node.remove?.(); node.parent = this; this.children.push(node); } }
    replaceChildren(...nodes) { for (const child of this.children) child.parent = null; this.children = []; this.append(...nodes); }
    remove() { if (this.parent) this.parent.children = this.parent.children.filter(n => n !== this); this.parent = null; }
    focus() { document.activeElement = this; }
    scrollIntoView() {}
    click() {}
    querySelector() { return new Element(); }
    async fire(name, event = {}) { await this.listeners.get(name)?.(event); for (let i = 0; i < 15; i++) await setImmediate(); }
  }
  const get = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  const location = { href: 'http://localhost/' }, events = new Map(), pending = deferred();
  const requests = [], uploaded = data('00000000-0000-4000-8000-000000000001'); let timer = 0;
  try {
    Object.assign(globalThis, {
      document: { body: new Element(), activeElement: null, getElementById: get, createElement: () => new Element(), querySelectorAll: () => [] },
      window: { localStorage: { getItem: () => null, setItem() {} }, matchMedia: () => ({ matches: false, addEventListener() {} }), addEventListener: (name, fn) => events.set(name, fn), scrollTo() {} },
      location, history: { pushState(_, __, url) { location.href = String(url); }, replaceState(_, __, url) { location.href = String(url); } },
      setTimeout() { return ++timer; }, clearTimeout() {},
      async fetch(path, init = {}) {
        requests.push({ path, method: init.method || 'GET' });
        if (path === '/api/session') return new Response(JSON.stringify({ csrf_token: 'token', max_upload_bytes: 1024, max_concurrent_jobs: 2 }));
        if (path === '/api/documents') return new Response(JSON.stringify(await pending.promise), { status: 201 });
        return new Response(JSON.stringify(uploaded));
      },
    });
    let source = await readFile(new URL('../frontend/assets/app.js', import.meta.url), 'utf8');
    for (const file of ['documents.mjs', 'views.mjs']) source = source.replace(new RegExp(`'\\./${file}(?:\\?[^']*)?'`), JSON.stringify(new URL(`../frontend/assets/${file}`, import.meta.url).href));
    await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
    await get('file-input').fire('change', { target: { files: [file()], value: '' } });
    assert.equal(get('start-check').disabled, false); assert.equal(requests.filter(r => r.method === 'POST').length, 0);
    assert.equal(new URL(location.href).hash, '#check');
    await get('start-check').fire('click');
    assert.equal(requests.filter(r => r.method === 'POST').length, 1); assert.equal(get('report').hidden, true);
    assert.match(get('document-title').textContent, /Проверяем/);
    // Navigate back while the request runs: its response must not reopen the report.
    location.href = 'http://localhost/'; await events.get('popstate')();
    pending.resolve(uploaded); for (let i = 0; i < 15; i++) await setImmediate();
    assert.equal(get('landing').hidden, false); assert.equal(location.href, 'http://localhost/');
    await get('document-select').fire('change', { target: { value: 'upload-1' } });
    assert.equal(get('report').hidden, false); assert.equal(get('count-all').textContent, 2);
    assert.equal(new URL(location.href).searchParams.get('document'), uploaded.document_id);
    get('finding-detail').scrollTop = 120;
    const firstMark = get('findings').children[0].children[0];
    firstMark.checked = true; await firstMark.fire('change');
    assert.equal(get('finding-detail').scrollTop, 120);
    await get('findings').children[1].children[1].fire('click');
    assert.equal(get('finding-detail').scrollTop, 0);
    await get('search').fire('input', { target: { value: 'неизвестный фрагмент' } });
    assert.equal(get('empty-title').textContent, 'Ничего не найдено по условиям');
    await get('reset-filters').fire('click'); assert.equal(get('empty').hidden, true);
    location.href = `http://localhost/?document=${uploaded.document_id}`; await events.get('popstate')();
    assert.equal(requests.at(-1).path, `/api/documents/${uploaded.document_id}`);
    location.href = 'http://localhost/?document=invalid'; await events.get('popstate')();
    assert.match(get('global-message').textContent, /некорректный идентификатор/);
    assert.equal(requests.filter(r => r.method === 'POST').length, 1);
  } finally {
    for (const [key, descriptor] of saved) { if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key]; }
  }
});
