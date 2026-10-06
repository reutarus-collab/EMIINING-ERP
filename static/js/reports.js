const reportEsc = value => String(value == null ? '' : value).replace(/[&<>"']/g,
  char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const reportMoney = value => value == null ? 'Not tracked' : `KSh ${Number(value).toLocaleString('en-KE', {minimumFractionDigits:2, maximumFractionDigits:2})}`;
let lastCashWalk = null;

function dateInputValue(date) {
  return `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`;
}

function reportDates() {
  return {start:document.getElementById('money-start').value, end:document.getElementById('money-end').value};
}

async function initMoneyReports() {
  const now = new Date();
  const end = document.getElementById('money-end');
  const start = document.getElementById('money-start');
  if (!end.value) end.value = dateInputValue(now);
  if (!start.value) {
    const weekStart = new Date(now); weekStart.setDate(now.getDate()-6);
    start.value = dateInputValue(weekStart);
  }
  await loadMoneyReports();
}

async function loadMoneyReports() {
  const {start, end} = reportDates();
  if (!start || !end) return;
  document.getElementById('opening-date-label').textContent = start;
  const opening = await fetch(`/api/reports/opening-balances?as_of_date=${encodeURIComponent(start)}`, {credentials:'same-origin'});
  const openingData = await opening.json();
  const names = {cash:'cash', inventory:'inventory', debtors:'debtors', creditors:'creditors', equipment:'equipment'};
  if (openingData.balances) {
    for (const [field, key] of Object.entries(names)) document.getElementById(`opening-${field}`).value = openingData.balances[key];
  } else {
    for (const field of Object.keys(names)) document.getElementById(`opening-${field}`).value = '';
  }
  const query = `start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`;
  const [cashResponse, leaksResponse] = await Promise.all([
    fetch(`/api/reports/cash-walk?${query}`, {credentials:'same-origin'}),
    fetch(`/api/reports/leaks?${query}`, {credentials:'same-origin'})
  ]);
  const cash = await cashResponse.json();
  const leaks = await leaksResponse.json();
  const message = document.getElementById('cash-walk-message');
  const body = document.getElementById('cash-walk-body');
  document.getElementById('roll-opening-btn').style.display = cashResponse.ok ? 'inline-block' : 'none';
  lastCashWalk = cashResponse.ok ? cash : null;
  if (!cashResponse.ok) {
    message.textContent = cash.message || 'Could not load cash walk.';
    body.innerHTML = '';
  } else {
    message.textContent = `Cash walk for ${cash.start} through ${cash.end}. Reconciliation difference: ${reportMoney(cash.reconciliation_difference)}.`;
    const lines = [
      ['Opening cash', cash.opening.cash, '+'], ['Profit', cash.lines.profit, '+'],
      ['Stock increase', cash.lines.stock_increase, '−'], ['Debtors increase', cash.lines.debtors_increase, '−'],
      ['Creditors increase', cash.lines.creditors_increase, '+'], ['Owner withdrawals', cash.lines.owner_withdrawals, '−'],
      ['Equipment bought', cash.lines.equipment_bought, '−']
    ];
    body.innerHTML = `<div class="money-table-wrap"><table class="data-table"><thead><tr><th>Cash walk line</th><th>KSh</th></tr></thead><tbody>${lines.map(row=>`<tr><td>${row[0]}</td><td>${row[2]} ${reportMoney(row[1])}</td></tr>`).join('')}<tr><th>Calculated closing cash</th><th>${reportMoney(cash.calculated_closing_cash)}</th></tr><tr><td>Ledger closing cash</td><td>${reportMoney(cash.ledger_closing_cash)}</td></tr><tr><td>Closing balances for next week</td><td>Stock ${reportMoney(cash.closing_balances.inventory)} · Debtors ${reportMoney(cash.closing_balances.debtors)} · Creditors ${reportMoney(cash.closing_balances.creditors)}</td></tr></tbody></table></div>`;
  }
  document.getElementById('leak-report-body').innerHTML = leaksResponse.ok ? renderLeakReport(leaks) : reportEsc(leaks.message || 'Could not load leak report.');
}

function renderLeakReport(data) {
  const rows = data.leaks.map(row => {
    const trend = row.trend_delta == null ? '—' : `${row.trend_delta > 0 ? '↑' : row.trend_delta < 0 ? '↓' : '→'} ${reportMoney(Math.abs(row.trend_delta))}`;
    const extra = [row.definition, row.note, row.trend_note].filter(Boolean).join(' ');
    return `<tr><td data-label="Leak">${reportEsc(row.label)}</td><td data-label="This period">${reportMoney(row.amount)}</td><td data-label="Previous period">${row.previous_amount == null ? '—' : reportMoney(row.previous_amount)}</td><td data-label="Trend">${trend}</td><td data-label="Definition / coverage">${reportEsc(extra)}</td></tr>`;
  }).join('');
  const detailRows = data.leaks.map(row => {
    const detail = row.items || row.products || row.by_cashier || row.by_product || row.tills || row.benchmarks;
    if (!detail || !detail.length) return '';
    if (row.key === 'price') {
      const cells = detail.slice(0, 10).map(item => `<tr><td>${reportEsc(item.item)}</td><td>${reportMoney(item.price)}</td><td>${reportMoney(item.cost)}</td><td>${item.margin_pct == null ? 'No price' : `${reportEsc(item.margin_pct)}%`}</td><td>${reportMoney(item.below_cost_exposure)}</td></tr>`).join('');
      return `<details><summary>Price leakage detail</summary><div class="money-table-wrap"><table class="data-table"><thead><tr><th>Product</th><th>Price / kg</th><th>Highest outlet cost / kg</th><th>Margin</th><th>Estimated below-cost sales</th></tr></thead><tbody>${cells}</tbody></table></div></details>`;
    }
    if (row.key === 'discounts') {
      const cells = detail.slice(0, 10).map(item => `<tr><td>${reportEsc(item.name)}</td><td>${reportMoney(item.discount)}</td><td>${reportMoney(item.sales)}</td><td>${reportEsc(item.rate_pct)}%</td></tr>`).join('');
      return `<details><summary>Discounts by cashier / product</summary><div class="money-table-wrap"><table class="data-table"><thead><tr><th>Cashier or product</th><th>Discount</th><th>Sales before discount</th><th>Discount rate</th></tr></thead><tbody>${cells}</tbody></table></div></details>`;
    }
    if (row.key === 'purchases' && row.benchmarks && row.benchmarks.length) {
      const cells = row.benchmarks.slice(0, 10).map(item => `<tr><td>${reportEsc(item.item)}</td><td>${reportMoney(item.unit_cost)}</td><td>${reportMoney(item['90d_average'])}</td><td>${reportMoney(item.premium)}</td></tr>`).join('');
      return `<details><summary>Purchase price benchmarks</summary><div class="money-table-wrap"><table class="data-table"><thead><tr><th>Item</th><th>Received cost / kg</th><th>Prior 90-day average</th><th>Premium</th></tr></thead><tbody>${cells}</tbody></table></div></details>`;
    }
    const cells = detail.slice(0, 10).map(item => `<tr><td>${reportEsc(item.item || item.name || item.cashier)}</td><td>${reportMoney(item.value ?? item.stock_value ?? item.discount ?? item.variance ?? item.below_cost_exposure)}</td><td>${item.days_on_hand == null ? '' : `${reportEsc(item.days_on_hand)} days`}</td></tr>`).join('');
    return `<details><summary>${reportEsc(row.label)} detail</summary><div class="money-table-wrap"><table class="data-table"><thead><tr><th>Item / cashier</th><th>KSh / value</th><th>Days on hand</th></tr></thead><tbody>${cells}</tbody></table></div></details>`;
  }).join('');
  return `<p>Ranked from highest estimated KSh impact to lowest. Period: ${reportEsc(data.start)} through ${reportEsc(data.end)}.</p><div class="money-table-wrap leak-table-wrap"><table class="data-table leak-table"><thead><tr><th>Leak</th><th>This period</th><th>Previous period</th><th>Trend</th><th>Definition / coverage</th></tr></thead><tbody>${rows}</tbody></table></div>${detailRows}`;
}

async function saveOpeningBalances() {
  const start = document.getElementById('money-start').value;
  if (!start) return alert('Choose the opening date first.');
  const payload = {as_of_date:start};
  for (const key of ['cash','inventory','debtors','creditors','equipment']) payload[key] = Number(document.getElementById(`opening-${key}`).value || 0);
  const response = await fetch('/api/reports/opening-balances', {method:'POST', credentials:'same-origin', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
  const data = await response.json();
  document.getElementById('cash-walk-message').textContent = data.status === 'success' ? `Opening balances saved for ${data.as_of_date}.` : (data.message || 'Could not save balances.');
  if (data.status === 'success') await loadMoneyReports();
}

async function rollOpeningBalances() {
  if (!lastCashWalk) return;
  const next = new Date(`${lastCashWalk.end}T00:00:00`); next.setDate(next.getDate()+1);
  const nextEnd = new Date(next); nextEnd.setDate(nextEnd.getDate()+6);
  const payload = {as_of_date:dateInputValue(next), ...lastCashWalk.closing_balances};
  const response = await fetch('/api/reports/opening-balances', {method:'POST', credentials:'same-origin', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
  const data = await response.json();
  if (data.status !== 'success') return alert(data.message || 'Could not carry balances forward.');
  document.getElementById('money-start').value = dateInputValue(next);
  document.getElementById('money-end').value = dateInputValue(nextEnd);
  await loadMoneyReports();
}

async function recordOwnerWithdrawal() {
  const message = document.getElementById('owner-withdrawal-message');
  const payload = {amount:Number(document.getElementById('owner-withdrawal-amount').value),
    payment_method:document.getElementById('owner-withdrawal-method').value,
    reason:document.getElementById('owner-withdrawal-reason').value,
    location_id:activeLocationId};
  const response = await fetch('/api/reports/owner-withdrawals', {method:'POST', credentials:'same-origin', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
  const data = await response.json();
  message.textContent = data.status === 'success' ? `Withdrawal recorded: ${data.reference}.` : (data.message || 'Could not record withdrawal.');
  if (data.status === 'success') {
    document.getElementById('owner-withdrawal-amount').value = '';
    document.getElementById('owner-withdrawal-reason').value = '';
    await loadMoneyReports();
  }
}

async function recordEquipmentPurchase() {
  const message = document.getElementById('equipment-message');
  const payload = {amount:Number(document.getElementById('equipment-amount').value),
    payment_method:document.getElementById('equipment-method').value,
    description:document.getElementById('equipment-description').value,
    location_id:activeLocationId};
  const response = await fetch('/api/reports/equipment-purchases', {method:'POST', credentials:'same-origin', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
  const data = await response.json();
  message.textContent = data.status === 'success' ? `Equipment purchase recorded: ${data.reference}.` : (data.message || 'Could not record purchase.');
  if (data.status === 'success') {
    document.getElementById('equipment-amount').value = '';
    document.getElementById('equipment-description').value = '';
    await loadMoneyReports();
  }
}
