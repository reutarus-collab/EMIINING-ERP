let cart = [];
let heldCart = [];
let customersCache = [];
let searchResultsCache = [];
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
    await refreshTill();
    if (typeof loadFactoryDropdowns === 'function') loadFactoryDropdowns();
    if (typeof loadProductionRuns === 'function') loadProductionRuns();
    if (typeof loadStock === 'function') loadStock();
    if (typeof loadDailyReport === 'function') loadDailyReport();
  } catch (e) {
    const state = document.getElementById('till-state');
    if (state) state.textContent = 'Outlet setup required: assign this user to a valid location.';
  }
}
async function changePosLocation() {
  activeLocationId = document.getElementById('pos-location').value;
  window.activeLocationId = activeLocationId;
  await refreshTill();
  searchProducts();
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
async function loadTillMovementHistory() {
  const el = document.getElementById('till-movement-history');
  if (!el) return;
  const r = await fetch('/api/till/cash-movements?location_id=' + encodeURIComponent(activeLocationId));
  const d = await r.json().catch(() => ({}));
  if (!r.ok) { el.textContent = d.message || 'Could not load till movements.'; return; }
  const rows = d.movements || [];
  el.innerHTML = rows.length ? rows.map(m => `${escHtml(m.created_at)} · ${escHtml(m.type)} KSh ${Number(m.amount).toFixed(2)} · ${escHtml(m.reason)} · ${escHtml(m.created_by)}`).join('<br>') : 'No paid-in / paid-out movements recorded.';
}
async function recordTillMovement() {
  const msg = document.getElementById('till-movement-message');
  const r = await fetch('/api/till/cash-movements', {method:'POST', headers:{'Content-Type':'application/json'},
    body:JSON.stringify({location_id:activeLocationId, movement_type:document.getElementById('till-movement-type').value,
      amount:document.getElementById('till-movement-amount').value, reason:document.getElementById('till-movement-reason').value})});
  const d = await r.json().catch(() => ({}));
  if (!r.ok) { msg.textContent = d.message || 'Could not save cash movement.'; return; }
  msg.textContent = `Saved ${d.reference}. Expected till cash KSh ${Number(d.expected_cash).toFixed(2)}.`;
  document.getElementById('till-movement-amount').value = ''; document.getElementById('till-movement-reason').value = '';
  await refreshTill();
}

async function searchProducts() {
  const q = document.getElementById('search-input').value;
  const cat = document.getElementById('category-filter').value;
  const res = await fetch(`/api/products/search?q=${encodeURIComponent(q)}&category=${encodeURIComponent(cat)}&location_id=${encodeURIComponent(activeLocationId)}`);
  searchResultsCache = await res.json();
  const div = document.getElementById('search-results');
  div.innerHTML = '';
  if (searchResultsCache.length === 0) { div.innerHTML = '<div style="padding:10px;">No items match query.</div>'; return; }
  searchResultsCache.forEach(i => {
    const tiers = [];
    if ((i.retail_price_kg || 0) > 0) tiers.push('KSh ' + i.retail_price_kg + '/kg');
    (i.packs || []).forEach(p => tiers.push(p.pack_kg + 'kg bag KSh ' + p.price));
    const priceText = tiers.length ? tiers.join(' | ') : '<b style="color:#b00">NO PRICE</b>';
    const btn = i.priced
      ? `<button class="btn-sm btn-primary" onclick="addToCart(${i.id})">Add</button>`
      : `<button class="btn-sm" disabled>No price</button>`;
    div.innerHTML += `<div class="product-row">
      <div><b>${escHtml(i.name)}</b> <span style="font-size:0.75rem; background:#e9ecef; padding:1px 4px; border-radius:3px;">${escHtml(i.category)}</span><br/><small>Stock: ${i.available_stock_kg} kg | ${priceText}</small></div>
      ${btn}</div>`;
  });
}

function buildPackOptions(item) {
  const out = [];
  if ((item.retail_price_kg || 0) > 0) out.push({ pack_kg: 1, price: item.retail_price_kg, label: 'KG' });
  (item.packs || []).forEach(p => out.push({ pack_kg: p.pack_kg, price: p.price, label: p.pack_kg + ' kg bag' }));
  return out;
}
function addToCart(itemId) {
  const item = searchResultsCache.find(i => i.id === itemId);
  if (!item) return;
  const options = buildPackOptions(item);
  if (options.length === 0) return alert('This item has no price set.');
  const first = options[0];
  const existing = cart.find(c => c.id === item.id && c.bag_size_kg === first.pack_kg);
  if (existing) {
      existing.qty += 1;
  } else {
      cart.push({ id: item.id, name: item.name, options: options, bag_size_kg: first.pack_kg,
                  unit_type: first.pack_kg === 1 ? 'KG' : 'BAG', unit_price: first.price, qty: 1 });
  }
  renderCart();
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
  const tbody = document.getElementById('cart-body');
  tbody.innerHTML = '';
  let subtotal = 0;
  if (cart.length === 0) {
    tbody.innerHTML = '<tr><td colspan="6" style="text-align: center; color: #888; padding: 20px;">Cart is empty.</td></tr>';
    document.getElementById('cart-total').textContent = '0.00';
    calculateChange();
    return;
  }
  cart.forEach((c, idx) => {
    const lineTotal = c.qty * c.unit_price;
    subtotal += lineTotal;
    const opts = c.options.map(o =>
      `<option value="${o.pack_kg}" ${o.pack_kg === c.bag_size_kg ? 'selected' : ''}>${escHtml(o.label)}</option>`).join('');
    tbody.innerHTML += `<tr>
      <td><b>${escHtml(c.name)}</b></td>
      <td><input type="number" value="${c.qty}" step="any" min="0.1" onchange="cart[${idx}].qty=Math.max(0.1, parseFloat(this.value)); renderCart()" style="width: 80px; text-align: center; padding: 10px; font-size: 16px; height: 50px;" /></td>
      <td>
        <select onchange="updateCartUnit(${idx}, this.value)" style="height: 50px; font-size: 16px; width: 100%; min-width: 120px;">${opts}</select>
      </td>
      <td style="font-size: 16px;">${c.unit_price.toFixed(2)}</td>
      <td style="font-size: 16px;"><b>${lineTotal.toFixed(2)}</b></td>
      <td><button class="btn-sm btn-danger" style="height: 50px;" onclick="cart.splice(${idx}, 1); renderCart()">x</button></td>
    </tr>`;
  });
  let discVal = parseFloat(document.getElementById('discount-val').value) || 0;
  let discType = document.getElementById('discount-type').value;
  let absoluteDisc = discType === 'PCT' ? (subtotal * (discVal / 100)) : discVal;
  document.getElementById('cart-total').textContent = Math.max(0, subtotal - absoluteDisc).toFixed(2);
  calculateChange();
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

function addPaymentLine() { 
    document.getElementById('payment-lines').innerHTML += `<div class="split-row" style="margin-bottom: 10px;"><select class="p-method" style="flex: 1; height: 50px; font-size: 16px; padding: 10px;"><option value="CASH">Cash</option><option value="MPESA">M-Pesa</option><option value="BANK">Bank</option><option value="CREDIT">Credit</option></select><input type="number" min="0" class="p-amount" placeholder="Enter amount paid..." oninput="calculateChange()" style="flex: 2; height: 50px; font-size: 18px; padding: 10px;" /><input type="text" class="p-ref" placeholder="Ref (Optional)" style="flex: 1; height: 50px; font-size: 16px; padding: 10px;" /></div>`; 
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
    let isValid = true, totalEntered = 0;
    document.querySelectorAll('.split-row').forEach(row => {
      const amtStr = row.querySelector('.p-amount').value;
      if (amtStr && parseFloat(amtStr) > 0) {
        const amt = parseFloat(amtStr);
        if (amt < 0) isValid = false;
        payments.push({payment_method:row.querySelector('.p-method').value, amount:amt,
          reference:row.querySelector('.p-ref').value});
        totalEntered += amt;
      }
    });
    if (!isValid) return alert('Negative payments are strictly prohibited.');
    if (totalEntered < totalDue - 0.01) return alert(`Payment incomplete! You are short by KSh ${(totalDue - totalEntered).toFixed(2)}.`);
    const cartPayload = cart.map(c => ({ingredient_id:c.id, unit_type:c.unit_type, qty:c.qty, bag_size_kg:c.bag_size_kg}));
    attempt = {key:newCheckoutUuid(), owner:window.currentUsername,
      cartFingerprint:JSON.stringify(cartPayload), payload:{customer_id:custId ? parseInt(custId) : null,
        location_id:activeLocationId, discount_amount:absoluteDisc, cart:cartPayload, payments}};
    pendingCheckoutAttempt = attempt;
    try { sessionStorage.setItem('pendingCheckoutAttempt', JSON.stringify(attempt)); } catch (_) {}
  }
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
          renderReceipt(data.data);
          if (JSON.stringify(cart.map(c => ({ingredient_id:c.id, unit_type:c.unit_type, qty:c.qty, bag_size_kg:c.bag_size_kg}))) === attempt.cartFingerprint) clearCart();
          else alert('The earlier checkout was confirmed. Your current cart was kept separate.');
          searchProducts();
      } else {
          if (res.status === 400) {
              pendingCheckoutAttempt = null;
              try { sessionStorage.removeItem('pendingCheckoutAttempt'); } catch (_) {}
          }
          alert('Sale failed: ' + data.message + (res.status === 409 ? ' Keep this tab open and contact an administrator before retrying.' : ''));
      }
  } catch(err) {
      alert('Online only: no connection or response. The sale may have reached the server. Keep this tab open and retry before re-entering the sale; the same checkout key will prevent duplicates.');
  } finally { checkoutInFlight = false; }
}
function renderReceipt(data) {
  document.getElementById('r-date').textContent = new Date().toLocaleString(); 
  document.getElementById('r-id').textContent = data.sale_id; 
  
  const cSel = document.getElementById('customer-select');
  const custId = cSel.value;
  let customerDisplayText = "Walk-In Cash Customer";
  let debtText = "";

  if (custId) {
      const cust = customersCache.find(c => c.id == custId);
      if (cust) {
          customerDisplayText = cust.name;
          debtText = `<br/>Remaining Debt: KSh ${cust.balance.toFixed(2)}`;
      }
  }

  document.getElementById('r-cust').innerHTML = `<strong>${escHtml(customerDisplayText)}</strong>${debtText}`; 
  document.getElementById('r-total').textContent = data.total_amount.toFixed(2); 
  
  const linesDiv = document.getElementById('r-lines');
  linesDiv.innerHTML = ''; 
  data.items.forEach(i => { 
      linesDiv.innerHTML += `<div style="display:flex; justify-content:space-between; margin-bottom: 4px;"><span>${i.qty_entered} ${i.unit} x ${escHtml(i.name)}</span><span>KSh ${i.subtotal.toFixed(2)}</span></div>`; 
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
      waBtn.onclick = shareWhatsApp; // Binds the new tab logic
  }
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
  const res = await fetch('/api/customers');
  customersCache = await res.json();
  const sel = document.getElementById('customer-select'); const currentSelVal = sel.value;
  sel.innerHTML = '<option value="">CASH CUSTOMER (Walk-In)</option>';
  customersCache.forEach(c => { 
      const locText = c.location && c.location !== 'Unknown' ? ` (${escHtml(c.location)})` : '';
      sel.innerHTML += `<option value="${c.id}">${escHtml(c.name)}${locText} - ${escHtml(c.phone)} - Debt: KSh ${c.balance}</option>`; 
  });
  sel.value = currentSelVal; updateCustomerDetails();
}

function updateCustomerDetails() {
  const selId = document.getElementById('customer-select').value;
  const card = document.getElementById('customer-credit-card');
  if (!selId) { card.style.display = 'none'; return; }
  const cust = customersCache.find(c => c.id == selId);
  if (cust) {
    document.getElementById('c-limit').textContent = cust.credit_limit.toFixed(2);
    document.getElementById('c-debt').textContent = cust.balance.toFixed(2);
    document.getElementById('c-avail').textContent = (cust.credit_limit - cust.balance).toFixed(2);
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
        body: JSON.stringify({name: name, phone: phone, location: location, credit_limit: limit})
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

let salesHistoryByOrder = {};
async function loadSalesHistory() {
  const res = await fetch('/api/sales-history?location_id=' + encodeURIComponent(activeLocationId));
  const history = await res.json();
  const tbody = document.getElementById('sales-table-body'); 
  if(!tbody) return;
  tbody.innerHTML = '';
  salesHistoryByOrder = Object.fromEntries(history.map(s => [s.order_id, s]));
  if (history.length === 0) { tbody.innerHTML = '<tr><td colspan="9">No sales logged.</td></tr>'; return; }
  history.forEach(s => {
    const refundDetails = (s.refunds || []).map(r => `<small>${escHtml(r.reference)} · ${escHtml(r.payment_method)} KSh ${Number(r.amount).toFixed(2)} · ${escHtml(r.reason)} · ${escHtml(r.created_by)}</small>`).join('<br>');
    tbody.innerHTML += `<tr><td><small>${escHtml(s.created_at)}</small></td><td><b>${escHtml(s.sale_id)}</b></td><td>${escHtml(s.customer_name)}</td><td><b>KSh ${Number(s.total_amount).toFixed(2)}</b></td><td>KSh ${Number(s.paid_amount).toFixed(2)}</td><td>${s.credit_amount > 0 ? '<span style="color:#dc3545; font-weight:bold;">KSh '+Number(s.credit_amount).toFixed(2)+'</span>' : 'KSh 0.00'}</td><td><small>${(s.payments || []).map(p => `${escHtml(p.method)}: KSh${Number(p.amount).toFixed(2)}`).join(', ') || 'CASH'}</small></td><td>KSh ${Number(s.refunded_amount || 0).toFixed(2)}${refundDetails ? '<br>' + refundDetails : ''}</td><td><button class="btn-sm btn-warning" onclick="refundSale(${s.order_id}, ${Number(s.refundable_amount || 0)})" ${Number(s.refundable_amount || 0) <= 0 ? 'disabled' : ''}>Refund</button></td></tr>`;
  });
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
window.addEventListener('load', initOutletTill);
