let poCart = [];
let activePOId = null;
let currentGRPOLines = [];

async function loadPODropdown() {
  const items = await (await fetch('/api/inventory')).json();
  const sel = document.getElementById('po-ingredient-select');
  if(!sel) return;
  sel.innerHTML = '';
  items.forEach(i => { sel.innerHTML += `<option value="${i.id}" data-cost="${i.cost_per_kg}">${i.name}</option>`; });

  const suppliers = await (await fetch('/api/suppliers')).json();
  const supSel = document.getElementById('po-supplier-select');
  supSel.innerHTML = '';
  suppliers.forEach(s => supSel.innerHTML += `<option value="${s.id}">${s.name}</option>`);

  const locations = await (await fetch('/api/locations')).json();
  const locSel = document.getElementById('po-location-select');
  locSel.innerHTML = '';
  locations.forEach(l => locSel.innerHTML += `<option value="${l.id}">${l.name}</option>`);

  sel.addEventListener('change', (e) => { document.getElementById('po-cost').value = e.target.options[e.target.selectedIndex].getAttribute('data-cost'); });
  if(sel.options.length > 0) document.getElementById('po-cost').value = sel.options[0].getAttribute('data-cost');
}

function addToPOCart() {
  const sel = document.getElementById('po-ingredient-select');
  if (sel.selectedIndex === -1) return alert("Select an item.");
  const qty = parseFloat(document.getElementById('po-qty').value);
  const cost = parseFloat(document.getElementById('po-cost').value);
  if (!qty || qty <= 0 || cost < 0 || isNaN(cost)) return alert("Valid quantity and cost required.");
  poCart.push({ ingredient_id: parseInt(sel.value), name: sel.options[sel.selectedIndex].text.split(' (')[0], qty_kg: qty, unit_cost: cost });
  document.getElementById('po-qty').value = ''; renderPOCart();
}

function renderPOCart() {
  const tbody = document.getElementById('po-cart-body');
  const totalEl = document.getElementById('po-total-val');
  tbody.innerHTML = '';
  let total = 0;
  if (poCart.length === 0) { tbody.innerHTML = '<tr><td colspan="5" style="text-align: center; color: #888;">Cart is empty.</td></tr>'; if(totalEl) totalEl.textContent='0.00'; return; }
  poCart.forEach((item, idx) => {
    const subtotal = item.qty_kg * item.unit_cost; total += subtotal;
    tbody.innerHTML += `<tr><td><b>${item.name}</b></td><td>${item.qty_kg}</td><td>${item.unit_cost.toFixed(2)}</td><td><b>${subtotal.toFixed(2)}</b></td><td><button class="btn-sm btn-danger" onclick="poCart.splice(${idx},1); renderPOCart()">x</button></td></tr>`;
  });
  if(totalEl) totalEl.textContent = total.toFixed(2);
}

async function submitPO() {
  if (poCart.length === 0) return alert("Cart is empty.");
  try {
    const res = await fetch('/api/po/create', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ supplier_id: parseInt(document.getElementById('po-supplier-select').value), location_id: parseInt(document.getElementById('po-location-select').value), items: poCart }) });
    const data = await res.json();
    if (data.status === 'success') { document.getElementById('po-response').innerHTML = `<span style="color:green;">✓ PO ${data.po_no} Issued!</span>`; poCart = []; renderPOCart(); } 
    else { document.getElementById('po-response').innerHTML = `<span style="color:red;">Error: ${data.message}</span>`; }
  } catch (err) { document.getElementById('po-response').innerHTML = `<span style="color:red;">System Error: ${err.message}</span>`; }
}

async function fetchPOLinesForGRPO() {
  const search = document.getElementById('grpo-po-search').value.trim();
  if (!search) return alert("Enter PO Number.");
  try {
    const res = await fetch(`/api/po/details?po_no=${search}`);
    const data = await res.json();
    if (data.status === 'error') return alert(data.message);
    activePOId = data.po_id; currentGRPOLines = data.lines;
    const tbody = document.getElementById('grpo-lines-body');
    tbody.innerHTML = '';
    currentGRPOLines.forEach((line, idx) => {
      tbody.innerHTML += `<tr><td>${line.ingredient_name}</td><td>${line.ordered_qty}</td><td>${line.received_qty}</td><td><input type="number" id="grpo-qty-${idx}" max="${line.ordered_qty - line.received_qty}" min="0" value="${Math.max(0, line.ordered_qty - line.received_qty)}" style="width: 80px; padding: 4px;" /></td></tr>`;
    });
  } catch (err) { alert("System Error."); }
}

async function submitGRPO() {
  if (!activePOId || currentGRPOLines.length === 0) return alert("No active PO.");
  const received = currentGRPOLines.map((line, idx) => ({ line_id: line.line_id, qty_kg: parseFloat(document.getElementById(`grpo-qty-${idx}`).value) || 0 })).filter(item => item.qty_kg > 0);
  if (received.length === 0) return alert("Enter quantities > 0.");
  try {
    const res = await fetch(`/api/po/${activePOId}/grpo`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ received_items: received }) });
    const data = await res.json();
    if (data.status === 'success') { document.getElementById('grpo-response').innerHTML = `<span style="color:#198754;">✓ GRPO Received!</span>`; document.getElementById('grpo-lines-body').innerHTML = '<tr><td colspan="4" style="text-align: center;">Search PO.</td></tr>'; activePOId = null; currentGRPOLines = []; } 
    else { document.getElementById('grpo-response').innerHTML = `<span style="color:red;">Error: ${data.message}</span>`; }
  } catch (err) { alert("System Error."); }
}