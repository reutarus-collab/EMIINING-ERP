let cart = [];
let heldCart = [];
let customersCache = [];
let searchResultsCache = [];

async function searchProducts() {
  const q = document.getElementById('search-input').value;
  const cat = document.getElementById('category-filter').value;
  const res = await fetch(`/api/products/search?q=${q}&category=${cat}`);
  searchResultsCache = await res.json();
  const div = document.getElementById('search-results');
  div.innerHTML = '';
  if (searchResultsCache.length === 0) { div.innerHTML = '<div style="padding:10px;">No items match query.</div>'; return; }
  searchResultsCache.forEach(i => {
    const priceText = i.priced ? `KSh ${i.retail_price_kg}/kg` : '<b style="color:#b00">NO PRICE</b>';
    const btn = i.priced
      ? `<button class="btn-sm btn-primary" onclick="addToCart(${i.id})">Add</button>`
      : `<button class="btn-sm" disabled>No price</button>`;
    div.innerHTML += `<div class="product-row">
      <div><b>${escHtml(i.name)}</b> <span style="font-size:0.75rem; background:#e9ecef; padding:1px 4px; border-radius:3px;">${escHtml(i.category)}</span><br/><small>Stock: ${i.available_stock_kg} kg | ${priceText} | Bag: ${i.bag_size_kg}kg</small></div>
      ${btn}</div>`;
  });
}

function addToCart(itemId) {
  const item = searchResultsCache.find(i => i.id === itemId);
  if(!item) return;
  const existing = cart.find(c => c.id === item.id);
  if (existing) { 
      existing.qty += 1; 
  } else { 
      cart.push({ id: item.id, name: item.name, base_bag_size: item.bag_size_kg, unit_type: 'KG', bag_size_kg: 1.0, qty: 1, retail_price_kg: item.retail_price_kg }); 
  }
  renderCart();
}

