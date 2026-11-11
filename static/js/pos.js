let cart = [];
let heldCart = [];
let customersCache = [];
let searchResultsCache = [];
let catalogViewMode = 'grid';
let activeLocationId = '';
let currentTill = null;
let pendingCheckoutAttempt = null;
try { pendingCheckoutAttempt = JSON.parse(sessionStorage.getItem('pendingCheckoutAttempt') || 'null'); } catch (_) {}
async function initOutletTill() {
  try {
    const [meRes, locationsRes] = await Promise.all([fetch('/api/me'), fetch('/api/locations')]);
    const me = await meRes.json();
    window.currentUsername = me.username;
    const locations = await locationsRes.json();
    const select = document.getElementById('pos-location');
    select.innerHTML = locations.map(l => `<option value="${l.id}">${escHtml(l.name)}</option>`).join('');
    const assigned = locations.find(l => String(l.id) === String(me.location_id));
    if (assigned) select.value = assigned.id;
    if (!['admin', 'accountant'].includes(me.role)) select.disabled = true;
    if (!['admin', 'accountant'].includes(me.role) && !assigned) {
      document.getElementById('till-state').textContent = 'Outlet assignment required. Ask an administrator to assign your exact outlet.';
      return;
    }
    activeLocationId = select.value;
    window.activeLocationId = activeLocationId;
    reloadCatalog();   // the first load ran before the outlet was known
    await refreshTill();
    if (document.getElementById('dashboard-tab')?.classList.contains('active') && typeof loadDashboard === 'function') loadDashboard();
    if (typeof loadCustomers === 'function') loadCustomers();
    if (typeof loadFactoryDropdowns === 'function') loadFactoryDropdowns();
    if (typeof loadProductionRuns === 'function') loadProductionRuns();
    if (typeof loadStock === 'function') loadStock();
    if (typeof loadDailyReport === 'function') loadDailyReport();
    await syncOfflineSales();
    await renderOfflineSales();
  } catch (e) {
    const state = document.getElementById('till-state');
    if (state) state.textContent = 'Outlet setup required: assign this user to a valid location.';
  }
}
async function changePosLocation() {
  activeLocationId = document.getElementById('pos-location').value;
  window.activeLocationId = activeLocationId;
  await refreshTill();
  if (document.getElementById('dashboard-tab')?.classList.contains('active') && typeof loadDashboard === 'function') loadDashboard();
  reloadCatalog();
  if (typeof loadCustomers === 'function') loadCustomers();
  if (typeof loadStock === 'function') loadStock();
  if (typeof loadDailyReport === 'function') loadDailyReport();
  if (typeof loadFactoryDropdowns === 'function') loadFactoryDropdowns();
  if (typeof loadProductionRuns === 'function') loadProductionRuns();
}
async function refreshTill() {
  const q = activeLocationId ? `?location_id=${encodeURIComponent(activeLocationId)}` : '';
  const r = await fetch('/api/till/current' + q);
  const d = await r.json();
  currentTill = d.session;
  document.getElementById('till-state').textContent = currentTill
    ? `${d.location_name} · Open till #${currentTill.id} · Expected cash KSh ${Number(currentTill.expected_cash).toFixed(2)}`
    : `${d.location_name || 'Outlet'} · Till closed`;
  const th = d.trading_hours;
  if (th && th.enforced) {
    const stateEl = document.getElementById('till-state');
    stateEl.textContent += ` · Trading hours ${th.text}${th.open_now ? '' : ' (closed now, reopens ' + String(th.open_hour).padStart(2, '0') + ':00)'}`;
    if (d.auto_closed && !currentTill) stateEl.textContent += ` · Your last till (#${d.auto_closed.id}) was closed by the system at closing time. Expected cash was KSh ${Number(d.auto_closed.expected_cash).toFixed(2)}; report any difference to the owner.`;
  }
  document.getElementById('till-open-btn').style.display = currentTill ? 'none' : '';
  document.getElementById('till-close-btn').style.display = currentTill ? '' : 'none';
  await loadTillMovementHistory();
}
async function openTill() {
  const opening_cash = Number(document.getElementById('till-opening-cash').value || 0);
  const r = await fetch('/api/till/open', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({location_id:activeLocationId, opening_cash})});
  const d = await r.json();
  if (!r.ok) return alert(d.message || 'Could not open till.');
  await refreshTill();
}
async function closeTill() {
  const entry = prompt('Enter the physical cash counted in the drawer:');
  if (entry === null) return;
  const counted_cash = Number(entry);
  if (!Number.isFinite(counted_cash) || counted_cash < 0) return;
  const r = await fetch('/api/till/close', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({location_id:activeLocationId, counted_cash})});
  const d = await r.json();
  if (!r.ok) return alert(d.message || 'Could not close till.');
  alert(`Till closed. Expected KSh ${d.expected_cash.toFixed(2)}, counted KSh ${d.counted_cash.toFixed(2)}, variance KSh ${d.variance.toFixed(2)}.`);
  await refreshTill();
}
const TILL_MOVE_LABELS = {PAID_IN:'Float added to counter', PAID_OUT:'Removed from counter', MPESA_DEPOSIT:'Deposited to M-Pesa'};
async function loadTillMovementHistory() {
  const el = document.getElementById('till-movement-history');
  if (!el) return;
  const r = await fetch('/api/till/cash-movements?location_id=' + encodeURIComponent(activeLocationId));
  const d = await r.json().catch(() => ({}));
  if (!r.ok) { el.textContent = d.message || 'Could not load till movements.'; return; }
  const rows = d.movements || [];
  el.innerHTML = rows.length ? rows.map(m => `${escHtml(m.created_at)} · ${escHtml(TILL_MOVE_LABELS[m.type] || m.type)} KSh ${Number(m.amount).toFixed(2)} · ${escHtml(m.reason)} · ${escHtml(m.created_by)}`).join('<br>') : 'No cash movements recorded.';
}
async function recordTillMovement() {
  const msg = document.getElementById('till-movement-message');
  const kind = document.getElementById('till-movement-type').value;
  const reason = document.getElementById('till-movement-reason').value.trim()
    || ({PAID_OUT:'Removed from counter', MPESA_DEPOSIT:'M-Pesa deposit', PAID_IN:'Float added to counter'}[kind]);
  const r = await fetch('/api/till/cash-movements', {method:'POST', headers:{'Content-Type':'application/json'},
    body:JSON.stringify({location_id:activeLocationId, movement_type:kind,
      amount:document.getElementById('till-movement-amount').value, reason:reason})});
  const d = await r.json().catch(() => ({}));
  if (!r.ok) { msg.textContent = d.message || 'Could not save cash movement.'; return; }
  msg.textContent = `Saved ${d.reference}. Expected till cash KSh ${Number(d.expected_cash).toFixed(2)}.`;
  document.getElementById('till-movement-amount').value = ''; document.getElementById('till-movement-reason').value = '';
  await refreshTill();
}

