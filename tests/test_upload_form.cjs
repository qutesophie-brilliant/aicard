// Unit tests for the actual app.js event handlers. No browser or network is used.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

class Element {
  constructor() {
    this.listeners = {}; this.attrs = {}; this.dataset = {}; this.children = [];
    this.value = ''; this.disabled = false; this.hidden = true;
    this.classList = { add() {}, remove() {}, toggle() {} };
  }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  emit(type, event = {}) { for (const fn of this.listeners[type] || []) fn(event); }
  getAttribute(name) { return this.attrs[name] ?? null; }
  setAttribute(name, value) { this.attrs[name] = String(value); }
  removeAttribute(name) { delete this.attrs[name]; }
  appendChild(el) { this.children.push(el); }
  focus() {}
  scrollIntoView() {}
  set innerHTML(value) {
    this.children = [];
    this.nodes = value ? { span: new Element(), button: new Element() } : {};
  }
  querySelector(selector) { return this.nodes?.[selector] || null; }
  querySelectorAll() { return []; }
}

class Transfer {
  constructor() {
    this._files = [];
    this.items = { add: file => this._files.push(file), clear: () => { this._files = []; } };
  }
  get files() { return this._files.slice(); }
}

function fixture(asyncMode = false) {
  const ids = Object.fromEntries([
    'drop', 'files', 'droplist', 'checkform', 'fileerror', 'file-status', 'go',
    'pick', 'busy', 'wait-message', 'wait-elapsed', 'wait-slow',
  ].map(id => [id, new Element()]));
  const form = ids.checkform, url = new Element(), body = new Element();
  const window = new Element();
  const submitted = [];
  const timers = [];
  let now = 0, resolve, reject;
  const pending = new Promise((a, b) => { resolve = a; reject = b; });
  const navigated = [], written = [];
  if (asyncMode) window.fetch = (url, options) => { submitted.push(options.body.data); return pending; };
  window.history = { pushState: (a, b, url) => navigated.push(url) };
  form.dataset = { maxFiles: '8', maxMb: '8' };
  form.querySelector = selector => ({ '[name="url"]': url, '[name="text"]': body }[selector] || null);
  form.querySelectorAll = selector => selector === '[data-check-submit]' ? [ids.go] : [];
  Object.defineProperty(url, 'validity', { get() {
    try { if (url.value) new URL(url.value); return { valid: true }; }
    catch { return { valid: false }; }
  } });
  form.reportValidity = () => !form.otherInvalid && (url.disabled || url.validity.valid);
  window.matchMedia = () => ({ matches: true });
  const document = new Element();
  document.documentElement = new Element();
  document.body = new Element();
  document.getElementById = id => ids[id] || null;
  document.querySelector = () => null;
  document.createElement = () => new Element();
  document.open = () => {}; document.close = () => {}; document.write = html => written.push(html);
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../app/static/app.js'), 'utf8'), {
    document, window, DataTransfer: Transfer, URL, AbortController,
    Date: { now: () => now },
    FormData: class { constructor() { this.data = { ...(url.disabled ? {} : { url: url.value }), text: body.value, files: ids.files.files }; } },
    File: class { constructor(parts, name, options) { this.name = name; this.type = options.type; this.size = 100; } },
    localStorage: { getItem: () => null, setItem() {} },
    location: { hash: '', href: 'https://example.test/check' }, navigator: {},
    setInterval: fn => { timers.push(fn); return timers.length; }, clearInterval(id) { timers[id - 1] = null; }, setTimeout: () => 1, clearTimeout() {},
  });
  return {
    ids, form, url, body, submitted, navigated, written,
    tick(ms) { now += ms; timers.filter(Boolean).forEach(fn => fn()); },
    resolve, reject,
    async settle() { await new Promise(done => setImmediate(done)); },
    addFile(ext = 'jpg') {
      ids.files.files = [{ name: 'sample.' + ext, type: ext === 'pdf' ? 'application/pdf' : 'image/' + ext, size: 100 }];
      ids.files.emit('change');
    },
    submit() {
      // Model the browser validation gate and successful-controls serialization.
      if (!form.noValidate && !form.reportValidity()) return false;
      const event = { defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } };
      form.emit('submit', event);
      if (event.defaultPrevented) return false;
      submitted.push({ ...(url.disabled ? {} : { url: url.value }), text: body.value, files: ids.files.files });
      return true;
    },
    restore() { window.emit('pageshow'); },
  };
}

for (const ext of ['jpg', 'png', 'pdf']) {
  test('invalid leftover URL does not block ' + ext + ' upload', () => {
    const f = fixture(); f.url.value = 'unfinished-url'; f.addFile(ext);
    assert.equal(f.url.disabled, false, 'URL must remain editable until submission');
    assert.equal(f.ids.go.textContent, '파일 1개 분석 시작');
    assert.equal(f.submit(), true);
    assert.equal('url' in f.submitted[0], false);
    assert.equal(f.submitted[0].files[0].name, 'sample.' + ext);
  });
}

