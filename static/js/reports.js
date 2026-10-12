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
    if (openingData.balances) {
      const difference = Number(openingData.cash_difference || 0);
      message.textContent += ` Opening cash is prefilled${openingData.defaulted_from_previous ? ' from the previous close' : ''}. Ledger cash at this date: ${reportMoney(openingData.ledger_cash)}; count the till and adjust the cash field if needed (difference KSh ${difference.toFixed(2)}). Save opening balances to confirm.`;
      message.style.color = Math.abs(difference) > 0.01 ? '#b42318' : '';
    }
    body.innerHTML = '';
  } else {
    const openingDifference = Number(cash.opening_cash_difference || 0);
    message.style.color = Math.abs(openingDifference) > 0.01 ? '#b42318' : '';
    const openingFlag = Math.abs(openingDifference) > 0.01 ? ' ⚠ OPENING CASH DOES NOT MATCH LEDGER.' : '';
    message.textContent = `Cash walk for ${cash.start} through ${cash.end}. Opening counted cash: ${reportMoney(cash.opening.cash)}; ledger expected ${reportMoney(cash.ledger_opening_cash)} (difference ${reportMoney(openingDifference)}).${openingFlag} Closing reconciliation difference: ${reportMoney(cash.reconciliation_difference)}.`;
    if (!openingData.exists && openingData.defaulted_from_previous) message.textContent += ' Opening balances came from the previous close. Count cash and save the opening balances to confirm this period.';
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
  const renderRows = rows => rows.map(row => {
    const trend = row.trend_delta == null ? '—' : `${row.trend_delta > 0 ? '↑' : row.trend_delta < 0 ? '↓' : '→'} ${reportMoney(Math.abs(row.trend_delta))}`;
    const extra = [row.definition, row.note, row.trend_note].filter(Boolean).join(' ');
    return `<tr><td data-label="Leak">${reportEsc(row.label)}</td><td data-label="This period">${reportMoney(row.amount)}</td><td data-label="Previous period">${row.previous_amount == null ? '—' : reportMoney(row.previous_amount)}</td><td data-label="Trend">${trend}</td><td data-label="Definition / coverage">${reportEsc(extra)}</td></tr>`;
  }).join('');
  const detailRows = [...(data.money_lost || data.leaks || []), ...(data.cash_tied_up || [])].map(row => {
    const detail = row.items || row.products || row.by_cashier || row.by_product || row.tills || row.benchmarks;
    if (!detail || !detail.length) return '';
    if (row.key === 'price') {
      const cells = detail.slice(0, 10).map(item => `<tr><td>${reportEsc(item.item)}</td><td>${reportMoney(item.price)}</td><td>${reportMoney(item.cost)}</td><td>${item.margin_pct == null ? 'No price' : `${reportEsc(item.margin_pct)}%`}</td><td>${reportMoney(item.target_price)}</td><td>${reportMoney(item.below_target_exposure)}</td></tr>`).join('');
      return `<details><summary>Price leakage detail</summary><div class="money-table-wrap"><table class="data-table"><thead><tr><th>Product</th><th>Price / kg</th><th>Outlet cost / kg</th><th>Margin</th><th>Target price / kg</th><th>Estimated target gap</th></tr></thead><tbody>${cells}</tbody></table></div></details>`;
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
  const table = rows => `<div class="money-table-wrap leak-table-wrap"><table class="data-table leak-table"><thead><tr><th>Leak</th><th>This period</th><th>Previous period</th><th>Trend</th><th>Definition / coverage</th></tr></thead><tbody>${renderRows(rows)}</tbody></table></div>`;
  const losses = data.money_lost || data.leaks || [];
  const tied = data.cash_tied_up || [];
  return `<p>Losses and cash tied up are separated so stock value is not mistaken for money already lost. Period: ${reportEsc(data.start)} through ${reportEsc(data.end)}.</p><h4>Money lost · biggest first</h4>${table(losses)}<h4>Cash tied up · biggest first</h4>${tied.length ? table(tied) : '<p>No cash-tied-up exposures tracked for this period.</p>'}<h4>Leak details</h4>${detailRows}`;
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

// ---- Financial statements (ledger) ----
const stmtMoney = v => Number(v || 0).toLocaleString('en-KE', {minimumFractionDigits: 2, maximumFractionDigits: 2});
const stmtRow = (label, amount, bold) => `<tr><${bold ? 'th' : 'td'}>${reportEsc(label)}</${bold ? 'th' : 'td'}><${bold ? 'th' : 'td'} style="text-align:right">${stmtMoney(amount)}</${bold ? 'th' : 'td'}></tr>`;
const stmtTable = (head, rows) => `<table class="data-table"><thead><tr><th>${head}</th><th style="text-align:right">KSh</th></tr></thead><tbody>${rows}</tbody></table>`;

function renderProfitLoss(d) {
  const list = items => items.map(r => stmtRow(`${r.code} ${r.name}`, r.amount)).join('');
  return stmtTable('Profit & Loss ' + d.start + ' to ' + d.end,
    list(d.revenue) + (d.returns_total ? list(d.returns).replace(/<td style="text-align:right">/g, '<td style="text-align:right">-') : '') +
    stmtRow('Net sales', d.net_sales, true) + stmtRow('Cost of goods sold', -d.cogs) +
    stmtRow('Gross profit' + (d.gross_margin_pct == null ? '' : ` (${d.gross_margin_pct}% margin)`), d.gross_profit, true) +
    (d.other_income.length ? list(d.other_income) : '') + 
    list(d.expenses.map(r => ({...r, amount: -r.amount}))) +
    stmtRow('Net profit', d.net_profit, true));
}

function renderCashFlow(d) {
  let rows = stmtRow('Opening cash (cash, M-Pesa, bank, safe)', d.opening_cash, true);
  d.groups.forEach(g => {
    rows += `<tr><td colspan="2"><b>${reportEsc(g.title)}</b></td></tr>`;
    rows += g.lines.length ? g.lines.map(l => stmtRow(l.label, l.amount)).join('') : '<tr><td colspan="2" style="color:#888">No movements</td></tr>';
    rows += stmtRow('Net ' + g.title.toLowerCase(), g.net, true);
  });
  rows += stmtRow('Net change in cash', d.net_change, true) + stmtRow('Closing cash', d.closing_cash, true);
  return stmtTable('Cash Flow ' + d.start + ' to ' + d.end, rows);
}

function renderTrialBalance(d) {
  const body = d.rows.map(r => `<tr><td>${reportEsc(r.code)} ${reportEsc(r.name)}</td><td style="text-align:right">${r.debit ? stmtMoney(r.debit) : ''}</td><td style="text-align:right">${r.credit ? stmtMoney(r.credit) : ''}</td></tr>`).join('');
  const warn = d.balanced ? '' : `<p style="color:#b42318"><b>OUT OF BALANCE by KSh ${stmtMoney(Math.abs(d.difference))}.</b> Some ledger postings are one-sided; find them before trusting the profit figures.</p>`;
  return warn + `<table class="data-table"><thead><tr><th>Account as of ${reportEsc(d.as_of)}</th><th style="text-align:right">Debit</th><th style="text-align:right">Credit</th></tr></thead><tbody>${body}<tr><th>Total</th><th style="text-align:right">${stmtMoney(d.total_debit)}</th><th style="text-align:right">${stmtMoney(d.total_credit)}</th></tr></tbody></table><p style="color:#666">${reportEsc(d.note)}</p>`;
}

async function loadStatements() {
  const startEl = document.getElementById('stmt-start'), endEl = document.getElementById('stmt-end');
  const now = new Date();
  if (!endEl.value) endEl.value = dateInputValue(now);
  if (!startEl.value) startEl.value = dateInputValue(new Date(now.getFullYear(), now.getMonth(), 1));
  const q = `start=${encodeURIComponent(startEl.value)}&end=${encodeURIComponent(endEl.value)}`;
  const msg = document.getElementById('stmt-message');
  msg.textContent = 'Loading...';
  const targets = [['profit-loss', 'stmt-pl', renderProfitLoss], ['cash-flow', 'stmt-cf', renderCashFlow], ['trial-balance', 'stmt-tb', renderTrialBalance]];
  try {
    const results = await Promise.all(targets.map(t => fetch(`/api/reports/${t[0]}?${q}`, {credentials: 'same-origin'}).then(async r => ({ok: r.ok, data: await r.json().catch(() => ({}))}))));
    msg.textContent = '';
    results.forEach((res, i) => {
      document.getElementById(targets[i][1]).innerHTML = res.ok ? targets[i][2](res.data) : reportEsc(res.data.message || 'Could not load.');
    });
  } catch (e) { msg.textContent = 'No connection. Try again.'; }
}

