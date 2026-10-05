let factoryInventory = [];

async function runFormulation() {
    const speciesSelect = document.getElementById('species-select');
    if (!speciesSelect) return;
    const result = document.getElementById('formulation-result');
    try {
        const res = await fetch('/api/formulate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                species: speciesSelect.value,
                target_batch_kg: document.getElementById('batch-kg').value
            })
        });
        const data = await res.json();
        result.textContent = data.message || JSON.stringify(data, null, 2);
    } catch (err) {
        result.textContent = 'Could not calculate a formulation. Check the connection and try again.';
    }
}

async function loadFactoryDropdowns() {
    const res = await fetch('/api/inventory?location_id=' + encodeURIComponent(window.activeLocationId || ''), { credentials: 'same-origin' });
    if (!res.ok) return;
    factoryInventory = await res.json();
    const millIn = document.getElementById('mill-input-select');
    const millOut = document.getElementById('mill-output-select');
    const prodOut = document.getElementById('prod-output-select');
    if (!millIn || !millOut || !prodOut) return;

    millIn.innerHTML = '';
    millOut.innerHTML = '';
    prodOut.innerHTML = '';
    factoryInventory.forEach(item => {
        const category = item.category || '';
        const option = `<option value="${item.id}">${escHtml(item.name)} (${Number(item.stock_quantity_kg || 0).toFixed(2)} kg available)</option>`;
        if (category.includes('Raw - Energy')) millIn.insertAdjacentHTML('beforeend', option);
        if (category.includes('Raw') || category.includes('Finished')) millOut.insertAdjacentHTML('beforeend', option);
        if (category === 'Finished Feed') prodOut.insertAdjacentHTML('beforeend', option);
    });
    const firstRaw = factoryInventory.find(item => (item.category || '').includes('Raw'));
    if (firstRaw && !document.querySelector('#prod-inputs tr[data-input-row]')) addProductionInput(firstRaw.id);
    loadProductionRuns();
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