// ---- Catalogue cache: ONE server call per outlet, then search is instant and local. ----
let catalogCache = [], catalogLoadedAt = 0, catalogLoading = null, catalogSeq = 0, topItemsCache = [], quickItemPrefs = null;
const CATALOG_STALE_MS = 120000;
const SEARCH_ROW_LIMIT = 30;
function kes(n) { return Number(n || 0).toLocaleString('en-US', {maximumFractionDigits: 2}); }
function searchProductsSoon() { searchProducts(); }   // local filtering is instant; no debounce needed
function reloadCatalog() {
  const seq = ++catalogSeq;
  const loc = encodeURIComponent(activeLocationId || '');
  catalogLoading = (async () => {
    try {
      const [res, topRes] = await Promise.all([
        fetch(`/api/products/search?location_id=${loc}`, { credentials: 'same-origin' }),
        fetch(`/api/pos/top-items?location_id=${loc}`, { credentials: 'same-origin' }).catch(() => null)
      ]);
      const items = await res.json();
      if (!res.ok || !Array.isArray(items)) throw new Error((items && items.message) || 'Could not load items');
      if (seq !== catalogSeq) return;                      // outlet changed while loading
      catalogCache = items; catalogLoadedAt = Date.now();
      let top = [];
      try { top = (topRes && topRes.ok) ? await topRes.json() : []; } catch (_) {}
      topItemsCache = Array.isArray(top) ? top : [];
      try { quickItemPrefs = JSON.parse(localStorage.getItem(`eminingQuickItems:${activeLocationId}`) || 'null'); }
      catch (_) { quickItemPrefs = null; }
    } catch (e) {
      if (seq === catalogSeq && !catalogCache.length) {
        document.getElementById('search-results').innerHTML = `<div style="padding:10px;color:#b00;">Could not load items: ${escHtml(e.message)}</div>`;
      }
      return;
    } finally { if (seq === catalogSeq) catalogLoading = null; }
    renderQuickItems(); searchProducts();
  })();
  return catalogLoading;
}
document.addEventListener('visibilitychange', () => {
  if (!document.hidden && catalogLoadedAt && !catalogLoading && Date.now() - catalogLoadedAt > CATALOG_STALE_MS) reloadCatalog();
});
async function loadCategories() {
  const sel = document.getElementById('category-filter');
  if (!sel) return;
  try {
    const r = await fetch('/api/products/categories', { credentials: 'same-origin' });
    const cats = await r.json();
    if (!Array.isArray(cats)) return;
    const cur = sel.value;
    sel.innerHTML = '<option value="">All Categories</option>' + cats.map(c => `<option value="${escHtml(c)}">${escHtml(c)}</option>`).join('');
    sel.value = cur;
    renderPosCategoryChips();
  } catch (e) { /* keep whatever options are already in the page */ }
}
function renderPosCategoryChips() {
  const box = document.getElementById('pos-category-chips'), sel = document.getElementById('category-filter');
  if (!box || !sel) return;
  const active = sel.value || '';
  const values = Array.from(sel.options).map(o => ({value:o.value, label:o.textContent}));
  box.innerHTML = values.map(o => `<button type="button" class="pos-category-chip ${o.value === active ? 'is-active' : ''}" aria-pressed="${o.value === active}" data-category="${escHtml(o.value)}" onclick="choosePosCategory(this.dataset.category)">${escHtml(o.label)}</button>`).join('');
}
function choosePosCategory(category) {
  const select = document.getElementById('category-filter'); if (!select) return;
  select.value = category; renderPosCategoryChips(); searchProducts();
}
function setCatalogView(mode) {
  catalogViewMode = mode === 'list' ? 'list' : 'grid';
  const box = document.getElementById('search-results');
  const gridButton = document.getElementById('catalog-grid-btn');
  const listButton = document.getElementById('catalog-list-btn');
  if (box) box.classList.toggle('is-product-list', catalogViewMode === 'list');
  if (gridButton) gridButton.setAttribute('aria-pressed', String(catalogViewMode === 'grid'));
  if (listButton) listButton.setAttribute('aria-pressed', String(catalogViewMode === 'list'));
}
function searchProducts() {
  const div = document.getElementById('search-results');
  if (!catalogLoadedAt) { if (!catalogLoading) reloadCatalog(); div.innerHTML = '<div style="padding:10px;">Loading items…</div>'; return; }
  if (!catalogLoading && Date.now() - catalogLoadedAt > CATALOG_STALE_MS) reloadCatalog();   // quiet background refresh
  const words = (document.getElementById('search-input').value || '').trim().toLowerCase().split(/\s+/).filter(Boolean);
  const cat = (document.getElementById('category-filter').value || '').trim().toLowerCase();
  renderPosCategoryChips();
  const items = catalogCache.filter(i => {
    if (cat && (i.category || '').toLowerCase() !== cat) return false;
    const hay = ((i.name || '') + ' ' + (i.category || '')).toLowerCase();
    return words.every(w => hay.includes(w));
  });
  searchResultsCache = items;
  if (items.length === 0) { div.innerHTML = '<div style="padding:10px;">No items match.</div>'; return; }
  const shown = items.slice(0, SEARCH_ROW_LIMIT);
  div.innerHTML = shown.map(i => {
    const stock = Number(i.available_stock_kg || 0);
    const noStock = !i.is_service && stock <= 0;
    const packOptions = buildPackOptions(i);
    const smallestPack = packOptions.reduce((smallest, o) => Math.min(smallest, o.pack_kg), Infinity);
    const lowStock = !i.is_service && !noStock && Number.isFinite(smallestPack) && stock < smallestPack;
    let btns;
    if (!i.priced) btns = '<button class="btn-sm" disabled>No price</button>';
    else if (noStock) btns = '<button class="btn-sm" disabled>Out of stock</button>';
    else btns = packOptions.map(o => `<button type="button" class="pack-btn" aria-label="Add ${escHtml(i.name)}, ${escHtml(o.label)}, KSh ${kes(o.price)}" onclick="addToCart(${i.id}, ${o.pack_kg})"><span>${escHtml(o.label)}</span><strong>KSh ${kes(o.price)}</strong><span class="pack-add-mark" aria-hidden="true">+</span></button>`).join('');
    return `<article class="pos-product-card">
      <div class="product-card-meta"><span class="product-category">${escHtml(i.category)}</span><span class="product-stock ${noStock ? 'is-out' : (lowStock ? 'is-low' : 'is-ok')}">${i.is_service ? 'Service' : (noStock ? 'Out of stock' : `${stock.toFixed(2)} kg available`)}</span></div>
      <h3>${escHtml(i.name)}</h3>
      <p class="product-card-note">${i.is_service ? 'Charged per kilogram; customer grain is not company stock.' : (lowStock ? 'Low stock · less than one configured pack remains.' : 'Choose a configured pack size.')}</p>
      <div class="product-pack-options">${btns}</div></article>`;
  }).join('') + (items.length > shown.length ? `<div style="padding:8px;font-size:.85rem;color:#666;">Showing ${shown.length} of ${items.length}. Type more to narrow.</div>` : '');
  div.classList.add('is-product-grid');
  div.classList.toggle('is-product-list', catalogViewMode === 'list');
}
function renderQuickItems() {
  const box = document.getElementById('quick-items'), card = document.getElementById('quick-card');
  if (!box) return;
  const selected = Array.isArray(quickItemPrefs) ? quickItemPrefs : topItemsCache.map(t => ({id:t.ingredient_id, pack_kg:t.pack_kg}));
  const btns = [];
  selected.forEach(t => {
    const i = catalogCache.find(x => x.id === Number(t.id));
    if (!i || !i.priced) return;
    if (!i.is_service && Number(i.available_stock_kg || 0) <= 0) return;
    const opts = buildPackOptions(i); if (!opts.length) return;
    const o = opts.find(p => Math.abs(p.pack_kg - Number(t.pack_kg)) < 0.0005) || opts[0];
    btns.push(`<button class="quick-btn" onclick="addToCart(${i.id}, ${o.pack_kg})">${escHtml(i.name)}<small>${escHtml(o.label)} · KSh ${kes(o.price)}</small></button>`);
  });
  if (card) card.style.display = '';
  box.innerHTML = btns.length ? btns.join('') : '<p>No quick items selected yet. Use Edit Quick items to choose products.</p>';
}

function toggleQuickEditor(force) {
  const editor = document.getElementById('quick-items-editor');
  if (!editor) return;
  const open = force === undefined ? editor.hidden : !!force;
  editor.hidden = !open;
  if (open) renderQuickItemEditor();
}
function renderQuickItemEditor() {
  const box = document.getElementById('quick-items-editor-list');
  if (!box) return;
  const selected = Array.isArray(quickItemPrefs) ? quickItemPrefs : topItemsCache.map(t => ({id:t.ingredient_id, pack_kg:t.pack_kg}));
  const byId = new Map(selected.map(t => [Number(t.id), Number(t.pack_kg)]));
  const candidates = catalogCache.filter(i => i.priced && (i.is_service || Number(i.available_stock_kg || 0) > 0));
  box.innerHTML = candidates.map(i => {
    const opts = buildPackOptions(i), current = byId.get(i.id);
    const options = opts.map(o => `<option value="${o.pack_kg}" ${Math.abs(o.pack_kg - (current || opts[0]?.pack_kg || 0)) < 0.0005 ? 'selected' : ''}>${escHtml(o.label)} · KSh ${kes(o.price)}</option>`).join('');
    return `<label class="quick-editor-row"><input type="checkbox" class="quick-editor-check" value="${i.id}" ${byId.has(i.id) ? 'checked' : ''}><span>${escHtml(i.name)}</span><select class="quick-editor-pack" data-item-id="${i.id}">${options}</select></label>`;
  }).join('') || '<p>No priced, in-stock products available.</p>';
}
function saveQuickItems() {
  const rows = Array.from(document.querySelectorAll('#quick-items-editor-list .quick-editor-check:checked'));
  const msg = document.getElementById('quick-items-editor-message');
  if (rows.length > 12) { if (msg) msg.textContent = 'Select no more than 12 items.'; return; }
  quickItemPrefs = rows.map(check => ({id:Number(check.value), pack_kg:Number(document.querySelector(`.quick-editor-pack[data-item-id="${check.value}"]`)?.value || 1)}));
  try { localStorage.setItem(`eminingQuickItems:${activeLocationId}`, JSON.stringify(quickItemPrefs)); }
  catch (_) { if (msg) msg.textContent = 'Could not save on this device. Check browser storage settings.'; return; }
  renderQuickItems(); toggleQuickEditor(false);
}
function resetQuickItems() {
  quickItemPrefs = null;
  try { localStorage.removeItem(`eminingQuickItems:${activeLocationId}`); } catch (_) {}
  renderQuickItems(); renderQuickItemEditor();
}

