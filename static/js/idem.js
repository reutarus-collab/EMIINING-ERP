/* Retry-safety for cash/stock-moving POSTs.
 *
 * Wraps window.fetch. For the protected endpoints below it attaches an
 * Idempotency-Key header. The key is REUSED when the same request is sent again
 * after an ambiguous outcome (network drop, 5xx, double-tap while still in flight),
 * so the server can recognise it as the same action. After any definite answer
 * (success or a 4xx rejection) the key is forgotten, so the next genuine entry
 * gets a fresh key.
 *
 * Keep PROTECTED in sync with the @idempotent routes in /routes
 * (tests/test_idempotency.py fails if they drift apart).
 */
(function () {
  const PROTECTED = [
    /^\/api\/admin\/payables\/\d+\/pay$/,
    /^\/api\/factory\/milling-runs$/,
    /^\/api\/factory\/own-milling$/,
    /^\/api\/admin\/customers\/merge$/,
    /^\/api\/factory\/produce$/,
    /^\/api\/customers$/,
    /^\/api\/customers\/\d+\/repay$/,
    /^\/api\/customers\/\d+\/add-debt$/,
    /^\/api\/till\/cash-movements$/,
    /^\/api\/pos\/orders\/\d+\/refund$/,
    /^\/api\/inventory\/adjust$/,
    /^\/api\/inventory\/transfers$/,
    /^\/api\/inventory\/transfers\/\d+\/(receive|close-short)$/,
    /^\/api\/expenses$/,
    /^\/api\/suppliers$/,
    /^\/api\/suppliers\/add$/,
    /^\/api\/po$/,
    /^\/api\/reports\/owner-withdrawals$/,
    /^\/api\/reports\/equipment-purchases$/,
  ];
  const STORE = 'emining.idem.pending';
  const TTL_MS = 15 * 60 * 1000;
  const realFetch = window.fetch.bind(window);

  function newKey() {
    if (window.crypto && typeof window.crypto.randomUUID === 'function') return window.crypto.randomUUID();
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
      const r = Math.random() * 16 | 0;
      return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
    });
  }
  function load() {
    try {
      const all = JSON.parse(localStorage.getItem(STORE) || '{}');
      const now = Date.now();
      Object.keys(all).forEach(k => { if (now - all[k].t > TTL_MS) delete all[k]; });
      return all;
    } catch (e) { return {}; }
  }
  function save(all) { try { localStorage.setItem(STORE, JSON.stringify(all)); } catch (e) {} }

  window.fetch = function (input, init) {
    init = init || {};
    const method = String(init.method || (input && input.method) || 'GET').toUpperCase();
    if (method !== 'POST' || typeof input !== 'string' && !(input instanceof URL)) return realFetch(input, init);
    let path;
    try {
      const u = new URL(String(input), window.location.origin);
      if (u.origin !== window.location.origin) return realFetch(input, init);
      path = u.pathname;
    } catch (e) { return realFetch(input, init); }
    if (!PROTECTED.some(rx => rx.test(path))) return realFetch(input, init);

    const headers = new Headers(init.headers || {});
    if (headers.has('Idempotency-Key')) return realFetch(input, init);   // caller manages its own key

    const fingerprint = path + '|' + (typeof init.body === 'string' ? init.body : '');
    const all = load();
    const reuse = typeof init.body === 'string' && all[fingerprint];
    const key = reuse ? all[fingerprint].key : newKey();
    all[fingerprint] = {key: key, t: reuse ? all[fingerprint].t : Date.now()};
    save(all);
    headers.set('Idempotency-Key', key);

    return realFetch(input, Object.assign({}, init, {headers: headers})).then(resp => {
      if (resp.status < 500) {            // definite answer: forget the key
        const now = load(); delete now[fingerprint]; save(now);
      }                                   // 5xx = outcome unknown: keep the key for the retry
      return resp;
    });                                   // network failure rejects: key is kept for the retry
  };
})();

/* Session-expiry notice: any same-origin /api/ 401 shows a login banner. */
(function () {
  const inner = window.fetch.bind(window);
  function banner() {
    if (document.getElementById('session-expired-banner') || !document.body) return;
    const b = document.createElement('div');
    b.id = 'session-expired-banner';
    b.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:10000;background:#b00;color:#fff;padding:12px;text-align:center;font-weight:bold';
    b.innerHTML = 'Your session expired. Nothing was lost or saved. ' +
      '<a href="/login?next=/" style="color:#fff;text-decoration:underline">Log in again</a>, then repeat the last action.';
    document.body.appendChild(b);
  }
  window.fetch = function (input, init) {
    return inner(input, init).then(resp => {
      try {
        const u = new URL(typeof input === 'string' || input instanceof URL ? String(input) : input.url, window.location.origin);
        if (resp.status === 401 && u.origin === window.location.origin && u.pathname.startsWith('/api/')) banner();
      } catch (e) {}
      return resp;
    });
  };
})();
