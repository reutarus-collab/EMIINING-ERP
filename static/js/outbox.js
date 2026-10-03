class OutboxManager {
  constructor() {
    this.dbName = 'EminingOutboxDB';
    this.dbVersion = 1;
    this.db = null;
    this.initDB();
  }

  initDB() {
    return new Promise((resolve, reject) => {
      const request = indexedDB.open(this.dbName, this.dbVersion);
      request.onupgradeneeded = (event) => {
        const db = event.target.result;
        if (!db.objectStoreNames.contains('outbox')) {
          db.createObjectStore('outbox', { keyPath: 'queue_id', autoIncrement: true });
        }
      };
      request.onsuccess = (event) => {
        this.db = event.target.result;
        resolve(this.db);
      };
      request.onerror = (event) => reject(event.target.error);
    });
  }

  async enqueue(type, data) {
    if (!this.db) await this.initDB();
    const idempotency_key = 'idemp-' + Date.now() + '-' + Math.random().toString(36).substr(2, 9);
    const item = { type, data, idempotency_key, created_at: new Date().toISOString() };

    return new Promise((resolve, reject) => {
      const tx = this.db.transaction('outbox', 'readwrite');
      const store = tx.objectStore('outbox');
      const req = store.add(item);
      req.onsuccess = () => {
        if ('serviceWorker' in navigator && 'SyncManager' in window) {
          navigator.serviceWorker.ready.then((swRegistration) => {
            swRegistration.sync.register('sync-outbox-queue');
          });
        }
        resolve(item);
      };
      req.onerror = (e) => reject(e.target.error);
    });
  }

  async getAll() {
    if (!this.db) await this.initDB();
    return new Promise((resolve, reject) => {
      const tx = this.db.transaction('outbox', 'readonly');
      const store = tx.objectStore('outbox');
      const req = store.getAll();
      req.onsuccess = () => resolve(req.result);
      req.onerror = (e) => reject(e.target.error);
    });
  }

  async remove(queue_id) {
    if (!this.db) await this.initDB();
    return new Promise((resolve, reject) => {
      const tx = this.db.transaction('outbox', 'readwrite');
      const store = tx.objectStore('outbox');
      const req = store.delete(queue_id);
      req.onsuccess = () => resolve();
      req.onerror = (e) => reject(e.target.error);
    });
  }

  async flushOutbox() {
    if (this.flushing) return;
    this.flushing = true;
    try {
      const queue = await this.getAll();
      if (queue.length === 0) return;

      const response = await fetch('/api/sync/outbox', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(queue)
      });

      if (response.status === 401) {
        window.dispatchEvent(new CustomEvent('outbox-auth-required',
          { detail: { pending: queue.length } }));
        return;
      }
      if (!response.ok) {
        console.error('Outbox sync rejected:', response.status);
        return;
      }
      const resData = await response.json();
      for (const id of (resData.synced_ids || [])) {
        await this.remove(id);
      }
    } catch (err) {
      console.log('Outbox flush deferred: offline mode active.');
    } finally {
      this.flushing = false;
    }
  }
}

window.outboxManager = new OutboxManager();

window.addEventListener('online', () => window.outboxManager.flushOutbox());
window.addEventListener('load', () => window.outboxManager.flushOutbox());
setInterval(() => {
  if (navigator.onLine) window.outboxManager.flushOutbox();
}, 60000);

window.addEventListener('outbox-auth-required', (e) => {
  if (document.getElementById('outbox-banner')) return;
  const b = document.createElement('div');
  b.id = 'outbox-banner';
  b.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:9999;background:#b00;color:#fff;padding:10px;text-align:center';
  b.innerHTML = e.detail.pending + ' sale(s) waiting to sync. <a href="/login" style="color:#fff;font-weight:bold">Log in again</a> to send them.';
  document.body.appendChild(b);
});

if ('serviceWorker' in navigator) {
  navigator.serviceWorker.addEventListener('message', (event) => {
    if (event.data && event.data.action === 'TRIGGER_SYNC') {
      window.outboxManager.flushOutbox();
    }
  });
}