function buildPackOptions(item) {
  const out = [];
  if ((item.retail_price_kg || 0) > 0) out.push({ pack_kg: 1, price: item.retail_price_kg, label: 'KG' });
  (item.packs || []).forEach(p => out.push({ pack_kg: p.pack_kg, price: p.price, label: p.pack_kg + ' kg bag' }));
  return out;
}
// Warn at tap time, not at the end of the queue. The server still re-checks stock at checkout.
function stockAllows(item, packKg, qty, excludeIdx) {
  if (item.is_service) return true;
  let kg = packKg * qty;
  cart.forEach((c, i) => { if (c.id === item.id && i !== excludeIdx) kg += c.qty * c.bag_size_kg; });
  const avail = Number(item.available_stock_kg || 0);
  if (kg > avail + 0.0001) { alert(`Only ${avail.toFixed(2)} kg of ${item.name} in stock at this outlet.`); return false; }
  return true;
}
function addToCart(itemId, packKg) {
  const item = catalogCache.find(i => i.id === itemId);
  if (!item) return;
  const options = buildPackOptions(item);
  if (options.length === 0) return alert('This item has no price set.');
  const chosen = (packKg != null && options.find(o => Math.abs(o.pack_kg - packKg) < 0.0005)) || options[0];
  const existing = cart.find(c => c.id === item.id && c.bag_size_kg === chosen.pack_kg);
  if (!stockAllows(item, chosen.pack_kg, (existing ? existing.qty : 0) + 1, existing ? cart.indexOf(existing) : -1)) return;
  if (existing) {
      existing.qty += 1;
  } else {
      cart.push({ id: item.id, name: item.name, options: options, bag_size_kg: chosen.pack_kg,
                  unit_type: chosen.pack_kg === 1 ? 'KG' : 'BAG', unit_price: chosen.price, qty: 1 });
  }
  renderCart();
}
function bumpQty(idx, delta) {
  const c = cart[idx]; if (!c) return;
  const next = Math.max(1, c.qty + delta);
  if (delta > 0) { const item = catalogCache.find(i => i.id === c.id); if (item && !stockAllows(item, c.bag_size_kg, next, idx)) return; }
  c.qty = next; renderCart();
}
function updateCartUnit(idx, packKg) {
  const line = cart[idx];
  const opt = line.options.find(o => o.pack_kg === parseFloat(packKg));
  if (!opt) return;
  line.bag_size_kg = opt.pack_kg;
  line.unit_type = opt.pack_kg === 1 ? 'KG' : 'BAG';
  line.unit_price = opt.price;
  renderCart();
}

function calculateChange() {
    let totalDue = parseFloat(document.getElementById('cart-total').textContent) || 0;
    let totalPaid = 0;
    document.querySelectorAll('.p-amount').forEach((input) => {
        let val = parseFloat(input.value) || 0;
        totalPaid += val;
    });
    let change = totalPaid - totalDue;
    document.getElementById('change-due').textContent = Math.max(0, change).toFixed(2);
}

function renderCart() {
  if (tenderState) { tenderState = null; resetPaymentLines(); }   // total changed: pick the payment again
  const tbody = document.getElementById('cart-body');
  const itemCount = cart.reduce((sum, item) => sum + (Number(item.qty) || 0), 0);
  const countLabel = itemCount + (itemCount === 1 ? ' item' : ' items');
  const stageCount = document.getElementById('pos-cart-count'); if (stageCount) stageCount.textContent = itemCount;
  const stickyCount = document.getElementById('sticky-count'); if (stickyCount) stickyCount.textContent = countLabel;
  const desktopCount = document.getElementById('pos-cart-count-desktop');
  if (desktopCount) desktopCount.textContent = countLabel;
  const cartCount = document.getElementById('pos-cart-count');
  if (cartCount) cartCount.textContent = itemCount;
  tbody.innerHTML = '';
  let subtotal = 0;
  if (cart.length === 0) {
    tbody.innerHTML = '<tr><td colspan="6" style="text-align: center; color: #888; padding: 20px;">Cart is empty.</td></tr>';
    document.getElementById('cart-total').textContent = '0.00';
    const sticky = document.getElementById('sticky-total'); if (sticky) sticky.textContent = '0.00';
    calculateChange();
    renderTenderBar();
    return;
  }
  cart.forEach((c, idx) => {
    const lineTotal = c.qty * c.unit_price;
    subtotal += lineTotal;
    const opts = c.options.map(o =>
      `<option value="${o.pack_kg}" ${o.pack_kg === c.bag_size_kg ? 'selected' : ''}>${escHtml(o.label)}</option>`).join('');
    tbody.innerHTML += `<tr>
      <td><b>${escHtml(c.name)}</b></td>
      <td><div class="qty-step"><button type="button" aria-label="Decrease ${escHtml(c.name)} quantity" onclick="bumpQty(${idx}, -1)">−</button><input type="number" aria-label="${escHtml(c.name)} quantity" inputmode="decimal" value="${c.qty}" step="any" min="0.1" onchange="cart[${idx}].qty=Math.max(0.1, parseFloat(this.value)||1); renderCart()" /><button type="button" aria-label="Increase ${escHtml(c.name)} quantity" onclick="bumpQty(${idx}, 1)">+</button></div></td>
      <td>
        <select onchange="updateCartUnit(${idx}, this.value)" style="height: 50px; font-size: 16px; width: 100%; min-width: 120px;">${opts}</select>
      </td>
      <td style="font-size: 16px;">${c.unit_price.toFixed(2)}</td>
      <td style="font-size: 16px;"><b>${lineTotal.toFixed(2)}</b></td>
      <td><button class="btn-sm btn-danger cart-remove" aria-label="Remove ${escHtml(c.name)}" onclick="cart.splice(${idx}, 1); renderCart()">Remove</button></td>
    </tr>`;
  });
  let discVal = parseFloat(document.getElementById('discount-val').value) || 0;
  let discType = document.getElementById('discount-type').value;
  let absoluteDisc = discType === 'PCT' ? (subtotal * (discVal / 100)) : discVal;
  document.getElementById('cart-total').textContent = Math.max(0, subtotal - absoluteDisc).toFixed(2);
  const sticky = document.getElementById('sticky-total'); if (sticky) sticky.textContent = document.getElementById('cart-total').textContent;
  calculateChange();
  renderTenderBar();
}
function clearCart() { 
    cart = []; 
    renderCart();
    document.querySelectorAll('.p-amount').forEach(i => i.value = ''); 
    calculateChange();
}

function holdCart() { 
    if(cart.length === 0) return alert("Cart is empty"); 
    heldCart = [...cart]; 
    clearCart(); 
    document.getElementById('btn-hold').style.display = 'none';
    document.getElementById('btn-restore').style.display = 'inline-block';
}

function restoreCart() { 
    if(heldCart.length === 0) return alert("No held sale found."); 
    cart = [...heldCart]; 
    heldCart = []; 
    renderCart(); 
    document.getElementById('btn-hold').style.display = 'inline-block';
    document.getElementById('btn-restore').style.display = 'none';
}

