const CACHE = 'emining-pos-shell-v1';
const SHELL = ['/static/js/offline-store.js', '/static/js/main.js', '/static/js/pos.js', '/static/js/idem.js', '/static/js/ui-mobile.js', '/static/css/ui-mobile.css', '/static/manifest.json'];
self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key.startsWith('emining-pos-shell-') && key !== CACHE).map(key => caches.delete(key)))).then(() => self.clients.claim()));
});
self.addEventListener('message', event => {
  if (event.data === 'CACHE_AUTHENTICATED_SHELL') {
    event.waitUntil(fetch('/', {credentials:'include'}).then(response => {
      if (response.ok && !response.redirected) return caches.open(CACHE).then(cache => cache.put('/', response));
    }).catch(() => {}));
  }
});
self.addEventListener('fetch', event => {
  const request = event.request, url = new URL(request.url);
  if (request.method !== 'GET' || url.origin !== self.location.origin) return;
  if (request.mode === 'navigate' && url.pathname === '/') {
    event.respondWith(fetch(request).then(async response => {
      if (response.ok && !response.redirected) await caches.open(CACHE).then(cache => cache.put('/', response.clone()));
      return response;
    }).catch(() => caches.match('/')));
  }
  // App shell assets are versioned by their URLs. Never cache API replies here.
  else if (url.pathname.startsWith('/static/')) {
    event.respondWith(caches.match(request).then(hit => hit || fetch(request).then(async response => {
      if (response.ok) await caches.open(CACHE).then(cache => cache.put(request, response.clone()));
      return response;
    })));
  }
});
