// Unique variables so they don't clash with pos.js
let po_suppliersList = [];
let po_inventoryList = [];
let po_currentCart = [];
let po_orderHistory = [];
let po_activeReceiving = null;

document.addEventListener('DOMContentLoaded', () => {
    // Failsafe initialization
    if(document.getElementById('po-tab')) {
        initPOModule();
    }
});

async function initPOModule() {
    await po_loadSuppliers();
    await po_loadInventory();
    await po_loadHistory();
}

async function po_loadSuppliers() {
    try {
        const res = await fetch('/api/suppliers');
        po_suppliersList = await res.json();
        const sel = document.getElementById('po-supplier');
        if(!sel) return;
        sel.innerHTML = '<option value="">-- Select Supplier --</option>';
        po_suppliersList.forEach(s => {
            sel.innerHTML += `<option value="${s.id}">${s.name}</option>`;
        });
    } catch (e) { console.error("Failed to load PO suppliers", e); }
}

async function po_loadInventory() {
    try {
        const res = await fetch('/api/po/inventory');
        po_inventoryList = await res.json();
        
        // 1. Populate the PO cart dropdown
        const sel = document.getElementById('po-item-select');
        if(sel) {
            sel.innerHTML = '<option value="">-- Select Item to Order --</option>';
            po_inventoryList.forEach(i => {
                sel.innerHTML += `<option value="${i.id}" data-cost="${i.cost_per_kg}">${i.name} (Cur. Cost: KSh ${i.cost_per_kg})</option>`;
            });
        }

        // 2. Populate the Item Master Table
        const tbody = document.getElementById('item-master-body');
        if(tbody) {
            tbody.innerHTML = '';
            po_inventoryList.forEach(i => {
                let stockColor = i.stock_quantity_kg > 0 ? 'green' : 'red';
                tbody.innerHTML += `
                    <tr>
                        <td><b>${i.name}</b></td>
                        <td>${i.category}</td>
                        <td>${i.bag_size_kg || '-'} kg</td>
                        <td style="font-weight:bold; color:${stockColor};">${i.stock_quantity_kg.toFixed(1)} kg</td>
                        <td>KSh ${i.cost_per_kg.toFixed(2)}</td>
                    </tr>
                `;
            });
        }
    } catch (e) { console.error("Failed to load PO inventory data", e); }
}

async function po_loadHistory() {
    try {
        const res = await fetch('/api/po');
        po_orderHistory = await res.json();
        const tbody = document.getElementById('po-list-body');
        if(!tbody) return;
        tbody.innerHTML = '';
        if(po_orderHistory.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;">No Purchase Orders found.</td></tr>';
            return;
        }
        po_orderHistory.forEach(po => {
            let actionBtn = '';
            if(po.status === 'APPROVED' || po.status === 'PARTIALLY_RECEIVED') {
                actionBtn = `<button class="btn-sm btn-success" onclick="openReceiveGRN(${po.id})">Receive GRN</button>`;
            }
            tbody.innerHTML += `
                <tr>
                    <td><b>${po.po_number}</b></td>
                    <td>${po.created_at}</td>
                    <td>${po.supplier_name}</td>
                    <td>KSh ${po.total_amount.toFixed(2)}</td>
                    <td><span style="font-weight:bold; color:${po.status === 'FULLY_RECEIVED' ? 'green' : 'orange'};">${po.status}</span></td>
                    <td>${actionBtn}</td>
                </tr>
            `;
        });
    } catch (e) { console.error("Failed to load PO history", e); }
}

function addPOLine() {
    const sel = document.getElementById('po-item-select');
    const qty = parseFloat(document.getElementById('po-qty').value);
    const cost = parseFloat(document.getElementById('po-cost').value);
    
    if(!sel.value || !qty || !cost) return alert("Select item, quantity, and unit cost.");
    
    const itemName = sel.options[sel.selectedIndex].text.split(' (')[0];
    
    po_currentCart.push({
        ingredient_id: parseInt(sel.value),
        name: itemName,
        qty: qty,
        unit_cost: cost
    });
    
    document.getElementById('po-qty').value = '';
    document.getElementById('po-cost').value = '';
    renderPOCart();
}

function renderPOCart() {
    const tbody = document.getElementById('po-cart-body');
    tbody.innerHTML = '';
    let total = 0;
    po_currentCart.forEach((item, idx) => {
        const sub = item.qty * item.unit_cost;
        total += sub;
        tbody.innerHTML += `
            <tr>
                <td>${item.name}</td>
                <td>${item.qty} kg</td>
                <td>KSh ${item.unit_cost.toFixed(2)}</td>
                <td><b>KSh ${sub.toFixed(2)}</b></td>
                <td><button class="btn-sm btn-danger" onclick="po_currentCart.splice(${idx}, 1); renderPOCart()">x</button></td>
            </tr>
        `;
    });
    document.getElementById('po-cart-total').innerText = total.toFixed(2);
}