function paymentRowHtml() {
    return `<div class="split-row" style="margin-bottom: 10px; flex-wrap: wrap;"><select class="p-method" onchange="onPayMethodChange(this)" style="flex: 1; height: 50px; font-size: 16px; padding: 10px;"><option value="CASH">Cash</option><option value="MPESA">M-Pesa</option><option value="BANK">Bank</option><option value="CREDIT">Credit</option></select><input type="number" min="0" class="p-amount" placeholder="Enter amount paid..." oninput="calculateChange()" style="flex: 2; height: 50px; font-size: 18px; padding: 10px;" /><input type="number" min="0" step="0.01" class="p-fee" placeholder="Deduction (KSh)" title="M-Pesa / bank charge deducted from this payment" style="flex: 1; height: 50px; font-size: 16px; padding: 10px; display: none;" /><input type="text" class="p-ref" placeholder="Ref (Optional)" style="flex: 1; height: 50px; font-size: 16px; padding: 10px; display: none;" /></div>`;
}
function onPayMethodChange(select) {
    const fee = select.closest('.split-row').querySelector('.p-fee');
    const show = select.value === 'MPESA' || select.value === 'BANK';
    fee.style.display = show ? '' : 'none';
    if (!show) fee.value = '';
    const ref = select.closest('.split-row').querySelector('.p-ref');
    ref.style.display = show ? '' : 'none';
    ref.placeholder = show ? 'M-Pesa / bank code' : 'Ref (Optional)';
    if (!show) ref.value = '';
}
// ---- One-tap payment buttons: fill the payment row so the cashier only presses Complete Sale. ----
let tenderState = null;
function tenderTotal() { return Math.round((parseFloat(document.getElementById('cart-total').textContent) || 0) * 100) / 100; }
function renderTenderBar() {
  const bar = document.getElementById('tender-bar'); if (!bar) return;
  const total = tenderTotal();
  if (cart.length === 0 || total <= 0) { bar.innerHTML = '<div style="color:#888;padding:6px;">Add items to see payment buttons.</div>'; return; }
  const on = (m, a) => tenderState && tenderState.method === m && Math.abs(tenderState.amount - a) < 0.005 ? ' active' : '';
  const notes = [];
  [100, 500, 1000, 5000].forEach(step => { const n = Math.ceil(total / step) * step; if (n > total + 0.001 && !notes.includes(n)) notes.push(n); });
  let html = `<button type="button" class="tender-btn${on('CASH', total)}" onclick="quickTender('CASH', ${total})">Exact cash<br/><small>KSh ${kes(total)}</small></button>`;
  notes.slice(0, 3).forEach(n => { html += `<button type="button" class="tender-btn${on('CASH', n)}" onclick="quickTender('CASH', ${n})">Cash ${kes(n)}<br/><small>change ${kes(n - total)}</small></button>`; });
  html += `<button type="button" class="tender-btn mpesa${on('MPESA', total)}" onclick="quickTender('MPESA', ${total})">M-Pesa<br/><small>KSh ${kes(total)}</small></button>`;
  if (document.getElementById('customer-select').value) html += `<button type="button" class="tender-btn credit${on('CREDIT', total)}" onclick="quickTender('CREDIT', ${total})">On credit<br/><small>KSh ${kes(total)}</small></button>`;
  bar.innerHTML = html;
}
function quickTender(method, amount) {
  resetPaymentLines();
  const row = document.querySelector('#payment-lines .split-row');
  const sel = row.querySelector('.p-method'); sel.value = method; onPayMethodChange(sel);
  row.querySelector('.p-amount').value = amount;
  tenderState = {method: method, amount: amount};
  calculateChange(); renderTenderBar();
  if (method === 'MPESA' || method === 'BANK') row.querySelector('.p-ref').focus();
}
function resetPaymentLines() {
    document.getElementById('payment-lines').innerHTML = paymentRowHtml();
    calculateChange();
}
function addPaymentLine() {
    document.getElementById('payment-lines').insertAdjacentHTML('beforeend', paymentRowHtml());
}
// Clears everything that belongs to ONE sale: customer, discount, payment rows.
function resetSaleForm() {
    document.getElementById('customer-select').value = '';
    updateCustomerDetails();
    document.getElementById('repay-amount').value = '';
    document.getElementById('discount-val').value = 0;
    document.getElementById('discount-type').value = 'KSH';
    ['discount-reason', 'discount-approver', 'new-cust-phone', 'new-cust-name'].forEach(id => { const e = document.getElementById(id); if (e) e.value = ''; });
    const customerSearch = document.getElementById('customer-search'); if (customerSearch) { customerSearch.value = ''; filterCustomerOptions(); }
    resetPaymentLines();
    renderCart();
}
function cancelSale() {
    clearCart();
    resetSaleForm();
}
let checkoutInFlight = false;
function newCheckoutUuid() {
  if (window.crypto && typeof window.crypto.randomUUID === 'function') return window.crypto.randomUUID();
  const b = new Uint8Array(16);
  window.crypto.getRandomValues(b);
  b[6] = (b[6] & 0x0f) | 0x40; b[8] = (b[8] & 0x3f) | 0x80;
  return [...b].map((v, i) => ([4, 6, 8, 10].includes(i) ? '-' : '') + v.toString(16).padStart(2, '0')).join('');
}

