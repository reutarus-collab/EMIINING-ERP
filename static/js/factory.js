let factoryInventory = [];

async function runFormulation() {
    const speciesSelect = document.getElementById('species-select');
    if (!speciesSelect) return;
    const result = document.getElementById('formulation-result');
    try {
        const res = await fetch('/api/formulate', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                species: speciesSelect.value,
                target_batch_kg: document.getElementById('batch-kg').value,
                target_cp_pct: document.getElementById('target-cp').value,
                target_me_mcal: document.getElementById('target-me').value,
                location_id: window.activeLocationId || ''
            })
        });
        const data = await res.json();
        if (!res.ok || data.status !== 'optimal') throw new Error(data.message || 'Could not calculate this ration.');
        window.lastFormulation = data;
        result.innerHTML = `<b>${escHtml(data.species_stage)}</b> · ${Number(data.target_batch_kg).toFixed(2)} kg batch<br>
          Target: ${Number(data.target_crude_protein_pct).toFixed(2)}% crude protein · ${Number(data.target_metabolizable_energy_mcal).toFixed(2)} Mcal/kg<br>
          Estimated cost: KSh ${Number(data.total_batch_cost).toFixed(2)} (${Number(data.cost_per_kg).toFixed(2)} per kg)
          <div style="overflow-x:auto"><table class="data-table"><thead><tr><th>Ingredient</th><th>%</th><th>Recipe kg</th><th>At outlet kg</th><th>Cost/kg</th><th>Line cost</th></tr></thead><tbody>
          ${data.recipe.map(row => `<tr><td>${escHtml(row.ingredient_name)}</td><td>${Number(row.fraction).toFixed(2)}</td><td>${Number(row.kg_required).toFixed(2)}</td><td>${Number(row.available_kg).toFixed(2)}</td><td>${Number(row.cost_per_kg).toFixed(2)}</td><td>${Number(row.cost).toFixed(2)}</td></tr>`).join('')}
          </tbody></table></div>
          <button type="button" class="btn-success" style="margin-top:10px" onclick="useFormulaInProduction()">Use this formula in Production Batch</button>`;
    } catch (err) {
        result.textContent = 'Could not calculate a formulation. Check the connection and try again.';
    }
}

async function loadFactoryDropdowns() {
    const res = await fetch('/api/inventory?location_id=' + encodeURIComponent(window.activeLocationId || ''), { credentials: 'same-origin' });
    if (!res.ok) return;
    factoryInventory = await res.json();
    const prodOut = document.getElementById('prod-output-select');
    if (!prodOut) return;
    prodOut.innerHTML = '';
    factoryInventory.forEach(item => {
        const category = item.category || '';
        const option = `<option value="${item.id}">${escHtml(item.name)} (${Number(item.stock_quantity_kg || 0).toFixed(2)} kg available)</option>`;
        if (category === 'Finished Feed') prodOut.insertAdjacentHTML('beforeend', option);
    });
    const firstRaw = factoryInventory.find(item => (item.category || '').includes('Raw'));
    if (firstRaw && !document.querySelector('#prod-inputs tr[data-input-row]')) addProductionInput(firstRaw.id);
    loadProductionRuns();
    loadMillingRuns();
    loadSavedFormulas();
}