test('body input ignores an invalid URL without changing its text', () => {
  const f = fixture(); f.url.value = 'unfinished-url'; f.body.value = 'AI 작동 설명';
  assert.equal(f.submit(), true);
  assert.equal(f.submitted[0].text, 'AI 작동 설명');
  assert.equal('url' in f.submitted[0], false);
  assert.equal(f.url.value, 'unfinished-url');
});

test('valid source URL stays in file and body submissions', () => {
  for (const mode of ['file', 'body']) {
    const f = fixture(); f.url.value = 'https://example.test/product?a=1';
    if (mode === 'file') f.addFile(); else f.body.value = 'AI 작동 설명';
    assert.equal(f.submit(), true);
    assert.equal(f.submitted[0].url, f.url.value);
    assert.equal(f.url.disabled, false);
  }
});

test('URL-only invalid input stays blocked and valid input submits', () => {
  const f = fixture(); f.url.value = 'unfinished-url';
  assert.equal(f.submit(), false);
  assert.equal(f.form.getAttribute('aria-busy'), null);
  assert.equal(f.url.disabled, false);
  f.url.value = 'https://example.test/product';
  assert.equal(f.submit(), true);
  assert.equal(f.submitted[0].url, f.url.value);
});

test('pageshow restores URL editing, retained files, and URL-only validation after removal', () => {
  const f = fixture(); f.url.value = 'unfinished-url'; f.addFile();
  assert.equal(f.submit(), true);
  assert.equal(f.url.disabled, true);
  f.restore();
  assert.equal(f.url.disabled, false);
  assert.equal(f.url.value, 'unfinished-url');
  assert.equal(f.ids.go.disabled, false);
  assert.equal(f.ids.go.textContent, '파일 1개 분석 시작');
  f.ids.droplist.children[0].querySelector('button').emit('click');
  assert.equal(f.ids.files.files.length, 0);
  assert.equal(f.submit(), false);
});

test('other invalid fields still block submission and restore the omitted URL', () => {
  const f = fixture(); f.url.value = 'unfinished-url'; f.addFile(); f.form.otherInvalid = true;
  assert.equal(f.submit(), false);
  assert.equal(f.url.disabled, false);
  assert.equal(f.form.getAttribute('aria-busy'), null);
});

test('empty form still shows the input-required message', () => {
  const f = fixture();
  assert.equal(f.submit(), false);
  assert.equal(f.ids.fileerror.hidden, false);
  assert.match(f.ids.fileerror.textContent, /판매페이지 주소/);
});


test('async POST keeps mobile elapsed clock active and prevents duplicate requests', async () => {
  const f = fixture(true); f.addFile('png');
  assert.equal(f.submit(), false, 'native navigation must be prevented');
  assert.equal(f.submitted.length, 1);
  f.tick(3000);
  assert.equal(f.ids['wait-elapsed'].textContent, '3초');
  f.restore(); // pageshow must not clear an active request on mobile.
  f.tick(62000);
  assert.equal(f.ids['wait-elapsed'].textContent, '1분 5초');
  assert.equal(f.ids['wait-slow'].hidden, false);
  assert.equal(f.submit(), false); assert.equal(f.submitted.length, 1);
  f.resolve({ ok:true, url:'https://example.test/check', headers:{get:()=> 'text/html'}, text:async()=> '<html>result</html>' });
  await f.settle();
  assert.equal(f.written[0], '<html>result</html>');
  assert.equal(f.navigated[0], 'https://example.test/check');
});

test('network failure preserves files and restores the action instead of leaving a spinner', async () => {
  const f = fixture(true); f.addFile('jpg'); f.url.value = 'unfinished-url';
  f.submit(); f.reject(new Error('Connection failed')); await f.settle();
  assert.equal(f.form.getAttribute('aria-busy'), null);
  assert.equal(f.ids.files.files.length, 1);
  assert.equal(f.url.disabled, false);
  assert.equal(f.ids.go.disabled, false);
  assert.equal(f.ids.busy.hidden, true);
  assert.equal(f.ids.fileerror.hidden, false);
  assert.match(f.ids.fileerror.textContent, /Connection/);
});

test('server 502 returns a clear retry state without replacing page with an error document', async () => {
  const f = fixture(true); f.url.value = 'https://example.test/product'; f.submit();
  f.resolve({ok:false,status:502}); await f.settle();
  assert.equal(f.written.length, 0);
  assert.equal(f.ids.go.disabled, false);
  assert.match(f.ids.fileerror.textContent, /분석 서버/);
});