async function completeCheckout() {
  if (checkoutInFlight) return;
  if (pendingCheckoutAttempt && pendingCheckoutAttempt.owner !== window.currentUsername) {
    return alert('A checkout from another cashier has an unresolved response in this browser session. Ask that cashier to retry it before switching users.');
  }
  let attempt = pendingCheckoutAttempt;
  if (!attempt) {
    if (cart.length === 0) return alert('Cart is empty!');
    const custId = document.getElementById('customer-select').value;
    const rawSubtotal = cart.reduce((sum, c) => sum + (c.qty * c.unit_price), 0);
    const discVal = parseFloat(document.getElementById('discount-val').value) || 0;
    const discType = document.getElementById('discount-type').value;
    const absoluteDisc = discType === 'PCT' ? (rawSubtotal * (discVal / 100)) : discVal;
    if (absoluteDisc < 0) return alert('Negative discounts are strictly prohibited.');
    const discReason = document.getElementById('discount-reason').value;
    const discApprover = document.getElementById('discount-approver').value.trim();
    if (absoluteDisc > 0 && !discReason) return alert('Choose why this discount is given.');
    if (absoluteDisc > rawSubtotal * 0.02 + 0.01 && !discApprover && window.currentRole !== 'admin') return alert('A discount above 2% needs the name of the person who approved it.');
    const newPhone = custId ? '' : document.getElementById('new-cust-phone').value.trim();
    const totalDue = Math.max(0, rawSubtotal - absoluteDisc);
    if (totalDue <= 0) return alert('Cannot complete sale: total amount due is KSh 0.00. Check that items have prices.');
    let explicitTotal = 0, emptyCreditRow = null;
    document.querySelectorAll('.split-row').forEach(row => {
      const method = row.querySelector('.p-method').value;
      const amt = parseFloat(row.querySelector('.p-amount').value) || 0;
      explicitTotal += amt;
      if (method === 'CREDIT' && amt === 0) emptyCreditRow = row;
    });
    if (emptyCreditRow && explicitTotal < totalDue) {
      emptyCreditRow.querySelector('.p-amount').value = parseFloat((totalDue - explicitTotal).toFixed(2));
      calculateChange();
    }
    const payments = [];
    let isValid = true, totalEntered = 0, negativeFee = false, feeTooBig = false;
    document.querySelectorAll('.split-row').forEach(row => {
      const amtStr = row.querySelector('.p-amount').value;
      if (amtStr && parseFloat(amtStr) > 0) {
        const amt = parseFloat(amtStr);
        if (amt < 0) isValid = false;
        const method = row.querySelector('.p-method').value;
        const fee = (method === 'MPESA' || method === 'BANK') ? (parseFloat(row.querySelector('.p-fee').value) || 0) : 0;
        if (fee < 0) { isValid = false; negativeFee = true; }
        if (fee > amt) feeTooBig = true;
        payments.push({payment_method:method, amount:amt, fee:fee,
          reference:row.querySelector('.p-ref').value});
        totalEntered += amt;
      }
    });
    if (negativeFee) return alert('M-Pesa/bank deduction cannot be negative.');
    if (!isValid) return alert('Negative payments are strictly prohibited.');
    if (feeTooBig) return alert('M-Pesa/bank deduction cannot be more than the amount paid on that line.');
    if (totalEntered < totalDue - 0.01) return alert(`Payment incomplete! You are short by KSh ${(totalDue - totalEntered).toFixed(2)}.`);
    const cartPayload = cart.map(c => ({ingredient_id:c.id, unit_type:c.unit_type, qty:c.qty, bag_size_kg:c.bag_size_kg, expected_unit_price:Number(c.unit_price.toFixed(2))}));
    const cartFingerprint = JSON.stringify(cart.map(c => ({ingredient_id:c.id, unit_type:c.unit_type, qty:c.qty, bag_size_kg:c.bag_size_kg})));
    attempt = {key:newCheckoutUuid(), owner:window.currentUsername,
      cartFingerprint:cartFingerprint, payload:{customer_id:custId ? parseInt(custId) : null,
        location_id:activeLocationId, discount_amount:absoluteDisc, discount_reason:discReason, discount_approver:discApprover,
        expected_total_amount:Number(totalDue.toFixed(2)), customer_phone:newPhone,
        customer_name:newPhone ? document.getElementById('new-cust-name').value.trim() : '', cart:cartPayload, payments}};
    pendingCheckoutAttempt = attempt;
    try { sessionStorage.setItem('pendingCheckoutAttempt', JSON.stringify(attempt)); } catch (_) {}
  }
  if (!navigator.onLine) {
    await saveOfflineCheckout(attempt);
    return;
  }
  attempt.sentOnce = true;      // once a request may have reached the server its payload must never change (idempotency hash)
  try { sessionStorage.setItem('pendingCheckoutAttempt', JSON.stringify(attempt)); } catch (_) {}
  checkoutInFlight = true;
  try {
      const res = await fetch('/api/pos/checkout', {
          method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'Idempotency-Key':attempt.key },
          body: JSON.stringify(attempt.payload)
      });
      if (res.status === 401) {
          return alert('Session expired. The sale was not accepted. Sign in again, then retry this checkout with the same key.');
      }
      if (res.status === 403) {
          pendingCheckoutAttempt = null;
          try { sessionStorage.removeItem('pendingCheckoutAttempt'); } catch (_) {}
          return alert('Your role is not allowed to complete this sale.');
      }
      const data = await res.json();
      if (data.status === 'success') {
          pendingCheckoutAttempt = null;
          try { sessionStorage.removeItem('pendingCheckoutAttempt'); } catch (_) {}
          await loadCustomers();
          renderReceipt(data.data, attempt.payload);
          if (JSON.stringify(cart.map(c => ({ingredient_id:c.id, unit_type:c.unit_type, qty:c.qty, bag_size_kg:c.bag_size_kg}))) === attempt.cartFingerprint) { clearCart(); resetSaleForm(); }
          else alert('The earlier checkout was confirmed. Your current cart was kept separate.');
          reloadCatalog();   // refresh stock after the sale
          if (typeof loadSalesHistory === 'function') loadSalesHistory();
      } else {
          if (res.status === 400) {
              pendingCheckoutAttempt = null;
              try { sessionStorage.removeItem('pendingCheckoutAttempt'); } catch (_) {}
          }
          alert('Sale failed: ' + data.message + (res.status === 409 ? ' Keep this tab open and contact an administrator before retrying.' : ''));
      }
  } catch(err) {
      await saveOfflineCheckout(attempt, true);
  } finally { checkoutInFlight = false; }
}
async function saveOfflineCheckout(attempt, uncertain) {
  try {
    if (!uncertain && !attempt.sentOnce && !attempt.payload.offline_sold_at) attempt.payload.offline_sold_at = new Date().toISOString();
    await OfflineStore.enqueue({key:attempt.key, owner:attempt.owner, createdAt:new Date().toISOString(), payload:attempt.payload, uncertain:!!uncertain});
    if (pendingCheckoutAttempt && pendingCheckoutAttempt.key === attempt.key) {
      pendingCheckoutAttempt = null;
      try { sessionStorage.removeItem('pendingCheckoutAttempt'); } catch (_) {}
      clearCart(); resetSaleForm();
    }
    await renderOfflineSales();
    alert((uncertain ? 'The server response was lost. ' : '') + 'Sale saved on this phone as PENDING. It is not yet posted or an official receipt. Reconnect and sign in as ' + attempt.owner + ' to sync it.');
  } catch (e) {
    alert('This phone could not save the offline sale. Do not re-enter it after an uncertain response. Record it on paper and contact the administrator.');
  }
}
async function renderOfflineSales() {
  const panel = document.getElementById('offline-sales-panel'); if (!panel || !window.OfflineStore) return;
  const rows = await OfflineStore.list().catch(() => []);
  panel.style.display = rows.length || offlineSyncNotice ? '' : 'none';
  const list = document.getElementById('offline-sales-list');
  if (!list) return;
  list.innerHTML = (offlineSyncNotice ? `<div style="padding:8px 0">${escHtml(offlineSyncNotice)}</div>` : '') + rows.map(s => {
    const total = Number(s.payload.expected_total_amount || 0);
    return `<div style="padding:8px 0;border-top:1px solid #ddd"><b>PENDING · KSh ${Math.max(0,total).toFixed(2)}</b> · ${escHtml(new Date(s.createdAt).toLocaleString())} · ${escHtml(s.owner)}${s.error ? `<br><span>${escHtml(s.error)}</span>` : ''}${s.error && s.status === 400 ? `<br><button type="button" onclick="syncOfflineSales()">Retry</button> <button type="button" onclick="discardOfflineSale('${escHtml(s.key)}')">Discard…</button>` : ''}</div>`;
  }).join('');
  const button = document.getElementById('offline-sync-button');
  if (button) { button.disabled = !navigator.onLine || !rows.length; button.style.display = rows.length ? '' : 'none'; }
}
async function discardOfflineSale(key) {
  // Only a sale the server explicitly refused (HTTP 400) can be discarded. A lost-response or conflict sale may already be posted.
  const sale = (await OfflineStore.list()).find(s => s.key === key);
  if (!sale || sale.status !== 400) return alert('Only a sale the server rejected can be discarded. Retry it, or contact an administrator.');
  const total = Number(sale.payload.expected_total_amount || 0).toFixed(2);
  const reason = (prompt(`Discard this pending sale of KSh ${total}?\nServer said: ${sale.error}\n\nThe goods and any cash/M-Pesa collected must be reconciled by hand. Type the reason (min 5 characters):`) || '').trim();
  if (reason.length < 5) return;
  await OfflineStore.logDiscard({key:sale.key, owner:sale.owner, soldAt:sale.createdAt, total:Number(total), error:sale.error, reason:reason, discardedAt:new Date().toISOString()});
  await OfflineStore.remove(sale.key);
  await renderOfflineSales();
  alert('Sale discarded on this phone. Write it in the paper record now and tell the administrator.');
}
let offlineSyncRunning = false;
let offlineSyncNotice = '';
async function syncOfflineSales() {
  if (offlineSyncRunning || !navigator.onLine || !window.currentUsername || !window.OfflineStore) return;
  offlineSyncRunning = true;
  try {
    let confirmed = 0;
    const rows = (await OfflineStore.list()).sort((a,b) => a.createdAt.localeCompare(b.createdAt));
    for (const sale of rows) {
      if (sale.owner !== window.currentUsername) continue;
      try {
        const res = await fetch('/api/pos/checkout', {method:'POST', credentials:'same-origin',
          headers:{'Content-Type':'application/json','Idempotency-Key':sale.key}, body:JSON.stringify(sale.payload)});
        const data = await res.json().catch(() => ({}));
        if (res.ok && data.status === 'success') {
          await OfflineStore.remove(sale.key);
          confirmed += 1;
        } else {
          sale.error = data.message || `Server rejected sale (${res.status}).`;
          sale.status = res.status;
          await OfflineStore.enqueue(sale);
          if (res.status === 401 || res.status === 403 || res.status >= 500) break;
        }
      } catch (_) { break; }
    }
    if (confirmed) offlineSyncNotice = `${confirmed} saved sale${confirmed === 1 ? '' : 's'} confirmed by the server. Check Sales History for receipts.`;
  } finally {
    offlineSyncRunning = false;
    await renderOfflineSales();
  }
}
window.addEventListener('online', syncOfflineSales);
window.addEventListener('online', renderOfflineSales);
function renderReceipt(data, payload) {
  const completedAt = data.completed_at ? new Date(data.completed_at) : new Date();
  document.getElementById('r-date').textContent = Number.isNaN(completedAt.getTime()) ? new Date().toLocaleString() : completedAt.toLocaleString();
  document.getElementById('r-id').textContent = data.receipt_no || data.sale_id;
  window.lastReceiptOrderId = data.order_id;
  
  const cSel = document.getElementById('customer-select');
  const custId = cSel.value;
  let customerDisplayText = data.customer_name || "Walk-in customer";
  let debtText = "";

  if (custId) {
      const cust = customersCache.find(c => c.id == custId);
      if (cust) {
          customerDisplayText = cust.name;
          debtText = `<br/>Remaining Debt: KSh ${cust.balance.toFixed(2)}`;
      }
  }

  document.getElementById('r-cust').innerHTML = `<strong>${escHtml(customerDisplayText)}</strong>${debtText}`;
  document.getElementById('r-branch').textContent = data.location_name || '---';
  document.getElementById('r-cashier').textContent = data.cashier_name || '---';
  document.getElementById('r-total').textContent = data.total_amount.toFixed(2); 
  
  const linesDiv = document.getElementById('r-lines');
  linesDiv.innerHTML = ''; 
  data.items.forEach(i => { 
      linesDiv.innerHTML += `<div style="display:flex; justify-content:space-between; margin-bottom: 4px;"><span>${i.qty_entered} ${i.unit} x ${escHtml(i.name)}</span><span>KSh ${i.subtotal.toFixed(2)}</span></div>`; 
  });

  const paymentDiv = document.getElementById('r-payments');
  paymentDiv.innerHTML = '';
  (data.payments || []).forEach(payment => {
    const method = escHtml(String(payment.method || '').replace('_', ' '));
    const reference = payment.reference ? ` · Ref ${escHtml(payment.reference)}` : '';
    const fee = Number(payment.fee || 0);
    const feeText = fee > 0 ? ` (fee KSh ${fee.toFixed(2)})` : '';
    paymentDiv.innerHTML += `<div style="display:flex; justify-content:space-between;"><span>${method}${reference}${feeText}</span><span>KSh ${Number(payment.amount || 0).toFixed(2)}</span></div>`;
  });

  linesDiv.innerHTML += `<hr style="border-top: 1px dashed #333; margin: 5px 0;"/>`;
  linesDiv.innerHTML += `<div style="display:flex; justify-content:space-between;"><span>Amount Paid:</span><span>KSh ${data.paid_amount.toFixed(2)}</span></div>`;
  linesDiv.innerHTML += `<div style="display:flex; justify-content:space-between;"><span>Change Due:</span><span>KSh ${data.change_due.toFixed(2)}</span></div>`;

  const printBtn = document.getElementById('btn-print');
  const waBtn = document.getElementById('btn-wa');
  if(printBtn) {
      printBtn.disabled = false;
      printBtn.onclick = printReceiptOnly; // Binds the new print logic
  }
  if(waBtn) {
      waBtn.disabled = false;
      waBtn.onclick = () => window.lastReceiptOrderId ? shareReceiptImage(window.lastReceiptOrderId) : shareWhatsApp();
  }
  if (typeof window.setPosStage === 'function') window.setPosStage('receipt');
}

