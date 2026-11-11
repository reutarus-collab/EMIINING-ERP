/* Small durable POS cache and outbox. It stores only the last authorized POS
   bootstrap responses and checkout payloads; the server remains authoritative. */
(function () {
  const DB = 'emining-pos-offline-v1';
  const openDb = () => new Promise((resolve, reject) => {
    const req = indexedDB.open(DB, 2);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains('cache')) db.createObjectStore('cache');
      if (!db.objectStoreNames.contains('sales')) db.createObjectStore('sales', {keyPath: 'key'});
      if (!db.objectStoreNames.contains('discarded')) db.createObjectStore('discarded', {keyPath: 'key'});
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
  async function transact(store, mode, action) {
    const db = await openDb();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(store, mode), result = action(tx.objectStore(store));
      tx.oncomplete = () => { db.close(); resolve(result && result.result); };
      tx.onerror = tx.onabort = () => { db.close(); reject(tx.error); };
    });
  }
  const request = req => new Promise((resolve, reject) => {
    req.onsuccess = () => resolve(req.result); req.onerror = () => reject(req.error);
  });
  window.OfflineStore = {
    get: key => openDb().then(db => new Promise((resolve, reject) => {
      const tx = db.transaction('cache'), req = tx.objectStore('cache').get(key);
      req.onsuccess = () => { db.close(); resolve(req.result); };
      req.onerror = () => { db.close(); reject(req.error); };
    })),
    set: (key, value) => transact('cache', 'readwrite', s => s.put(value, key)),
    enqueue: sale => transact('sales', 'readwrite', s => s.put(sale)),
    remove: key => transact('sales', 'readwrite', s => s.delete(key)),
    clearCache: () => transact('cache', 'readwrite', s => s.clear()),
    logDiscard: rec => transact('discarded', 'readwrite', s => s.put(rec)),
    list: async () => {
      const db = await openDb();
      return new Promise((resolve, reject) => {
        const tx = db.transaction('sales'), req = tx.objectStore('sales').getAll();
        req.onsuccess = () => { db.close(); resolve(req.result || []); };
        req.onerror = () => { db.close(); reject(req.error); };
      });
    },
    cacheable: url => /^\/api\/(me|locations|products\/search|products\/categories|customers|till\/current)$/.test(url.pathname)
  };

  const nativeFetch = window.fetch.bind(window);
  window.fetch = async function (input, init) {
    const url = new URL(typeof input === 'string' || input instanceof URL ? String(input) : input.url, location.origin);
    const method = String((init && init.method) || (input && input.method) || 'GET').toUpperCase();
    const key = url.pathname + url.search;
    try {
      const response = await nativeFetch(input, init);
      if (method === 'GET' && response.ok && OfflineStore.cacheable(url)) {
        response.clone().json().then(data => OfflineStore.set(key, data)).catch(() => {});
      }
      return response;
    } catch (error) {
      if (method !== 'GET' || !OfflineStore.cacheable(url)) throw error;
      const data = await OfflineStore.get(key);
      if (data === undefined) throw error;
      return new Response(JSON.stringify(data), {status: 200, headers: {'Content-Type': 'application/json', 'X-Offline-Cache': '1'}});
    }
  };
})();