async function submitPO() {
    const supplierId = document.getElementById('po-supplier').value;
    if(!supplierId) return alert("Select a supplier.");
    if(po_currentCart.length === 0) return alert("Add at least one item to the order.");

    const payload = {
        supplier_id: parseInt(supplierId),
        items: po_currentCart
    };

    try {
        const res = await fetch('/api/po', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if(data.status === 'success') {
            alert(`Purchase Order created successfully.`);
            po_currentCart = [];
            renderPOCart();
            document.getElementById('po-supplier').value = '';
            document.getElementById('create-po-modal').style.display = 'none';
            await po_loadHistory();
        } else {
            alert("Error: " + data.message);
        }
    } catch (err) {
        alert("System error creating PO.");
    }
}

// ================= GRN RECEIVING LOGIC =================
async function openReceiveGRN(poId) {
    const po = po_orderHistory.find(p => p.id === poId);
    if(!po) return;
    
    const res = await fetch(`/api/po/${poId}/lines`);
    const poData = await res.json();
    
    po_activeReceiving = poData;
    document.getElementById('grn-po-number').innerText = po.po_number;
    
    const tbody = document.getElementById('grn-lines-body');
    tbody.innerHTML = '';
    
    poData.lines.forEach((line, idx) => {
        const remaining = line.qty_ordered - line.qty_received_so_far;
        if (remaining <= 0) return;

        tbody.innerHTML += `
            <tr data-polineid="${line.id}" data-ordered="${remaining}">
                <td>${line.item_name}<br/><small style="color:#666;">Ordered: ${line.qty_ordered} | Pending: ${remaining}</small></td>
                <td><input type="number" class="grn-recv-qty" max="${remaining}" placeholder="Qty" style="width:90px;" oninput="calcAccepted(${idx})"></td>
                <td><input type="number" class="grn-rej-qty" value="0" min="0" style="width:90px;" oninput="calcAccepted(${idx})"></td>
                <td><span class="grn-acc-qty" style="font-weight:bold; color:green; font-size:16px;">0</span></td>
                <td><input type="text" class="grn-batch" placeholder="Lot/Batch" style="width:110px;"></td>
            </tr>
        `;
    });
    
    document.getElementById('receive-grn-modal').style.display = 'block';
}

function calcAccepted(idx) {
    const row = document.getElementById('grn-lines-body').children[idx];
    const recv = parseFloat(row.querySelector('.grn-recv-qty').value) || 0;
    const rej = parseFloat(row.querySelector('.grn-rej-qty').value) || 0;
    const acc = Math.max(0, recv - rej);
    row.querySelector('.grn-acc-qty').innerText = acc;
}

async function submitGRN() {
    const dNote = document.getElementById('grn-delivery-note').value;
    const vReg = document.getElementById('grn-vehicle-reg').value;
    const rows = document.getElementById('grn-lines-body').children;
    let receiptLines = [];
    
    for(let row of rows) {
        const poLineId = row.getAttribute('data-polineid');
        const recv = parseFloat(row.querySelector('.grn-recv-qty').value) || 0;
        const rej = parseFloat(row.querySelector('.grn-rej-qty').value) || 0;
        const batch = row.querySelector('.grn-batch').value;
        
        if (recv > 0) {
            receiptLines.push({
                po_line_id: parseInt(poLineId),
                qty_received: recv,
                qty_rejected: rej,
                batch_number: batch
            });
        }
    }
    
    if(receiptLines.length === 0) return alert("No quantities entered to receive.");
    
    try {
        const res = await fetch(`/api/po/${po_activeReceiving.id}/receive`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                delivery_note: dNote,
                vehicle_reg: vReg,
                receipt_lines: receiptLines
            })
        });
        const data = await res.json();
        
        if(data.status === 'success') {
            alert(`GRN ${data.grn_number} posted successfully. Inventory updated.`);
            document.getElementById('receive-grn-modal').style.display = 'none';
            await po_loadHistory();
            await po_loadInventory(); 
        } else {
            alert("Error posting GRN: " + data.message);
        }
    } catch(err) {
        alert("System error posting GRN.");
    }
}

// ================= NEW ITEM LOGIC =================
async function submitNewItem() {
    const name = document.getElementById('new-item-name').value;
    const cat = document.getElementById('new-item-cat').value;
    const bag = parseFloat(document.getElementById('new-item-bag').value) || 50;
    const cost = parseFloat(document.getElementById('new-item-cost').value) || 0;

    if(!name) return alert("Item name is required.");

    try {
        const res = await fetch('/api/po/inventory/add', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ 
                name: name, 
                category: cat, 
                bag_size_kg: bag, 
                cost_per_kg: cost 
            })
        });
        const data = await res.json();
        
        if(data.status === 'success') {
            alert("New item successfully added to the database!");
            document.getElementById('create-item-modal').style.display = 'none';
            document.getElementById('new-item-name').value = '';
            await po_loadInventory(); 
        } else {
            alert("Error: " + data.message);
        }
    } catch (err) {
        alert("System error adding item.");
    }
}