// 1. Opens WhatsApp in a New Tab
function shareWhatsApp() {
    let text = "EMINING FEEDS\nQuality Feeds For You\n--------------------\n";
    text += "Date: " + document.getElementById('r-date').innerText + "\n";
    text += "Receipt: " + document.getElementById('r-id').innerText + "\n";
    
    let custText = document.getElementById('r-cust').innerText.replace(/\n/g, ' - ');
    text += "Customer: " + custText + "\n--------------------\n";
    
    const itemNodes = document.getElementById('r-lines').childNodes;
    itemNodes.forEach(node => {
        if(node.innerText && !node.innerText.includes('---')) {
            text += node.innerText.replace('\n', ' - ') + "\n";
        }
    });
    
    text += "--------------------\nTOTAL: KSh " + document.getElementById('r-total').innerText + "\n\nThank you!";
    const encoded = encodeURIComponent(text);
    
    // This forces it to open in a new tab instead of closing the POS
    window.open(`https://wa.me/?text=${encoded}`, '_blank');
}

// 2. Prints ONLY the receipt (Thermal Printer Style)
function printReceiptOnly() {
    const printContent = `
        <html><head><style>
            body { font-family: monospace; width: 300px; margin: 0 auto; padding: 20px; }
            h3, p { text-align: center; margin: 5px 0; }
            hr { border-top: 1px dashed #000; margin: 10px 0; }
            .flex-row { display: flex; justify-content: space-between; margin-bottom: 4px; }
        </style></head><body>
            <h3>EMINING FEEDS</h3>
            <p>Quality Feeds For You</p>
            <hr>
            <div>Date: ${document.getElementById('r-date').innerText}</div>
            <div>Receipt: ${escHtml(document.getElementById('r-id').innerText)}</div>
            <div>Customer: ${escHtml(document.getElementById('r-cust').innerText)}</div>
            <hr>
            ${document.getElementById('r-lines').innerHTML}
            <hr>
            <h3>TOTAL: KSh ${document.getElementById('r-total').innerText}</h3>
        </body></html>
    `;
    const printWindow = window.open('', '', 'width=400,height=600');
    printWindow.document.write(printContent);
    printWindow.document.close();
    printWindow.focus();
    printWindow.print();
    printWindow.close();
}

async function loadCustomers() {
  const q = window.activeLocationId ? '?location_id=' + encodeURIComponent(window.activeLocationId) : '';
  const sel = document.getElementById('customer-select');
  if (!sel) return;
  const currentSelVal = sel.value;
  try {
    const res = await fetch('/api/customers' + q, { credentials: 'same-origin' });
    const data = await res.json();
    customersCache = (res.ok && Array.isArray(data)) ? data : [];
    if (!res.ok) throw new Error((data && data.message) || ('HTTP ' + res.status));
  } catch (e) {
    customersCache = [];
    sel.innerHTML = '<option value="">CASH CUSTOMER (Walk-In)</option><option disabled>Could not load customers - ' + escHtml(e.message) + '</option>';
    return;
  }
  renderCustomerOptions(currentSelVal);
  updateCustomerDetails();
}

function renderCustomerOptions(selectedId) {
  const sel = document.getElementById('customer-select'); if (!sel) return;
  const query = (document.getElementById('customer-search')?.value || '').trim().toLowerCase();
  let matches = customersCache.filter(c => !query || `${c.name || ''} ${c.phone || ''} ${c.location || ''}`.toLowerCase().includes(query));
  if (selectedId && !matches.some(c => String(c.id) === String(selectedId))) {
    const selected = customersCache.find(c => String(c.id) === String(selectedId)); if (selected) matches = [selected, ...matches];
  }
  let html = '<option value="">Walk-in customer</option>';
  matches.forEach(c => {
    const locText = c.location && c.location !== 'Unknown' ? ` · ${escHtml(c.location)}` : '';
    const phone = c.phone ? ` · ${escHtml(c.phone)}` : '';
    html += `<option value="${c.id}">${escHtml(c.name)}${phone}${locText} · Balance KSh ${Number(c.balance || 0).toFixed(2)}</option>`;
  });
  if (query && !matches.length) html += '<option disabled>No customers match this search</option>';
  sel.innerHTML = html;
  if (selectedId) sel.value = selectedId;
}
function filterCustomerOptions() {
  const selectedId = document.getElementById('customer-select')?.value || '';
  renderCustomerOptions(selectedId);
}

function updateCustomerDetails() {
  if (typeof renderTenderBar === 'function') renderTenderBar();
  const selId = document.getElementById('customer-select').value;
  const card = document.getElementById('customer-credit-card');
  if (!selId) { card.style.display = 'none'; return; }
  const cust = customersCache.find(c => c.id == selId);
  if (cust) {
    const limit = Number(cust.credit_limit || 0), debt = Number(cust.balance || 0);
    document.getElementById('c-limit').textContent = limit.toFixed(2);
    document.getElementById('c-debt').textContent = debt.toFixed(2);
    document.getElementById('c-avail').textContent = (limit - debt).toFixed(2);
    card.style.display = 'block';
  }
}

async function saveNewCustomer() {
    const name = document.getElementById('nc-name').value;
    const phone = document.getElementById('nc-phone').value;
    const location = document.getElementById('nc-location').value;
    const limit = Math.max(0, parseFloat(document.getElementById('nc-limit').value) || 0);
    if(!name) return alert("Name is required");
    const res = await fetch('/api/customers', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({name: name, phone: phone, location: location, credit_limit: limit, location_id: window.activeLocationId})
    });
    const data = await res.json();
    if(data.status === 'success') {
        await loadCustomers();
        document.getElementById('customer-select').value = data.customer_id;
        updateCustomerDetails();
        document.getElementById('new-cust-form').style.display = 'none';
        document.getElementById('nc-name').value = ''; document.getElementById('nc-phone').value = ''; document.getElementById('nc-location').value = ''; document.getElementById('nc-limit').value = '';
    } else {
        alert("Failed to save customer");
    }
}

async function processDebtRepayment() {
    const custId = document.getElementById('customer-select').value;
    const amt = parseFloat(document.getElementById('repay-amount').value);
    
    if(!custId) return alert("Select a customer first.");
    if(!amt || amt <= 0) return alert("Enter a valid repayment amount.");

    try {
        const res = await fetch(`/api/customers/${custId}/repay`, {
            method: 'POST', 
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ amount: amt, payment_method: document.getElementById('repay-method').value,
              location_id: activeLocationId })
        });
        const data = await res.json();
        
        if(data.status === 'success') {
            alert(`Repayment successful! New balance: KSh ${data.new_balance.toFixed(2)}`);
            document.getElementById('repay-amount').value = '';
            await refreshTill();
            await loadCustomers(); 
        } else {
            alert('Repayment Failed: ' + data.message);
        }
    } catch(e) {
        alert('System Error during repayment.');
    }
}

