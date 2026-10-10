const fs = require('fs'), assert = require('assert');
const store = {};
const calls = [];
let next = () => ({status: 200});
const window = {
  location: {origin: 'https://x.test'},
  crypto: {randomUUID: (() => { let n = 0; return () => 'key-' + (++n); })()},
  fetch: (url, init) => { calls.push({url, init}); const r = next(); return r.reject ? Promise.reject(new Error('net')) : Promise.resolve(r); },
};
window.window = window;
const sandbox = {window, localStorage: {getItem: k => store[k] ?? null, setItem: (k, v) => { store[k] = v; }}, URL, Headers};
new Function('window', 'localStorage', 'URL', 'Headers', fs.readFileSync('static/js/idem.js', 'utf8'))(window, sandbox.localStorage, URL, Headers);
const key = () => calls[calls.length - 1].init.headers.get('Idempotency-Key');
const post = (u, b) => window.fetch(u, {method: 'POST', body: JSON.stringify(b)});

(async () => {
  next = () => ({status: 200});
  await post('/api/expenses', {amount: 50});           const k1 = key();
  await post('/api/expenses', {amount: 50});           const k2 = key();
  assert.notStrictEqual(k1, k2, 'after success the next identical entry must get a NEW key');

  next = () => ({reject: true});
  await post('/api/expenses', {amount: 70}).catch(() => {}); const k3 = key();
  next = () => ({status: 200});
  await post('/api/expenses', {amount: 70});                 const k4 = key();
  assert.strictEqual(k3, k4, 'after a network failure the identical retry must REUSE the key');
  await post('/api/expenses', {amount: 70});                 const k5 = key();
  assert.notStrictEqual(k4, k5, 'key forgotten after the successful retry');

  next = () => ({status: 502});
  await post('/api/admin/payables/3/pay', {amount: 900});    const k6 = key();
  await post('/api/admin/payables/3/pay', {amount: 900});    const k7 = key();
  assert.strictEqual(k6, k7, '5xx = unknown outcome, key kept');

  next = () => ({status: 400});
  await post('/api/inventory/transfers/9/receive', {q: 1});  const k8 = key();
  await post('/api/inventory/transfers/9/receive', {q: 1});  const k9 = key();
  assert.notStrictEqual(k8, k9, '4xx = definite rejection, key dropped');

  next = () => ({status: 200});
  await post('/api/expenses', {amount: 71});                 const k10 = key();
  assert.notStrictEqual(k10, k5, 'different body, different key');

  // in-flight double tap: two identical calls before the first resolves share a key
  let release; next = () => ({status: 200});
  const p1 = post('/api/customers/4/repay', {amount: 100}); const a = key();
  const p2 = post('/api/customers/4/repay', {amount: 100}); const b = key();
  await Promise.all([p1, p2]);
  assert.strictEqual(a, b, 'double-tap shares one key');

  // untouched traffic
  const n = calls.length;
  await window.fetch('/api/expenses');                        assert.strictEqual(calls[n].init.headers, undefined, 'GET untouched');
  await post('/api/pos/checkout', {x: 1});                    assert.strictEqual(calls[n + 1].init.headers, undefined, 'unlisted POST untouched');
  await window.fetch('/api/expenses', {method: 'POST', body: '{}', headers: {'Idempotency-Key': 'mine'}});
  assert.strictEqual(calls[n + 2].init.headers['Idempotency-Key'], 'mine', 'caller key respected');
  await window.fetch('https://other.test/api/expenses', {method: 'POST', body: '{}'});
  assert.strictEqual(calls[n + 3].init.headers, undefined, 'cross-origin untouched');
  console.log('ALL 10 CLIENT CHECKS PASSED');
})().catch(e => { console.error('FAIL:', e.message); process.exit(1); });
