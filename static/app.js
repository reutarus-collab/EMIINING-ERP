// --- Device identity (persists per phone, survives app restarts) ---
let deviceId = localStorage.getItem("device_id");
if (!deviceId) {
  deviceId = "dev-" + Math.random().toString(36).slice(2, 10);
  localStorage.setItem("device_id", deviceId);
}

function uuid() {
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

// --- IndexedDB: the offline queue. This is the whole point of the app. ---
const DB_NAME = "emining_erp";
const STORE = "pending_transactions";

function openIDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = () => {
      req.result.createObjectStore(STORE, { keyPath: "id" });
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function queueTransaction(txn) {
  const db = await openIDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, "readwrite");
    tx.objectStore(STORE).put(txn);
    tx.oncomplete = resolve;
    tx.onerror = () => reject(tx.error);
  });
}

async function getPending() {
  const db = await openIDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, "readonly");
    const req = tx.objectStore(STORE).getAll();
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function removePending(ids) {
  const db = await openIDB();
  const tx = db.transaction(STORE, "readwrite");
  const store = tx.objectStore(STORE);
  ids.forEach((id) => store.delete(id));
  return new Promise((resolve, reject) => {
    tx.oncomplete = resolve;
    tx.onerror = () => reject(tx.error);
  });
}

// --- UI wiring ---
let itemsById = {};

async function loadItems() {
  const res = await fetch("/api/items");
  const items = await res.json();
  itemsById = Object.fromEntries(items.map((i) => [i.id, i]));

  const select = document.getElementById("item-select");
  select.innerHTML = "";
  items.forEach((i) => {
    const opt = document.createElement("option");
    opt.value = i.id;
    opt.textContent = `${i.name} (${i.item_type})`;
    select.appendChild(opt);
  });
}

async function loadStock() {
  try {
    const res = await fetch("/api/stock", { credentials: "same-origin" });
    if (!res.ok) return;
    const rows = await res.json();
    const list = document.getElementById("stock-list");
    list.innerHTML = "";
    rows.forEach((r) => {
      const li = document.createElement("li");
      li.innerHTML = `<span>${r.name}</span><span>${r.qty_kg.toFixed(1)} kg</span>`;
      list.appendChild(li);
    });
  } catch (e) {
    // offline — leave last-known stock on screen rather than clearing it
  }
}

async function renderPending() {
  const pending = await getPending();
  const list = document.getElementById("pending-list");
  list.innerHTML = "";
  pending.forEach((t) => {
    const name = itemsById[t.item_id] ? itemsById[t.item_id].name : `item ${t.item_id}`;
    const li = document.createElement("li");
    li.className = "pending-item";
    li.innerHTML = `<span>${name} — ${t.qty_in_unit} ${t.unit_used}</span><span>queued</span>`;
    list.appendChild(li);
  });

  const status = document.getElementById("sync-status");
  if (pending.length > 0) {
    status.textContent = `${pending.length} sale(s) waiting to sync`;
    status.classList.add("pending");
  } else {
    status.textContent = "all synced";
    status.classList.remove("pending");
  }
}

async function syncNow() {
  const pending = await getPending();
  if (pending.length === 0) return;
  try {
    const res = await fetch("/api/sync", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ transactions: pending }),
    });
    if (!res.ok) throw new Error("sync rejected");
    const data = await res.json();
    await removePending(data.accepted);
    await renderPending();
    await loadStock();
  } catch (e) {
    // no signal, or server unreachable — stays queued, tries again later
    console.log("sync deferred:", e.message);
  }
}

// --- Login ---
document.getElementById("login-btn").addEventListener("click", async () => {
  const username = document.getElementById("username").value.trim();
  const password = document.getElementById("password").value;
  const errorEl = document.getElementById("login-error");
  errorEl.textContent = "";

  try {
    const res = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    const data = await res.json();
    if (!res.ok) {
      errorEl.textContent = data.error || "login failed";
      return;
    }
    document.getElementById("location-name").textContent = data.location_name;
    document.getElementById("login-screen").style.display = "none";
    document.getElementById("app-screen").style.display = "block";

    await loadItems();
    await loadStock();
    await renderPending();
  } catch (e) {
    errorEl.textContent = "Can't reach the server — you need signal to log in the first time.";
  }
});

document.getElementById("logout-btn").addEventListener("click", async () => {
  await fetch("/api/logout", { method: "POST", credentials: "same-origin" });
  location.reload();
});

// --- Log a sale (this is the core action the whole app exists for) ---
document.getElementById("sale-form").addEventListener("submit", async (e) => {
  e.preventDefault();

  const itemId = parseInt(document.getElementById("item-select").value, 10);
  const qty = parseFloat(document.getElementById("qty").value);
  const unit = document.getElementById("unit-select").value;
  if (!qty || qty <= 0) return;

  const txn = {
    id: uuid(),
    item_id: itemId,
    action_type: "sale",
    qty_in_unit: qty,
    unit_used: unit,
    device_id: deviceId,
    created_at: new Date().toISOString(),
  };

  // Always write to the local queue first — the UI never waits on the network.
  await queueTransaction(txn);
  document.getElementById("qty").value = "";
  await renderPending();

  // Try to push immediately; if there's no signal this just silently fails
  // and the sale sits safely in the queue until the next sync.
  syncNow();
});

document.getElementById("sync-btn").addEventListener("click", syncNow);
window.addEventListener("online", syncNow);

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/service-worker.js").catch(() => {});
}