async function recordMillingRun() {
    const message = document.getElementById('mill-message');
    const payload = {
        location_id: window.activeLocationId || '',
        customer_name: document.getElementById('mill-customer-name').value,
        customer_phone: document.getElementById('mill-customer-phone').value,
        grain_description: document.getElementById('mill-grain-description').value,
        input_qty_kg: document.getElementById('mill-input-qty').value,
        output_qty_kg: document.getElementById('mill-output-qty').value,
        service_sale_reference: document.getElementById('mill-sale-reference').value,
        notes: document.getElementById('mill-notes').value
    };
    try {
        const res = await fetch('/api/factory/milling-runs', {method:'POST', credentials:'same-origin',
            headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
        const data = await res.json();
        if (!res.ok || data.status !== 'success') throw new Error(data.message || 'Could not record milling job.');
        message.style.color = 'green';
        message.textContent = `Milling job ${data.run_no} recorded. Weight difference: ${Number(data.loss_kg).toFixed(2)} kg. Customer-owned grain was not added to company stock.`;
        ['mill-customer-phone','mill-input-qty','mill-output-qty','mill-sale-reference','mill-notes'].forEach(id => document.getElementById(id).value = '');
        await loadMillingRuns();
    } catch (err) {
        message.style.color = 'red';
        message.textContent = err.message || 'Could not record milling job.';
    }
}

async function loadMillingRuns() {
    const body = document.getElementById('mill-history');
    if (!body) return;
    try {
        const res = await fetch('/api/factory/milling-runs?location_id=' + encodeURIComponent(window.activeLocationId || ''), {credentials:'same-origin'});
        const rows = await res.json();
        if (!res.ok) throw new Error('Could not load customer milling history.');
        body.innerHTML = rows.map(run => `<tr><td>${escHtml(run.run_no)}</td><td>${escHtml(run.customer_name)}<br>${escHtml(run.customer_phone)}</td>
          <td>${escHtml(run.grain_description)}</td><td>${Number(run.input_qty_kg).toFixed(2)}</td><td>${Number(run.output_qty_kg).toFixed(2)}</td>
          <td>${Number(run.loss_kg).toFixed(2)}</td><td>${escHtml(run.service_sale_reference)}</td><td>${escHtml(run.created_by)}</td><td>${escHtml(run.created_at)}</td></tr>`).join('') || '<tr><td colspan="9">No customer milling jobs recorded.</td></tr>';
    } catch (_) {
        body.innerHTML = '<tr><td colspan="9">Could not load customer milling history.</td></tr>';
    }
}

function addProductionInput(selectedId = null) {
    const body = document.getElementById('prod-inputs');
    if (!body) return;
    if (body.querySelector('td[colspan]')) body.innerHTML = '';
    const row = document.createElement('tr');
    row.dataset.inputRow = '1';
    const ingredientCell = document.createElement('td');
    const select = document.createElement('select');
    select.className = 'prod-ingredient';
    factoryInventory.filter(item => (item.category || '').includes('Raw')).forEach(item => {
        const option = document.createElement('option');
        option.value = item.id;
        option.textContent = `${item.name} (${Number(item.stock_quantity_kg || 0).toFixed(2)} kg)`;
        if (selectedId != null && item.id === Number(selectedId)) option.selected = true;
        select.appendChild(option);
    });
    select.addEventListener('change', calculateProductionLoss);
    ingredientCell.appendChild(select);
    row.appendChild(ingredientCell);
    row.appendChild(makeProductionQtyCell('prod-planned-input', 'Planned kg'));
    row.appendChild(makeProductionQtyCell('prod-actual-input', 'Actual kg'));
    const removeCell = document.createElement('td');
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'btn-sm btn-danger';
    remove.textContent = 'Remove';
    remove.addEventListener('click', () => { row.remove(); calculateProductionLoss(); });
    removeCell.appendChild(remove);
    row.appendChild(removeCell);
    body.appendChild(row);
}

function makeProductionQtyCell(className, placeholder) {
    const cell = document.createElement('td');
    const input = document.createElement('input');
    input.type = 'number';
    input.min = '0';
    input.step = '0.01';
    input.placeholder = placeholder;
    input.className = className;
    input.addEventListener('input', calculateProductionLoss);
    cell.appendChild(input);
    return cell;
}

function calculateProductionLoss() {
    const totalInput = Array.from(document.querySelectorAll('#prod-inputs tr[data-input-row] .prod-actual-input'))
        .reduce((sum, input) => sum + (Number(input.value) || 0), 0);
    const output = Number(document.getElementById('prod-actual-output')?.value) || 0;
    const loss = Math.max(0, totalInput - output);
    const gain = Math.max(0, output - totalInput);
    const preview = document.getElementById('prod-loss-preview');
    if (totalInput <= 0 || output <= 0) {
        preview.textContent = 'Enter ingredient quantities and actual output to calculate loss.';
        return;
    }
    const pct = loss / totalInput * 100;
    preview.textContent = `Input ${totalInput.toFixed(2)} kg · output ${output.toFixed(2)} kg · loss ${loss.toFixed(2)} kg (${pct.toFixed(2)}%)${gain ? ` · yield gain ${gain.toFixed(2)} kg` : ''}`;
}

async function submitProductionBatch() {
    const rows = Array.from(document.querySelectorAll('#prod-inputs tr[data-input-row]'));
    const inputs = rows.map(row => ({
        ingredient_id: Number(row.querySelector('.prod-ingredient').value),
        planned_qty_kg: row.querySelector('.prod-planned-input').value,
        actual_qty_kg: row.querySelector('.prod-actual-input').value
    }));
    const message = document.getElementById('prod-message');
    const payload = {
        formula_name: document.getElementById('prod-formula').value,
        output_ingredient_id: document.getElementById('prod-output-select').value,
        planned_output_kg: document.getElementById('prod-planned-output').value,
        actual_output_kg: document.getElementById('prod-actual-output').value,
        loss_reason: document.getElementById('prod-loss-reason').value,
        location_id: window.activeLocationId || '',
        inputs
    };
    try {
        const res = await fetch('/api/factory/produce', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (!res.ok || data.status !== 'success') throw new Error(data.message || 'Could not record batch.');
        message.style.color = 'green';
        message.textContent = `Batch ${data.batch_no} recorded. Loss: ${data.loss_kg.toFixed(2)} kg (${data.loss_pct.toFixed(2)}%). Input cost: KSh ${data.total_input_cost.toFixed(2)}.`;
        document.getElementById('prod-actual-output').value = '';
        document.getElementById('prod-loss-reason').value = '';
        rows.forEach(row => {
            row.querySelector('.prod-planned-input').value = '';
            row.querySelector('.prod-actual-input').value = '';
        });
        calculateProductionLoss();
        await loadFactoryDropdowns();
    } catch (err) {
        message.style.color = 'red';
        message.textContent = err.message || 'System error recording batch.';
    }
}

async function loadProductionRuns() {
    const body = document.getElementById('prod-history');
    if (!body) return;
    try {
        const res = await fetch('/api/factory/production-runs?location_id=' + encodeURIComponent(window.activeLocationId || ''), { credentials: 'same-origin' });
        if (!res.ok) throw new Error('Could not load production history.');
        const rows = await res.json();
        body.innerHTML = rows.map(run => `<tr>
          <td>${escHtml(run.batch_no)}</td><td>${escHtml(run.formula_name)}<br>${escHtml(run.output_item)}</td>
          <td>${run.total_input_kg.toFixed(2)}</td><td>${run.planned_output_kg.toFixed(2)}</td>
          <td>${run.actual_output_kg.toFixed(2)}</td><td>${run.output_variance_kg.toFixed(2)}</td>
          <td>${run.loss_kg.toFixed(2)}</td><td>${run.loss_pct.toFixed(2)}%</td><td>${run.loss_cost.toFixed(2)}</td>
          <td>${escHtml(run.loss_reason)}</td><td>${run.total_input_cost.toFixed(2)}</td><td>${escHtml(run.created_at)}</td>
          <td><button type="button" class="btn-sm btn-info" onclick="toggleProductionLines('${escHtml(run.batch_no)}', this)">Inputs</button></td>
        </tr><tr id="prod-lines-${escHtml(run.batch_no)}" hidden><td colspan="13"></td></tr>`).join('') || '<tr><td colspan="13">No production batches recorded.</td></tr>';
    } catch (err) {
        body.innerHTML = '<tr><td colspan="13">Could not load production history.</td></tr>';
    }
}

async function toggleProductionLines(batchNo, button) {
    const row = document.getElementById(`prod-lines-${batchNo}`);
    if (!row) return;
    if (!row.hidden) { row.hidden = true; return; }
    const cell = row.querySelector('td');
    cell.textContent = 'Loading ingredient use…';
    row.hidden = false;
    try {
        const res = await fetch(`/api/factory/production-runs/${encodeURIComponent(batchNo)}/lines?location_id=${encodeURIComponent(window.activeLocationId || '')}`, { credentials: 'same-origin' });
        const lines = await res.json();
        if (!res.ok) throw new Error('Unable to load batch inputs.');
        cell.innerHTML = `<table class="data-table"><thead><tr><th>Ingredient</th><th>Planned kg</th><th>Actual kg</th><th>Variance kg</th><th>Unit cost</th><th>Line cost</th></tr></thead><tbody>${lines.map(line => `<tr>
          <td>${escHtml(line.ingredient)}</td><td>${line.planned_qty_kg.toFixed(2)}</td><td>${line.actual_qty_kg.toFixed(2)}</td>
          <td>${line.variance_kg.toFixed(2)}</td><td>${line.cost_per_kg.toFixed(2)}</td><td>${line.line_cost.toFixed(2)}</td>
        </tr>`).join('') || '<tr><td colspan="6">No input lines.</td></tr>'}</tbody></table>`;
        button.textContent = 'Hide';
    } catch (err) {
        cell.textContent = err.message;
    }
}


async function useFormulaInProduction() {
    const f = window.lastFormulation;
    if (!f || !Array.isArray(f.recipe) || !f.recipe.length) { alert('Calculate a ration first.'); return; }
    showTab('factory-tab');
    await loadFactoryDropdowns();
    const rawIds = new Set(factoryInventory.filter(i => (i.category || '').includes('Raw')).map(i => i.id));
    const missing = f.recipe.filter(r => !rawIds.has(Number(r.ingredient_id))).map(r => r.ingredient_name);
    if (missing.length) {
        alert('These recipe items are not available as Raw ingredients here, so nothing was copied:\n' + missing.join(', '));
        return;
    }
    document.getElementById('prod-formula').value = f.species_stage || '';
    document.getElementById('prod-planned-output').value = Number(f.target_batch_kg).toFixed(2);
    document.getElementById('prod-actual-output').value = '';
    document.getElementById('prod-loss-reason').value = '';
    document.getElementById('prod-inputs').innerHTML = '';
    f.recipe.forEach(r => {
        addProductionInput(r.ingredient_id);
        const row = document.querySelector('#prod-inputs tr[data-input-row]:last-child');
        row.querySelector('.prod-planned-input').value = Number(r.kg_required).toFixed(2);
    });
    calculateProductionLoss();
    const msg = document.getElementById('prod-message');
    msg.style.color = '#0a58ca';
    msg.textContent = 'Formula loaded. Pick the Finished Feed output, then enter the ACTUAL kg you weighed for each ingredient.';
}


let savedFormulas = [];

async function loadSavedFormulas() {
    const sel = document.getElementById('saved-formula-select');
    if (!sel) return;
    try {
        const res = await fetch('/api/factory/formulas', { credentials: 'same-origin' });
        if (!res.ok) return;
        savedFormulas = await res.json();
    } catch (e) { return; }
    sel.innerHTML = savedFormulas.length ? '' : '<option value="">-- none saved --</option>';
    savedFormulas.forEach(fm => {
        const o = document.createElement('option');
        o.value = fm.id;
        o.textContent = fm.name;
        sel.appendChild(o);
    });
    renderFormulaLibrary();
}

function loadSavedFormula() {
    const fm = savedFormulas.find(x => String(x.id) === String(document.getElementById('saved-formula-select').value));
    const batch = Number(document.getElementById('saved-formula-batch').value);
    if (!fm) { alert('Choose a saved formula first.'); return; }
    if (!(batch > 0)) { alert('Enter the batch size in kg.'); return; }
    const rawIds = new Set(factoryInventory.filter(i => (i.category || '').includes('Raw')).map(i => i.id));
    const missing = fm.lines.filter(l => !rawIds.has(Number(l.ingredient_id))).map(l => l.ingredient_name);
    if (missing.length) {
        alert('These formula items are not available as Raw ingredients here, so nothing was copied:\n' + missing.join(', '));
        return;
    }
    const total = fm.lines.reduce((s, l) => s + Number(l.pct), 0);
    document.getElementById('prod-formula').value = fm.name;
    document.getElementById('prod-planned-output').value = batch.toFixed(2);
    document.getElementById('prod-actual-output').value = '';
    document.getElementById('prod-loss-reason').value = '';
    document.getElementById('prod-inputs').innerHTML = '';
    fm.lines.forEach(l => {
        addProductionInput(l.ingredient_id);
        const row = document.querySelector('#prod-inputs tr[data-input-row]:last-child');
        row.querySelector('.prod-planned-input').value = (batch * Number(l.pct) / total).toFixed(2);
    });
    calculateProductionLoss();
    const msg = document.getElementById('prod-message');
    msg.style.color = '#0a58ca';
    msg.textContent = 'Formula "' + fm.name + '" loaded for ' + batch.toFixed(2) + ' kg. ' + formulaCostNote(fm, batch) + 'Pick the Finished Feed output, then enter the ACTUAL kg you weighed for each ingredient.';
}


let formulaCosts = {};
let editingFormulaId = null;
let savingFormula = false;

function formulaCostNote(fm, batch) {
    if (fm.unpriced && fm.unpriced.length) return 'No cost set for: ' + fm.unpriced.join(', ') + ' (cost estimate is incomplete). ';
    return 'At today\'s costs: KSh ' + Number(fm.cost_per_kg).toFixed(2) + '/kg, about KSh ' + (Number(fm.cost_per_kg) * batch).toFixed(0) + ' for this batch. ';
}

async function loadFormulaCosts() {
    try {
        const res = await fetch('/api/factory/ingredient-costs', { credentials: 'same-origin' });
        if (res.ok) formulaCosts = await res.json();
    } catch (e) {}
}

function renderFormulaLibrary() {
    const body = document.getElementById('formula-list');
    if (!body) return;
    if (!savedFormulas.length) {
        body.innerHTML = '<tr><td colspan="5">No saved formulas yet. Click + New formula.</td></tr>';
        return;
    }
    body.innerHTML = '';
    savedFormulas.forEach(fm => {
        const tr = document.createElement('tr');
        const warn = fm.unpriced && fm.unpriced.length ? ' (no cost: ' + fm.unpriced.join(', ') + ')' : '';
        tr.innerHTML = '<td></td><td>' + fm.lines.length + '</td><td>' + Number(fm.total_pct).toFixed(3) +
            '</td><td>' + Number(fm.cost_per_kg).toFixed(2) + escHtml(warn) + '</td><td></td>';
        tr.children[0].textContent = fm.name;
        const edit = document.createElement('button');
        edit.type = 'button'; edit.className = 'btn-sm btn-info'; edit.textContent = 'Edit';
        edit.addEventListener('click', () => editFormula(fm.id));
        const del = document.createElement('button');
        del.type = 'button'; del.className = 'btn-sm btn-danger'; del.textContent = 'Delete';
        del.addEventListener('click', () => deleteFormula(fm.id));
        tr.children[4].appendChild(edit);
        tr.children[4].appendChild(document.createTextNode(' '));
        tr.children[4].appendChild(del);
        body.appendChild(tr);
    });
}

function openFormulaEditor(title) {
    document.getElementById('formula-editor-title').textContent = title;
    document.getElementById('formula-message').textContent = '';
    document.getElementById('formula-rows').innerHTML = '';
    document.getElementById('formula-editor').style.display = 'block';
}

function closeFormulaEditor() {
    editingFormulaId = null;
    document.getElementById('formula-editor').style.display = 'none';
}

async function newFormula() {
    await loadFormulaCosts();
    editingFormulaId = null;
    openFormulaEditor('New formula');
    document.getElementById('formula-name').value = '';
    document.getElementById('formula-notes').value = '';
    addFormulaRow();
    updateFormulaTotals();
}

async function editFormula(id) {
    const fm = savedFormulas.find(x => x.id === id);
    if (!fm) return;
    await loadFormulaCosts();
    editingFormulaId = id;
    openFormulaEditor('Edit formula');
    document.getElementById('formula-name').value = fm.name;
    document.getElementById('formula-notes').value = fm.notes || '';
    fm.lines.forEach(l => addFormulaRow(l.ingredient_id, l.pct));
    updateFormulaTotals();
}

function addFormulaRow(ingredientId = null, pct = '') {
    const body = document.getElementById('formula-rows');
    const row = document.createElement('tr');
    row.dataset.formulaRow = '1';
    const c1 = document.createElement('td');
    const select = document.createElement('select');
    select.className = 'formula-ingredient';
    factoryInventory.filter(i => (i.category || '').includes('Raw')).forEach(i => {
        const o = document.createElement('option');
        o.value = i.id; o.textContent = i.name;
        if (ingredientId != null && i.id === Number(ingredientId)) o.selected = true;
        select.appendChild(o);
    });
    select.addEventListener('change', updateFormulaTotals);
    c1.appendChild(select);
    const c2 = document.createElement('td');
    const input = document.createElement('input');
    input.type = 'number'; input.min = '0'; input.max = '100'; input.step = '0.001';
    input.className = 'formula-pct'; input.placeholder = '%'; input.value = pct;
    input.addEventListener('input', updateFormulaTotals);
    c2.appendChild(input);
    const c3 = document.createElement('td'); c3.className = 'formula-cost';
    const c4 = document.createElement('td'); c4.className = 'formula-linecost';
    const c5 = document.createElement('td');
    const rm = document.createElement('button');
    rm.type = 'button'; rm.className = 'btn-sm btn-danger'; rm.textContent = 'Remove';
    rm.addEventListener('click', () => { row.remove(); updateFormulaTotals(); });
    c5.appendChild(rm);
    [c1, c2, c3, c4, c5].forEach(c => row.appendChild(c));
    body.appendChild(row);
    updateFormulaTotals();
}

function readFormulaRows() {
    return Array.from(document.querySelectorAll('#formula-rows tr[data-formula-row]')).map(r => ({
        ingredient_id: Number(r.querySelector('.formula-ingredient').value),
        pct: Number(r.querySelector('.formula-pct').value) || 0,
        row: r
    }));
}

function updateFormulaTotals() {
    const rows = readFormulaRows();
    const total = rows.reduce((s, r) => s + r.pct, 0);
    let weighted = 0, unpriced = [];
    rows.forEach(r => {
        const cost = Number(formulaCosts[String(r.ingredient_id)] || 0);
        r.row.querySelector('.formula-cost').textContent = cost > 0 ? cost.toFixed(2) : 'no cost set';
        r.row.querySelector('.formula-linecost').textContent = cost > 0 ? (cost * r.pct / 100).toFixed(2) : '-';
        if (cost <= 0 && r.pct > 0) unpriced.push(r.row.querySelector('.formula-ingredient').selectedOptions[0]?.textContent || '?');
        weighted += cost * r.pct;
    });
    const ok = Math.abs(total - 100) <= 0.1;
    const el = document.getElementById('formula-total');
    el.style.color = ok ? '#0a7a2f' : '#b00';
    el.textContent = 'Total: ' + total.toFixed(3) + '%' + (ok ? '' : ' (must be 100%)') +
        (total > 0 ? ' - cost at today\'s prices: KSh ' + (weighted / total).toFixed(2) + ' per kg' : '') +
        (unpriced.length ? ' - no cost set for ' + unpriced.join(', ') : '');
}

function scaleFormulaTo100() {
    const rows = readFormulaRows();
    const total = rows.reduce((s, r) => s + r.pct, 0);
    if (total <= 0) return;
    rows.forEach(r => { r.row.querySelector('.formula-pct').value = (r.pct * 100 / total).toFixed(4); });
    updateFormulaTotals();
}

async function saveFormula() {
    if (savingFormula) return;
    const msg = document.getElementById('formula-message');
    const lines = readFormulaRows().filter(r => r.pct > 0).map(r => ({ ingredient_id: r.ingredient_id, pct: r.pct }));
    savingFormula = true;
    try {
        const res = await fetch('/api/factory/formulas', {
            method: 'POST', credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                id: editingFormulaId,
                name: document.getElementById('formula-name').value,
                notes: document.getElementById('formula-notes').value,
                lines: lines
            })
        });
        let data = {};
        try { data = await res.json(); } catch (e) {}
        if (res.ok && data.status === 'success') {
            closeFormulaEditor();
            await loadSavedFormulas();
        } else if (res.status === 403) {
            msg.style.color = '#b00'; msg.textContent = 'You do not have permission to save formulas.';
        } else {
            msg.style.color = '#b00'; msg.textContent = data.message || 'Could not save this formula.';
        }
    } catch (err) {
        msg.style.color = '#b00';
        msg.textContent = 'Network problem. Press Save again; if it was already saved, nothing is duplicated.';
    } finally {
        savingFormula = false;
    }
}

async function deleteFormula(id) {
    const fm = savedFormulas.find(x => x.id === id);
    if (!fm || !confirm('Delete formula "' + fm.name + '"? Past production batches are not affected.')) return;
    try {
        const res = await fetch('/api/factory/formulas/' + id, { method: 'DELETE', credentials: 'same-origin' });
        if (res.status === 403) { alert('Only an admin can delete formulas.'); return; }
        if (!res.ok) { alert('Could not delete this formula.'); return; }
        await loadSavedFormulas();
    } catch (err) {
        alert('Network problem. Check the list and try again.');
    }
}


let formulaImport = [];
let formulaImportSkipped = [];
let importingFormulas = false;

function importNorm(s) { return String(s || '').toLowerCase().replace(/[^a-z0-9]/g, ''); }

async function previewFormulaImport() {
    const input = document.getElementById('formula-import-file');
    const panel = document.getElementById('formula-import-panel');
    if (!input.files.length) { alert('Choose your Excel (.xlsx) file first.'); return; }
    const fd = new FormData();
    fd.append('file', input.files[0]);
    panel.style.display = 'block';
    panel.style.color = '';
    panel.textContent = 'Reading file...';
    try {
        const res = await fetch('/api/factory/formulas/import-preview', { method: 'POST', credentials: 'same-origin', body: fd });
        let data = {};
        try { data = await res.json(); } catch (e) {}
        if (!res.ok || data.status !== 'success') {
            panel.style.color = '#b00';
            panel.textContent = data.message || (res.status === 403 ? 'You do not have permission to import formulas.' : 'Could not read this file.');
            return;
        }
        formulaImport = data.formulas;
        formulaImportSkipped = data.skipped || [];
        renderFormulaImport();
    } catch (e) {
        panel.style.color = '#b00';
        panel.textContent = 'Network problem. Try again.';
    }
}

function renderFormulaImport() {
    const panel = document.getElementById('formula-import-panel');
    panel.style.color = '';
    panel.innerHTML = '';
    const raw = factoryInventory.filter(i => (i.category || '').includes('Raw'));
    const head = document.createElement('p');
    head.textContent = 'Check each Excel ingredient points at the right item in your system. Amber means a guess - confirm it. Your choices are remembered for next time.';
    panel.appendChild(head);
    formulaImportSkipped.forEach(m => {
        const d = document.createElement('div');
        d.style.color = '#b06000';
        d.textContent = m;
        panel.appendChild(d);
    });
    formulaImport.forEach(f => {
        const box = document.createElement('div');
        box.style.cssText = 'border:1px solid #ccc; border-radius:6px; padding:10px; margin-bottom:12px; background:#fff;';
        const include = document.createElement('input');
        include.type = 'checkbox'; include.checked = !f.exists;
        const nameInput = document.createElement('input');
        nameInput.type = 'text'; nameInput.value = f.name; nameInput.maxLength = 100;
        const top = document.createElement('div');
        top.appendChild(include);
        top.appendChild(document.createTextNode(' Import as: '));
        top.appendChild(nameInput);
        box.appendChild(top);
        if (f.exists) {
            const w = document.createElement('div');
            w.style.color = '#b06000';
            w.textContent = 'A formula with this name already exists. Rename it and tick the box to import.';
            box.appendChild(w);
        }
        const table = document.createElement('table');
        table.className = 'data-table';
        table.innerHTML = '<thead><tr><th>Excel ingredient</th><th>%</th><th>Your item</th><th></th></tr></thead>';
        const tb = document.createElement('tbody');
        const selects = [];
        f.lines.forEach(l => {
            const tr = document.createElement('tr');
            const c1 = document.createElement('td'); c1.textContent = l.source_name;
            const c2 = document.createElement('td'); c2.textContent = Number(l.pct).toFixed(3);
            const c3 = document.createElement('td');
            const sel = document.createElement('select');
            const blank = document.createElement('option'); blank.value = ''; blank.textContent = '-- choose item --';
            sel.appendChild(blank);
            raw.forEach(i => {
                const o = document.createElement('option');
                o.value = i.id; o.textContent = i.name;
                if (l.match_id != null && Number(l.match_id) === i.id) o.selected = true;
                sel.appendChild(o);
            });
            c3.appendChild(sel);
            const c4 = document.createElement('td');
            const paint = () => {
                if (!sel.value) { c4.textContent = 'needs an item'; c4.style.color = '#b00'; }
                else if (l.suggested && Number(sel.value) === Number(l.match_id)) { c4.textContent = 'check this guess'; c4.style.color = '#b06000'; }
                else { c4.textContent = 'ok'; c4.style.color = '#0a7a2f'; }
            };
            sel.addEventListener('change', paint);
            paint();
            [c1, c2, c3, c4].forEach(c => tr.appendChild(c));
            tb.appendChild(tr);
            selects.push(sel);
        });
        table.appendChild(tb);
        const wrap = document.createElement('div');
        wrap.style.overflowX = 'auto';
        wrap.appendChild(table);
        box.appendChild(wrap);
        const tot = document.createElement('div');
        tot.textContent = 'Total in Excel: ' + Number(f.total_pct).toFixed(3) + '%';
        box.appendChild(tot);
        let scale = null;
        if (Math.abs(f.total_pct - 100) > 0.1) {
            scale = document.createElement('input');
            scale.type = 'checkbox'; scale.checked = true;
            const lab = document.createElement('label');
            lab.appendChild(scale);
            lab.appendChild(document.createTextNode(' Scale to 100% (total is outside 99.9-100.1)'));
            box.appendChild(lab);
        }
        f.ui = { include, nameInput, selects, scale };
        panel.appendChild(box);
    });
    const go = document.createElement('button');
    go.type = 'button'; go.className = 'btn-success'; go.textContent = 'Import selected formulas';
    go.addEventListener('click', commitFormulaImport);
    const cancel = document.createElement('button');
    cancel.type = 'button'; cancel.className = 'btn-sm btn-info'; cancel.textContent = 'Cancel';
    cancel.addEventListener('click', () => { panel.style.display = 'none'; panel.innerHTML = ''; });
    panel.appendChild(go);
    panel.appendChild(document.createTextNode(' '));
    panel.appendChild(cancel);
    const result = document.createElement('div');
    result.id = 'formula-import-result';
    result.style.marginTop = '10px';
    panel.appendChild(result);
}

async function commitFormulaImport() {
    if (importingFormulas) return;
    importingFormulas = true;
    const result = document.getElementById('formula-import-result');
    const messages = [];
    const aliasPairs = [];
    try {
        for (const f of formulaImport) {
            if (!f.ui.include.checked) continue;
            const name = f.ui.nameInput.value.trim();
            const merged = new Map();
            let complete = true;
            f.lines.forEach((l, li) => {
                const id = Number(f.ui.selects[li].value);
                if (!id) { complete = false; return; }
                merged.set(id, (merged.get(id) || 0) + Number(l.pct));
            });
            if (!complete) { messages.push(name + ': choose an item for every line.'); continue; }
            let lines = Array.from(merged, ([id, pct]) => ({ ingredient_id: id, pct: pct }));
            const total = lines.reduce((s, l) => s + l.pct, 0);
            if (f.ui.scale && f.ui.scale.checked) lines = lines.map(l => ({ ingredient_id: l.ingredient_id, pct: l.pct * 100 / total }));
            const res = await fetch('/api/factory/formulas', {
                method: 'POST', credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: name, notes: 'Imported from Excel', lines: lines })
            });
            let data = {};
            try { data = await res.json(); } catch (e) {}
            if (res.ok && data.status === 'success') {
                messages.push(name + ': saved.');
                f.lines.forEach((l, li) => {
                    const sel = f.ui.selects[li];
                    const itemName = sel.selectedOptions[0] ? sel.selectedOptions[0].textContent : '';
                    if (importNorm(l.source_name) !== importNorm(itemName)) aliasPairs.push({ alias: l.source_name, ingredient_id: Number(sel.value) });
                });
                f.ui.include.checked = false;
            } else {
                messages.push(name + ': ' + (data.message || 'could not be saved.'));
            }
        }
        if (aliasPairs.length) {
            try {
                await fetch('/api/factory/formula-aliases', {
                    method: 'POST', credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ aliases: aliasPairs })
                });
            } catch (e) {}
        }
        await loadSavedFormulas();
    } catch (err) {
        messages.push('Network problem. Check the Formula Library before importing again; nothing is duplicated if a formula was already saved.');
    } finally {
        importingFormulas = false;
    }
    result.style.color = '#0a58ca';
    result.innerHTML = '';
    messages.forEach(m => { const d = document.createElement('div'); d.textContent = m; result.appendChild(d); });
}
