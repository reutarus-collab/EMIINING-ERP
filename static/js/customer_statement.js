/* Customer account window: statement (bank style), purchases, repay, share/download PDF.
 * Open from anywhere with openCustomerStatement(customerId[, 'statement'|'purchases'|'repay']).
 * Read-only except Repay, which uses the existing /api/customers/<id>/repay route (retry-safe via idem.js). */
(function () {
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const kes = v => Number(v || 0).toLocaleString('en-KE', {minimumFractionDigits: 2, maximumFractionDigits: 2});
  const qs = o => Object.keys(o).filter(k => o[k] !== '' && o[k] != null).map(k => k + '=' + encodeURIComponent(o[k])).join('&');
  let S = null;   // {id, tab, from, to, stmt, purchases, repay}

  function ymd(d) { const z = n => String(n).padStart(2, '0'); return d.getFullYear() + '-' + z(d.getMonth() + 1) + '-' + z(d.getDate()); }
  function period() { return qs({from: S.from, to: S.to}); }

  async function getJson(url) {
    const r = await fetch(url, {credentials: 'same-origin'});
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.message || ('HTTP ' + r.status));
    return d;
  }

  function root() {
    let ov = document.getElementById('cust-stmt-overlay');
    if (!ov) {
      ov = document.createElement('div');
      ov.id = 'cust-stmt-overlay';
      ov.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:2100;display:flex;align-items:center;justify-content:center;padding:8px';
      ov.addEventListener('click', e => { if (e.target === ov) closeStmt(); });
      document.body.appendChild(ov);
    }
    return ov;
  }
  function closeStmt() { const ov = document.getElementById('cust-stmt-overlay'); if (ov) ov.remove(); S = null; }

  function statementHtml(d) {
    const rows = d.entries.map(e => `<tr>
      <td><small>${esc(e.date.slice(0, 10))}</small></td>
      <td>${esc(e.description)}<br><small style="color:#777">${esc(e.ref)}${e.by ? ' &middot; ' + esc(e.by) : ''}</small></td>
      <td style="text-align:right;color:#b00">${e.debit ? kes(e.debit) : ''}</td>
      <td style="text-align:right;color:#1a7f37">${e.credit ? kes(e.credit) : ''}</td>
      <td style="text-align:right"><b>${kes(e.balance)}</b></td></tr>`).join('');
    const gap = Math.abs(d.unreconciled_gap) >= 0.01
      ? `<p style="color:#8a5a00;font-size:.85rem">KSh ${kes(d.unreconciled_gap)} of this balance has no itemised entry (recorded before statements existed).</p>` : '';
    return `<div style="display:flex;gap:14px;flex-wrap:wrap;margin:8px 0;font-size:.95rem">
        <span>Opening <b>${kes(d.opening_balance)}</b></span><span>Charges <b style="color:#b00">${kes(d.total_charges)}</b></span>
        <span>Paid <b style="color:#1a7f37">${kes(d.total_payments)}</b></span><span>Closing <b>${kes(d.closing_balance)}</b></span></div>${gap}
      <div style="overflow-x:auto"><table class="data-table"><thead><tr><th>Date</th><th>Details</th><th>Charge</th><th>Paid</th><th>Balance</th></tr></thead>
      <tbody><tr><td></td><td><i>Opening balance</i></td><td></td><td></td><td style="text-align:right"><b>${kes(d.opening_balance)}</b></td></tr>
      ${rows || '<tr><td colspan="5">No activity in this period.</td></tr>'}
      <tr><td></td><td><b>Closing balance</b></td><td></td><td></td><td style="text-align:right"><b>${kes(d.closing_balance)}</b></td></tr></tbody></table></div>`;
  }

  function purchasesHtml(p) {
    const top = p.top_items.map(t => `<span style="background:#eef3ef;border-radius:12px;padding:3px 10px;margin:2px;display:inline-block;font-size:.85rem">${esc(t.name)} &middot; ${t.kg.toLocaleString('en-KE')} kg</span>`).join('');
    const cards = p.sales.map(s => `<div style="border:1px solid #ddd;border-radius:8px;padding:8px 10px;margin:8px 0">
        <div style="display:flex;justify-content:space-between;gap:8px"><b>${esc(s.sale_id)}</b><small>${esc(s.date)}</small></div>
        ${s.items.map(i => `<div style="display:flex;justify-content:space-between;gap:8px;font-size:.92rem"><span>${esc(i.name)} &times; ${i.qty} <small>(${esc(i.unit.toLowerCase())}${/BAG$/.test(i.unit) ? ', ' + i.kg + ' kg' : ''})</small></span><span>${kes(i.amount)}</span></div>`).join('')}
        <div style="border-top:1px dashed #ccc;margin-top:4px;padding-top:4px;font-size:.9rem;display:flex;justify-content:space-between;flex-wrap:wrap;gap:6px">
          <span>Total <b>${kes(s.total)}</b>${s.discount ? ' (discount ' + kes(s.discount) + ')' : ''}</span>
          <span>Paid ${kes(s.paid)}</span>${s.credit ? `<span style="color:#b00">On credit <b>${kes(s.credit)}</b></span>` : ''}</div></div>`).join('');
    const more = p.sales.length < p.total_orders
      ? `<button class="btn-sm btn-info" style="min-height:42px;width:100%" onclick="csMorePurchases()">Load more (${p.total_orders - p.sales.length} left)</button>` : '';
    return `<p style="margin:8px 0">${p.total_orders} purchase${p.total_orders === 1 ? '' : 's'} &middot; spent <b>KSh ${kes(p.total_spent)}</b> &middot; of which on credit <b>KSh ${kes(p.total_on_credit)}</b></p>
      ${top ? '<div style="margin-bottom:6px"><small style="color:#666">Most bought:</small><br>' + top + '</div>' : ''}
      <div id="cs-sales">${cards || '<p>No purchases in this period.</p>'}</div><div id="cs-more">${more}</div>`;
  }

  function repayHtml(d) {
    return `<div id="cs-repay" style="background:#f4f9f5;border:1px solid #b7d9bf;border-radius:8px;padding:10px;margin:8px 0">
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
        <input id="cs-amt" type="number" inputmode="decimal" min="1" step="0.01" placeholder="Amount (KSh)" style="flex:2;min-width:130px;height:44px;font-size:16px;padding:8px">
        <select id="cs-method" style="flex:1;min-width:100px;height:44px"><option value="MPESA">M-Pesa</option><option value="CASH">Cash</option><option value="BANK">Bank</option></select>
        <button class="btn-sm btn-info" style="height:44px" onclick="document.getElementById('cs-amt').value='${d.balance.toFixed(2)}'">Full ${kes(d.balance)}</button>
        <button id="cs-pay" class="btn-sm btn-success" style="height:44px;flex:1" onclick="csRepay()">Record payment</button></div>
      <small style="color:#555">Cash goes into your open till. Open a till first if you take cash.</small><div id="cs-repay-msg" style="margin-top:6px"></div></div>`;
  }

  function paint() {
    const ov = root(), d = S.stmt;
    const tabBtn = (id, label) => `<button class="btn-sm ${S.tab === id ? 'btn-primary' : 'btn-info'}" style="min-height:40px;flex:1" onclick="csTab('${id}')">${label}</button>`;
    ov.innerHTML = `<div style="background:#fff;border-radius:8px;max-width:760px;width:100%;max-height:94vh;overflow:auto;padding:14px">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px"><div><h3 style="margin:0">${esc(d.name)}</h3><small>${esc(d.phone)}</small></div>
        <button style="min-height:40px" onclick="closeCustomerStatement()">Close</button></div>
      <p style="margin:8px 0">Owes <b style="color:${d.balance > 0 ? '#b00' : '#1a7f37'}">KSh ${kes(d.balance)}</b> &middot; limit ${kes(d.credit_limit)} &middot; available ${kes(d.available_credit)}</p>
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:6px">
        ${d.balance > 0 ? '<button class="btn-sm btn-success" style="min-height:42px;flex:1" onclick="csToggleRepay()">Repay debt</button>' : ''}
        <button class="btn-sm btn-primary" style="min-height:42px;flex:1" onclick="csShare()">Share PDF</button>
        <button class="btn-sm btn-info" style="min-height:42px;flex:1" onclick="csDownload()">Download PDF</button></div>
      <div id="cs-repay-slot">${S.repay ? repayHtml(d) : ''}</div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:8px 0">
        <label style="font-size:.85rem">From <input type="date" id="cs-from" value="${esc(S.from)}" style="height:38px"></label>
        <label style="font-size:.85rem">To <input type="date" id="cs-to" value="${esc(S.to)}" style="height:38px"></label>
        <button class="btn-sm btn-info" style="min-height:38px" onclick="csApply()">Apply</button>
        <button class="btn-sm btn-info" style="min-height:38px" onclick="csQuick(30)">30 days</button>
        <button class="btn-sm btn-info" style="min-height:38px" onclick="csQuick(0)">All</button></div>
      <div style="display:flex;gap:8px;margin-bottom:6px">${tabBtn('statement', 'Statement')}${tabBtn('purchases', 'Purchases')}</div>
      <div id="cs-body">${S.tab === 'statement' ? statementHtml(d) : purchasesHtml(S.purchases)}</div>
      <div id="cs-note" style="margin-top:6px;color:#b00"></div></div>`;
  }

  async function load() {
    S.stmt = await getJson(`/api/customers/${S.id}/statement?` + period());
    if (S.tab === 'purchases') { S.purchases = await getJson(`/api/customers/${S.id}/purchases?` + period()); }
    paint();
  }

  window.openCustomerStatement = async function (id, tab) {
    if (!id) return alert('Select a customer first.');
    S = {id: id, tab: tab === 'purchases' ? 'purchases' : 'statement', from: '', to: '', repay: tab === 'repay'};
    try { await load(); } catch (e) { closeStmt(); alert('Could not load customer account: ' + e.message); }
  };
  window.closeCustomerStatement = closeStmt;
  window.csTab = async function (t) {
    if (!S) return; S.tab = t;
    try { await load(); } catch (e) { alert(e.message); }
  };
  window.csApply = async function () {
    S.from = document.getElementById('cs-from').value; S.to = document.getElementById('cs-to').value;
    try { await load(); } catch (e) { document.getElementById('cs-note').textContent = e.message; }
  };
  window.csQuick = async function (days) {
    if (days) { const d = new Date(); d.setDate(d.getDate() - days); S.from = ymd(d); S.to = ymd(new Date()); } else { S.from = ''; S.to = ''; }
    try { await load(); } catch (e) { alert(e.message); }
  };
  window.csMorePurchases = async function () {
    try {
      const p = await getJson(`/api/customers/${S.id}/purchases?` + qs({from: S.from, to: S.to, offset: S.purchases.sales.length}));
      S.purchases = Object.assign({}, S.purchases, {sales: S.purchases.sales.concat(p.sales), total_orders: p.total_orders});
      paint();
    } catch (e) { document.getElementById('cs-note').textContent = e.message; }
  };
  window.csToggleRepay = function () { S.repay = !S.repay; document.getElementById('cs-repay-slot').innerHTML = S.repay ? repayHtml(S.stmt) : ''; };

  window.csRepay = async function () {
    const amt = parseFloat(document.getElementById('cs-amt').value), msg = document.getElementById('cs-repay-msg');
    const btn = document.getElementById('cs-pay');
    if (!amt || amt <= 0) { msg.innerHTML = '<span style="color:#b00">Enter an amount above zero.</span>'; return; }
    if (amt > S.stmt.balance + 0.01) { msg.innerHTML = '<span style="color:#b00">That is more than the KSh ' + kes(S.stmt.balance) + ' owed.</span>'; return; }
    const method = document.getElementById('cs-method').value;
    if (!confirm('Record KSh ' + kes(amt) + ' paid by ' + method + ' for ' + S.stmt.name + '?')) return;
    btn.disabled = true;
    try {
      const body = {amount: amt, payment_method: method};
      if (S.stmt.outlet_id != null) body.location_id = S.stmt.outlet_id;
      else if (typeof activeLocationId !== 'undefined') body.location_id = activeLocationId;
      const r = await fetch(`/api/customers/${S.id}/repay`, {method: 'POST', headers: {'Content-Type': 'application/json'},
        credentials: 'same-origin', body: JSON.stringify(body)});
      const d = await r.json().catch(() => ({}));
      if (!r.ok || d.status !== 'success') throw new Error(d.message || ('HTTP ' + r.status));
      S.repay = false;
      await load();
      document.getElementById('cs-note').style.color = '#1a7f37';
      document.getElementById('cs-note').textContent = 'Payment recorded (' + d.reference + '). New balance KSh ' + kes(d.new_balance) + '.';
      if (typeof loadCustAdmin === 'function' && document.getElementById('cust-body')) loadCustAdmin();
      if (typeof loadCustomers === 'function' && document.getElementById('customer-select')) loadCustomers();
      if (typeof refreshTill === 'function') refreshTill();
    } catch (e) { msg.innerHTML = '<span style="color:#b00">' + esc(e.message) + '</span>'; btn.disabled = false; }
  };

  function pdfUrl() { return `/api/customers/${S.id}/statement.pdf?` + period(); }
  function fileName() {
    return 'Emining_Statement_' + (S.stmt.name || 'customer').replace(/[^A-Za-z0-9]+/g, '_').replace(/^_|_$/g, '') + '_' + ymd(new Date()) + '.pdf';
  }
  async function pdfBlob() {
    const r = await fetch(pdfUrl(), {credentials: 'same-origin'});
    if (!r.ok) { const d = await r.json().catch(() => ({})); throw new Error(d.message || ('HTTP ' + r.status)); }
    return await r.blob();
  }
  function saveBlob(blob, name) {
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = name;
    document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 10000);
  }
  function waNumber(phone) {
    let n = String(phone || '').replace(/\D/g, '');
    if (n.startsWith('0') && n.length === 10) n = '254' + n.slice(1);
    else if (n.length === 9 && /^[17]/.test(n)) n = '254' + n;
    return n.length >= 11 ? n : '';
  }
  function shareText() {
    const d = S.stmt;
    return 'Hello ' + d.name + ', your Emining Feeds account balance is KSh ' + kes(d.balance) + ' as of ' + ymd(new Date()) + '. Statement attached.';
  }
  window.csDownload = async function () {
    try { saveBlob(await pdfBlob(), fileName()); } catch (e) { document.getElementById('cs-note').textContent = 'Could not make the PDF: ' + e.message; }
  };
  window.csShare = async function () {
    const note = document.getElementById('cs-note'); note.style.color = '#b00'; note.textContent = '';
    try {
      const blob = await pdfBlob(), name = fileName();
      const file = new File([blob], name, {type: 'application/pdf'});
      if (navigator.canShare && navigator.canShare({files: [file]})) {
        await navigator.share({files: [file], title: 'Account statement', text: shareText()});
        return;
      }
      // Desktop or older browser: save the PDF and open WhatsApp with the message ready.
      saveBlob(blob, name);
      const n = waNumber(S.stmt.phone);
      window.open('https://wa.me/' + n + '?text=' + encodeURIComponent(shareText()), '_blank');
      note.style.color = '#555'; note.textContent = 'PDF saved. Attach it in the WhatsApp chat that just opened.';
    } catch (e) {
      if (e && e.name === 'AbortError') return;   // person closed the share sheet
      note.textContent = 'Could not share: ' + e.message;
    }
  };
})();