async function recordOldDebt() {
    const custId = document.getElementById('customer-select').value;
    const amt = parseFloat(document.getElementById('olddebt-amount').value);
    const note = document.getElementById('olddebt-note').value.trim();
    if (!custId) return alert('Select a customer first.');
    if (!amt || amt <= 0) return alert('Enter the amount owed.');
    if (note.length < 3) return alert('Say what this old debt is for.');
    if (!confirm('Add KSh ' + amt.toFixed(2) + ' of debt to this customer?\n\nThis is recorded under your name and cannot be edited.')) return;
    try {
        const res = await fetch(`/api/customers/${custId}/add-debt`, {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ amount: amt, note: note, location_id: activeLocationId })
        });
        const data = await res.json();
        if (data.status === 'success') {
            alert(`Debt recorded. New balance: KSh ${data.new_balance.toFixed(2)}`);
            document.getElementById('olddebt-amount').value = '';
            document.getElementById('olddebt-note').value = '';
            await loadCustomers();
        } else {
            alert('Could not record debt: ' + data.message);
        }
    } catch (e) {
        alert('System error recording debt. Check the connection and try again.');
    }
}

let salesHistoryByOrder = {};
let salesFilter = {period: '', creditOnly: false, sort: 'date'};
let salesOffset = 0;
let salesLoadTicket = 0;
const SALES_PERIOD_LABELS = {today:'Today', yesterday:'Yesterday', this_week:'This week', this_month:'This month', this_year:'This year'};
function setSalesPeriod(k) { salesFilter.period = (salesFilter.period === k) ? '' : k; loadSalesHistory(); }
function clearSalesPeriod() { salesFilter.period = ''; salesFilter.creditOnly = false; salesFilter.sort = 'date';
  const c = document.getElementById('sales-credit-only'); if (c) c.checked = false;
  const so = document.getElementById('sales-sort'); if (so) so.value = 'date'; loadSalesHistory(); }
function setSalesSort(v) { salesFilter.sort = v; loadSalesHistory(); }
function setSalesCreditOnly(v) { salesFilter.creditOnly = !!v; loadSalesHistory(); }
async function loadSalesSummary() {
  const box = document.getElementById('sales-summary');
  if (!box) return;
  const res = await fetch('/api/sales-history/summary?location_id=' + encodeURIComponent(activeLocationId));
  if (!res.ok) { box.innerHTML = ''; return; }
  const d = (await res.json()).periods;
  box.innerHTML = Object.keys(SALES_PERIOD_LABELS).map(k => {
    const p = d[k], on = salesFilter.period === k;
    return `<button type="button" aria-pressed="${on}" onclick="setSalesPeriod('${k}')" style="text-align:left;cursor:pointer;min-height:44px;border:${on ? '2px solid #1b7f4f' : '1px solid #dee2e6'};background:${on ? '#e8f5ee' : '#fff'};color:inherit;border-radius:8px;padding:10px">
      <div style="font-size:.8rem;color:#6c757d">${SALES_PERIOD_LABELS[k]}</div>
      <div style="font-size:1.25rem;font-weight:700">KSh ${Number(p.net_sales).toLocaleString(undefined,{maximumFractionDigits:0})}</div>
      <small>${p.orders} sales${p.refunds ? ' · refunds ' + Number(p.refunds).toFixed(0) : ''}${p.credit_given ? ' · credit ' + Number(p.credit_given).toFixed(0) : ''}</small></button>`;
  }).join('');
}
async function loadTopProducts() {
  const box = document.getElementById('top-products');
  if (!box) return;
  const period = (document.getElementById('top-period') || {}).value || 'this_month';
  try {
    const res = await fetch('/api/sales-history/top-products?limit=10&period=' + encodeURIComponent(period) + '&location_id=' + encodeURIComponent(activeLocationId), { credentials: 'same-origin' });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) { box.textContent = d.message || 'Could not load top products.'; return; }
    if (!d.products.length) { box.textContent = 'No sales in this period.'; return; }
    const money = v => Number(v).toLocaleString(undefined, { maximumFractionDigits: 0 });
    box.innerHTML = `<table class="data-table"><thead><tr><th>#</th><th>Product</th><th>Sold</th><th>Sales (KSh)</th><th>% of sales</th>${d.show_profit ? '<th>Profit (KSh)</th>' : ''}</tr></thead><tbody>` +
      d.products.map((p, i) => `<tr><td>${i + 1}</td><td>${escHtml(p.name)}</td><td>${Number(p.kg_sold).toLocaleString(undefined, { maximumFractionDigits: 1 })} kg${p.bags_sold ? ' (' + Number(p.bags_sold).toLocaleString(undefined, { maximumFractionDigits: 1 }) + ' bags)' : ''}</td><td>${money(p.sales)}</td><td>${p.share_pct}%</td>${d.show_profit ? `<td>${money(p.profit)}</td>` : ''}</tr>`).join('') +
      `</tbody></table><small style="color:#666">Total sales in period: KSh ${money(d.total_sales)} (refunds taken off)</small>`;
  } catch (e) { box.textContent = 'Could not load top products.'; }
}
async function loadSalesHistory(append) {
  append = append === true;
  if (!append) { salesOffset = 0; loadSalesSummary(); loadTopProducts(); }
  const q = new URLSearchParams({location_id: activeLocationId, limit: '200'});
  if (salesOffset > 0) q.set('offset', salesOffset);
  const ticket = append ? salesLoadTicket : ++salesLoadTicket;   // a newer filter click cancels an older, slower load
  if (salesFilter.period) q.set('period', salesFilter.period);
  if (salesFilter.creditOnly) q.set('credit_only', '1');
  if (salesFilter.sort === 'credit') q.set('sort', 'credit');
  const label = document.getElementById('sales-filter-label');
  if (label) label.textContent = 'Showing: ' + (salesFilter.period ? SALES_PERIOD_LABELS[salesFilter.period] : 'all sales, newest first') + (salesFilter.creditOnly ? ' · credit only' : '');
  const res = await fetch('/api/sales-history?' + q.toString());
  const history = await res.json();
  if (ticket !== salesLoadTicket) return;
  const tbody = document.getElementById('sales-table-body');
  if(!tbody) return;
  if (!res.ok || !Array.isArray(history)) { tbody.innerHTML = '<tr><td colspan="9" style="color:#b00">Could not load sales.</td></tr>'; return; }
  const more = document.getElementById('sales-more');
  if (more) more.style.display = res.headers.get('X-Has-More') === '1' ? 'block' : 'none';
  if (!append) { tbody.innerHTML = ''; salesHistoryByOrder = {}; }
  Object.assign(salesHistoryByOrder, Object.fromEntries(history.map(s => [s.order_id, s])));
  salesOffset += history.length;
  if (history.length === 0 && !append) { tbody.innerHTML = '<tr><td colspan="9">No sales match this filter.</td></tr>'; return; }
  const rowsHtml = history.map(s => {
    const refundDetails = (s.refunds || []).map(r => `<small>${escHtml(r.reference)} · ${escHtml(r.payment_method)} KSh ${Number(r.amount).toFixed(2)} · ${escHtml(r.reason)} · ${escHtml(r.created_by)}</small>`).join('<br>');
    return `<tr><td><small>${escHtml(s.created_at)}</small></td><td><b>${escHtml(s.sale_id)}</b></td><td>${escHtml(s.customer_name)}</td><td><b>KSh ${Number(s.total_amount).toFixed(2)}</b></td><td>KSh ${Number(s.paid_amount).toFixed(2)}</td><td>${s.credit_amount > 0 ? '<span style="color:#dc3545; font-weight:bold;">KSh '+Number(s.credit_amount).toFixed(2)+'</span>' : 'KSh 0.00'}</td><td><small>${(s.payments || []).map(p => `${escHtml(p.method)}: KSh${Number(p.amount).toFixed(2)}`).join(', ') || 'CASH'}</small></td><td>KSh ${Number(s.refunded_amount || 0).toFixed(2)}${refundDetails ? '<br>' + refundDetails : ''}</td><td style="white-space:nowrap"><button class="btn-sm btn-info" onclick="showSaleReceipt(${s.order_id})">Receipt</button> <button class="btn-sm btn-warning" onclick="refundSale(${s.order_id}, ${Number(s.refundable_amount || 0)})" ${Number(s.refundable_amount || 0) <= 0 ? 'disabled' : ''}>Refund</button></td></tr>`;
  });
  tbody.insertAdjacentHTML('beforeend', rowsHtml.join(''));
  if (res.headers.get('X-Has-More') === '1' && salesOffset < 20000) return loadSalesHistory(true);
}
function saleReceiptHtml(s) {
  const lines = (s.items || []).map(i => `<div class="r-row"><span>${escHtml(i.qty_entered)} ${escHtml(i.unit || '')} x ${escHtml(i.name)}</span><span>KSh ${Number(i.subtotal || 0).toFixed(2)}</span></div>`).join('');
  const pays = (s.payments || []).map(p => `<div class="r-row"><span>${escHtml(p.method)}</span><span>KSh ${Number(p.amount).toFixed(2)}</span></div>`).join('');
  return `<h3>EMINING FEEDS</h3><p>Quality Feeds For You</p><hr>
    <div>Date: ${escHtml(s.created_at)}</div><div>Receipt: ${escHtml(s.sale_id)}</div><div>Customer: ${escHtml(s.customer_name)}</div><hr>
    ${lines}<hr>
    <div class="r-row"><b>TOTAL</b><b>KSh ${Number(s.total_amount).toFixed(2)}</b></div>
    <div class="r-row"><span>Paid</span><span>KSh ${Number(s.paid_amount).toFixed(2)}</span></div>${pays}
    ${s.credit_amount > 0 ? `<div class="r-row"><b>On credit</b><b>KSh ${Number(s.credit_amount).toFixed(2)}</b></div>` : ''}
    ${s.refunded_amount > 0 ? `<div class="r-row"><span>Refunded</span><span>KSh ${Number(s.refunded_amount).toFixed(2)}</span></div>` : ''}
    <hr><p>Thank you!</p>`;
}
function showSaleReceipt(orderId) {
  const s = salesHistoryByOrder[orderId];
  if (!s) return alert('Receipt not available. Refresh the list and try again.');
  let ov = document.getElementById('sale-receipt-overlay');
  if (ov) ov.remove();
  ov = document.createElement('div');
  ov.id = 'sale-receipt-overlay';
  ov.setAttribute('role', 'dialog'); ov.setAttribute('aria-label', 'Sale receipt');
  ov.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:100;display:flex;align-items:center;justify-content:center;padding:12px';
  ov.innerHTML = `<div style="background:#fff;border-radius:10px;max-width:360px;width:100%;max-height:90vh;overflow:auto;padding:16px;font-family:monospace">
    <style>#sale-receipt-overlay h3,#sale-receipt-overlay p{text-align:center;margin:4px 0}#sale-receipt-overlay hr{border:0;border-top:1px dashed #000;margin:8px 0}#sale-receipt-overlay .r-row{display:flex;justify-content:space-between;gap:8px;margin-bottom:4px}</style>
    <div id="sale-receipt-body">${saleReceiptHtml(s)}</div>
    <div style="display:flex;gap:8px;margin-top:12px;font-family:system-ui"><button class="btn-primary" style="flex:1;min-height:44px" id="sale-receipt-print">Print</button><button class="btn-success" style="flex:1;min-height:44px" id="sale-receipt-share">Share image</button><button class="btn-info" style="flex:1;min-height:44px" id="sale-receipt-close">Close</button></div></div>`;
  document.body.appendChild(ov);
  document.getElementById('sale-receipt-close').onclick = () => ov.remove();
  const shareBtn = document.getElementById('sale-receipt-share'); if (shareBtn) shareBtn.onclick = () => shareReceiptImage(orderId);
  ov.addEventListener('click', e => { if (e.target === ov) ov.remove(); });
  document.getElementById('sale-receipt-print').onclick = () => {
    const w = window.open('', '', 'width=400,height=600');
    if (!w) return alert('Allow pop-ups to print, or use the browser menu to print this page.');
    w.document.write(`<html><head><style>body{font-family:monospace;width:300px;margin:0 auto;padding:20px}h3,p{text-align:center;margin:5px 0}hr{border-top:1px dashed #000;margin:10px 0}.r-row{display:flex;justify-content:space-between;margin-bottom:4px}</style></head><body>${saleReceiptHtml(s)}</body></html>`);
    w.document.close(); w.focus(); w.print(); w.close();
  };
}
async function refundSale(orderId, refundable) {
  if (refundable <= 0) return alert('No paid amount remains available to refund.');
  const amount = prompt(`Maximum refundable: KSh ${Number(refundable).toFixed(2)}. Enter refund amount:`);
  if (amount === null) return;
  const method = (prompt('Refund through CASH, MPESA, or BANK?') || '').trim().toUpperCase();
  const reason = (prompt('Refund reason:') || '').trim();
  if (!['CASH','MPESA','BANK'].includes(method)) return alert('Use CASH, MPESA, or BANK.');
  const sale = salesHistoryByOrder[orderId];
  const returned_items = [];
  for (const item of (sale?.items || [])) {
    const remaining = Math.max(0, Number(item.quantity_kg || 0) - Number(item.returned_kg || 0));
    if (!remaining || !confirm(`Was any ${item.name} physically returned in saleable condition? Up to ${remaining.toFixed(2)} kg remains eligible.`)) continue;
    const rawQty = prompt(`How many kg of ${item.name} should return to this outlet's stock?`, remaining.toFixed(2));
    if (rawQty === null) return;
    const quantity = Number(rawQty);
    if (!Number.isFinite(quantity) || quantity <= 0 || quantity > remaining + 0.000001) return alert('Enter a positive returned quantity no greater than the amount remaining on this sale line.');
    returned_items.push({order_line_id:item.order_line_id, quantity_kg:quantity});
  }
  const r = await fetch(`/api/pos/orders/${orderId}/refund`, {method:'POST', headers:{'Content-Type':'application/json'},
    body:JSON.stringify({amount, payment_method:method, reason, returned_items})});
  const d = await r.json().catch(() => ({}));
  if (!r.ok) return alert(d.message || 'Could not record refund.');
  alert(`Refund ${d.reference} recorded. ${Number(d.restocked_kg || 0).toFixed(2)} kg returned to outlet stock.`);
  await loadSalesHistory();
}
resetPaymentLines();
window.addEventListener('load', initOutletTill);


