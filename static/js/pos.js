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
    div.innerHTML += `<div class="product-row">
      <div><b>${i.name}</b> <span style="font-size:0.75rem; background:#e9ecef; padding:1px 4px; border-radius:3px;">${i.category}</span><br/><small>Stock: ${i.available_stock_kg} kg | KSh ${i.retail_price_kg}/kg | Bag: ${i.bag_size_kg}kg</small></div>
      <button class="btn-sm btn-primary" onclick="addToCart(${i.id})">Add</button></div>`;
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
      <td><b>${c.name}</b></td>
      <td><input type="number" value="${c.qty}" step="any" min="0.1" onchange="cart[${idx}].qty=Math.max(0.1, parseFloat(this.value)); renderCart()" style="width: 80px; text-align: center; padding: 10px; font-size: 16px; height: 50px;" /></td>
      <td>
        <!-- MASSIVE CART DROPDOWN -->
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

function renderReceipt(data) {
  document.getElementById('r-date').textContent = new Date().toLocaleString(); 
  document.getElementById('r-id').textContent = data.sale_id; 
  
  const cSel = document.getElementById('customer-select');
  const custId = cSel.value;
  let customerDisplayText = "Walk-In Cash Customer";
  let debtText = "";

  // Check the customer cache for the updated debt
  if (custId) {
      const cust = customersCache.find(c => c.id == custId);
      if (cust) {
          customerDisplayText = cust.name;
          debtText = `<br/>Remaining Debt: KSh ${cust.balance.toFixed(2)}`;
      }
  }

  // Inject the name and the debt onto the receipt
  document.getElementById('r-cust').innerHTML = `<strong>${customerDisplayText}</strong>${debtText}`; 
  document.getElementById('r-total').textContent = data.total_amount.toFixed(2); 
  
  const linesDiv = document.getElementById('r-lines');
  linesDiv.innerHTML = ''; 
  data.items.forEach(i => { 
      linesDiv.innerHTML += `<div style="display:flex; justify-content:space-between; margin-bottom: 4px;"><span>${i.qty_entered} ${i.unit} x ${i.name}</span><span>KSh ${i.subtotal.toFixed(2)}</span></div>`; 
  });

  // Add the Amount Paid and Change Due to the bottom of the receipt
  linesDiv.innerHTML += `<hr style="border-top: 1px dashed #333; margin: 5px 0;"/>`;
  linesDiv.innerHTML += `<div style="display:flex; justify-content:space-between;"><span>Amount Paid:</span><span>KSh ${data.paid_amount.toFixed(2)}</span></div>`;
  linesDiv.innerHTML += `<div style="display:flex; justify-content:space-between;"><span>Change Due:</span><span>KSh ${data.change_due.toFixed(2)}</span></div>`;

  document.getElementById('btn-print').disabled = false;
  document.getElementById('btn-wa').disabled = false;
}function clearCart() { 
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
    document.getElementById('payment-lines').innerHTML += `<div class="split-row"><select class="p-method" style="flex: 1;"><option value="CASH">Cash</option><option value="MPESA">M-Pesa</option><option value="BANK">Bank</option><option value="CREDIT">Credit</option></select><input type="number" min="0" class="p-amount" placeholder="Enter amount paid..." oninput="calculateChange()" style="flex: 2;" /><input type="text" class="p-ref" placeholder="Ref (Optional)" style="flex: 1;" /></div>`; 
}

async function completeCheckout