function updateCartUnit(idx, newUnit) {
    if(newUnit === 'BAG') {
        cart[idx].unit_type = 'BAG';
        cart[idx].bag_size_kg = cart[idx].base_bag_size;
    } else {
        cart[idx].unit_type = 'KG';
        cart[idx].bag_size_kg = 1.0; 
    }
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
    const linePricePerUnit = c.retail_price_kg * c.bag_size_kg;
    const lineTotal = c.qty * linePricePerUnit;
    subtotal += lineTotal;
    
    tbody.innerHTML += `<tr>
      <td><b>${escHtml(c.name)}</b></td>
      <td><input type="number" value="${c.qty}" step="any" min="0.1" onchange="cart[${idx}].qty=Math.max(0.1, parseFloat(this.value)); renderCart()" style="width: 80px; text-align: center; padding: 10px; font-size: 16px; height: 50px;" /></td>
      <td>
        <select onchange="updateCartUnit(${idx}, this.value)" style="height: 50px; font-size: 16px; width: 100%; min-width: 120px;">
            <option value="KG" ${c.unit_type === 'KG' ? 'selected' : ''}>KG</option>
            <option value="BAG" ${c.unit_type === 'BAG' ? 'selected' : ''}>Bag (${c.base_bag_size}kg)</option>
        </select>
      </td>
      <td style="font-size: 16px;">${linePricePerUnit.toFixed(2)}</td>
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

async function completeCheckout() {
  if (cart.length === 0) return alert('Cart is empty!');
  const custId = document.getElementById('customer-select').value;
  
  let rawSubtotal = cart.reduce((sum, c) => sum + (c.qty * c.retail_price_kg * c.bag_size_kg), 0);
  let discVal = parseFloat(document.getElementById('discount-val').value) || 0;
  let discType = document.getElementById('discount-type').value;
  let absoluteDisc = discType === 'PCT' ? (rawSubtotal * (discVal / 100)) : discVal;
  
  if (absoluteDisc < 0) return alert("Negative discounts are strictly prohibited.");
  
  let totalDue = Math.max(0, rawSubtotal - absoluteDisc);

  // --- NEW STRICT BLOCKER ---
  if (totalDue <= 0) {
      return alert("Cannot complete sale: Total amount due is KSh 0.00. Please check if all items have prices set.");
  }
  // --- NEW: AUTO-CREDIT CALCULATOR ---
  let explicitTotal = 0;
  let emptyCreditRow = null;
  
  document.querySelectorAll('.split-row').forEach(row => {
      const method = row.querySelector('.p-method').value;
      const amt = parseFloat(row.querySelector('.p-amount').value) || 0;
      explicitTotal += amt;
      
      // Detect if they chose CREDIT but left amount blank
      if (method === 'CREDIT' && amt === 0) {
          emptyCreditRow = row;
      }
  });

  // Auto-fill the missing balance
  if (emptyCreditRow && explicitTotal < totalDue) {
      const short = parseFloat((totalDue - explicitTotal).toFixed(2));
      emptyCreditRow.querySelector('.p-amount').value = short;
      calculateChange(); 
  }
  // -----------------------------------

  let payments = []; 
  let isValid = true;
  let totalEntered = 0;
  
  document.querySelectorAll('.split-row').forEach(row => { 
      const amtStr = row.querySelector('.p-amount').value; 
      if(amtStr && parseFloat(amtStr) > 0) {
          const amt = parseFloat(amtStr);
          if (amt < 0) isValid = false;
          payments.push({ payment_method: row.querySelector('.p-method').value, amount: amt, reference: row.querySelector('.p-ref').value }); 
          totalEntered += amt;
      }
  });

  if (!isValid) return alert("Negative payments are strictly prohibited.");
  
  // STRICT PAYMENT CHECK
  if (totalEntered < (totalDue - 0.01)) { 
      const short = (totalDue - totalEntered).toFixed(2);
      return alert(`Payment incomplete! You are short by KSh ${short}.`);
  }

    if (checkoutInFlight) return;
  checkoutInFlight = true;
  try {
      const res = await fetch('/api/pos/checkout', {
          method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
              customer_id: custId ? parseInt(custId) : null,
              discount_amount: absoluteDisc,
              cart: cart.map(c => ({ ingredient_id: c.id, unit_type: c.unit_type, qty: c.qty, bag_size_kg: c.bag_size_kg })),
              payments: payments
          })
      });
      if (res.status === 401) {
          return alert('Session expired. Sale NOT recorded. Cart is kept. Log in again in a new tab, then press Complete again.');
      }
      if (res.status === 403) return alert('Your role is not allowed to do this.');
      const data = await res.json();
      if (data.status === 'success') {
          await loadCustomers();
          renderReceipt(data.data);
          clearCart();
          searchProducts();
      } else { alert('Sale Failed: ' + data.message); }
  } catch(err) {
      alert('No connection or system error. Sale NOT recorded. Cart is kept. (' + err.message + ')');
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
            body: JSON.stringify({ amount: amt })
        });
        const data = await res.json();
        
        if(data.status === 'success') {
            alert(`Repayment successful! New balance: KSh ${data.new_balance.toFixed(2)}`);
            document.getElementById('repay-amount').value = '';
            await loadCustomers(); 
        } else {
            alert('Repayment Failed: ' + data.message);
        }
    } catch(e) {
        alert('System Error during repayment.');
    }
}

async function loadSalesHistory() {
  const res = await fetch('/api/sales-history'); 
  const history = await res.json();
  const tbody = document.getElementById('sales-table-body'); 
  if(!tbody) return;
  tbody.innerHTML = '';
  if (history.length === 0) { tbody.innerHTML = '<tr><td colspan="7">No sales logged.</td></tr>'; return; }
  history.forEach(s => {
    tbody.innerHTML += `<tr><td><small>${s.created_at}</small></td><td><b>${escHtml(s.sale_id)}</b></td><td>${escHtml(s.customer_name)}</td><td><b>KSh ${s.total_amount.toFixed(2)}</b></td><td>KSh ${s.paid_amount.toFixed(2)}</td><td>${s.credit_amount > 0 ? '<span style="color:#dc3545; font-weight:bold;">KSh '+s.credit_amount.toFixed(2)+'</span>' : 'KSh 0.00'}</td><td><small>${s.payments.map(p => `${p.method}: KSh${p.amount.toFixed(2)}`).join(', ') || 'CASH'}</small></td></tr>`;
  });
}