// ---- receipt as an image: attach it from the share sheet (pick the customer's chat there)
async function shareReceiptImage(orderId) {
  try {
    const res = await fetch('/api/receipts/' + orderId + '.png', {credentials: 'same-origin'});
    if (!res.ok) throw new Error('Could not build the receipt image.');
    const blob = await res.blob();
    const file = new File([blob], 'receipt-' + orderId + '.png', {type: 'image/png'});
    if (navigator.canShare && navigator.canShare({files: [file]})) {
      await navigator.share({files: [file], title: 'Emining Feeds receipt'});
    } else {
      const a = document.createElement('a');            // no share sheet (desktop): save the picture instead
      a.href = URL.createObjectURL(blob); a.download = file.name; document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 4000);
    }
  } catch (e) {
    if (e && e.name === 'AbortError') return;            // the person closed the share sheet
    alert(e.message || 'Could not share the receipt.');
  }
}

// ---- show the reason/approver boxes only while a discount is typed
document.addEventListener('input', e => {
  if (e.target && e.target.id === 'discount-val') {
    document.getElementById('discount-extra').style.display = (parseFloat(e.target.value) || 0) > 0 ? 'block' : 'none';
  }
});
document.addEventListener('change', e => {
  if (e.target && (e.target.id === 'discount-val' || e.target.id === 'discount-type')) {
    document.getElementById('discount-extra').style.display = (parseFloat(document.getElementById('discount-val').value) || 0) > 0 ? 'block' : 'none';
  }
  if (e.target && e.target.id === 'customer-select') {
    document.getElementById('walkin-capture').style.display = e.target.value ? 'none' : '';
  }
});

async function verifyReceipt() {
  const ref = document.getElementById('verify-ref').value.trim(), code = document.getElementById('verify-code').value.trim();
  const out = document.getElementById('verify-out');
  if (!ref || !code) { out.innerHTML = '<span style="color:#b00">Enter the receipt number and the check code.</span>'; return; }
  try {
    const r = await fetch('/api/receipts/verify?ref=' + encodeURIComponent(ref) + '&code=' + encodeURIComponent(code), {credentials: 'same-origin'});
    const d = await r.json();
    out.innerHTML = d.valid
      ? `<div style="color:#146c2e;font-weight:700">${escHtml(d.message)}</div><div>${escHtml(d.ref)} &middot; ${escHtml(d.when)} &middot; ${escHtml(d.customer)} &middot; served by ${escHtml(d.cashier)}</div><div><b>KSh ${Number(d.total).toFixed(2)}</b></div><ul>${d.items.map(i => '<li>' + escHtml(i) + '</li>').join('')}</ul>`
      : `<div style="color:#b00;font-weight:700">${escHtml(d.message || 'Not valid.')}</div>`;
  } catch (e) { out.innerHTML = '<span style="color:#b00">No connection. Try again.</span>'; }
}
