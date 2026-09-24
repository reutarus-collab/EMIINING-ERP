class OfflineSyncEngine {
    constructor() {
        this.db = null;
        this.deviceId = localStorage.getItem('device_id') || 'DEV-' + Math.random().toString(36).substr(2, 9);
        localStorage.setItem('device_id', this.deviceId);
        this.initDB();
    }

    async initDB() {
        return new Promise((resolve, reject) => {
            const request = indexedDB.open('EminingOfflineDB', 1);
            
            request.onupgradeneeded = (event) => {
                const db = event.target.result;
                if (!db.objectStoreNames.contains('action_queue')) {
                    db.createObjectStore('action_queue', { keyPath: 'client_seq', autoIncrement: true });
                }
            };

            request.onsuccess = (event) => {
                this.db = event.target.result;
                resolve();
            };

            request.onerror = (e) => reject(e);
        });
    }

    async queueAction(actionType, payload) {
        const transaction = this.db.transaction(['action_queue'], 'readwrite');
        const store = transaction.objectStore('action_queue');
        
        const record = {
            action_type: actionType,
            payload: payload,
            timestamp: new Date().toISOString()
        };

        store.add(record);
        console.log('Action queued offline successfully.');
        this.updateSyncUI();
    }

    async triggerSync() {
        if (!navigator.onLine) return;

        const transaction = this.db.transaction(['action_queue'], 'readonly');
        const store = transaction.objectStore('action_queue');
        const allRecords = await new Promise(r => store.getAll().onsuccess = e => r(e.target.result));

        if (allRecords.length === 0) return;

        const syncPayload = {
            device_id: this.deviceId,
            branch_id: localStorage.getItem('branch_id') || 'BRANCH-MAIN',
            actions: allRecords.map(r => ({
                client_seq: r.client_seq,
                action_type: r.action_type,
                payload: r.payload
            }))
        };

        try {
            const response = await fetch('/api/v1/sync', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(syncPayload)
            });

            const result = await response.json();
            
            // Remove successfully synced actions from IndexedDB
            const deleteTx = this.db.transaction(['action_queue'], 'readwrite');
            const deleteStore = deleteTx.objectStore('action_queue');

            result.results.forEach(res => {
                if (res.status === 'SUCCESS' || res.status === 'DUPLICATE') {
                    deleteStore.delete(res.client_seq);
                }
            });

            console.log('Sync sequence completed.');
            this.updateSyncUI();

        } catch (err) {
            console.error('Offline sync failed:', err);
        }
    }

    async updateSyncUI() {
        const transaction = this.db.transaction(['action_queue'], 'readonly');
        const store = transaction.objectStore('action_queue');
        const count = await new Promise(r => store.count().onsuccess = e => r(e.target.result));

        const badge = document.getElementById('pending-sync-badge');
        if (badge) {
            badge.innerText = `${count} pending sync`;
            badge.className = count > 0 ? "badge bg-warning" : "badge bg-success";
        }
    }
}

const syncEngine = new OfflineSyncEngine();
window.addEventListener('online', () => syncEngine.triggerSync());
