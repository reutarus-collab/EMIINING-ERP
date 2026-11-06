/* Formulation Lab: manual, least-cost and verify modes. The server is authoritative for every number. */
const LAB = {
  ready: false, nutrients: [], nut: {}, profiles: [], ings: [], ing: {}, profileId: '', profileMeta: null,
  lines: [], rows: [], mode: 'manual', res: null, dirty: false, cols: null, changed: {}, overrides: {},
  timer: null, savedName: '', savedOk: false, saved: [], seq: 0,
};
const $l = id => document.getElementById(id);
const labEsc = s => String(s === null || s === undefined ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const labFmt = (v, d = 3) => (v === null || v === undefined || !isFinite(v)) ? '-' : Number(v).toFixed(d);
const labNum = v => { if (v === '' || v === null || v === undefined) return null; const n = Number(v); return isFinite(n) ? n : null; };

async function labApi(url, opts) {
  try {
    const r = await fetch(url, Object.assign({ credentials: 'same-origin' }, opts || {}));
    let d;
    try { d = await r.json(); } catch (e) { d = { status: 'error', message: 'Unexpected reply from the server (' + r.status + '). You may need to log in again.' }; }
    return { ok: r.ok, code: r.status, d };
  } catch (e) {
    return { ok: false, code: 0, d: { status: 'error', message: 'Could not reach the server. Check the connection.' } };
  }
}
const labPost = (url, body) => labApi(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

/* ---------------------------------------------------------------- setup */
async function labInit() {
  const r = await labApi('/api/nutrition/bootstrap');
  if (!r.ok) { $l('lab-hint').textContent = r.d.message || 'Could not load the Formulation Lab.'; return; }
  LAB.nutrients = r.d.nutrients; LAB.nut = {};
  LAB.nutrients.forEach(n => { LAB.nut[n.code] = n; });
  LAB.profiles = r.d.profiles; LAB.ings = r.d.ingredients; LAB.ing = {};
  LAB.ings.forEach(i => { LAB.ing[i.id] = i; });
  LAB.rows.forEach(rw => { if (LAB.ing[rw.id]) { rw.price = LAB.ing[rw.id].price; rw.name = LAB.ing[rw.id].name; } });
  labFillSelects();
  if (!LAB.ready) {
    LAB.ready = true;
    const first = LAB.profiles[0];
    if (first) { $l('lab-profile').value = first.id; await labLoadProfile(first.id); }
    labSetMode('manual');
    labLoadSavedList();
  } else {
    $l('lab-profile').value = LAB.profileId;
    labRenderAll();
  }
}

function labFillSelects() {
  const ps = $l('lab-profile');
  ps.innerHTML = '<option value="">(no profile - scratch pad)</option>' + LAB.profiles.map(p => `<option value="${p.id}">${labEsc(p.name)}${p.species && p.species !== 'other' ? ' - ' + labEsc(p.species) : ''}</option>`).join('');
  ps.value = LAB.profileId;
  const nutOpts = LAB.nutrients.map(n => `<option value="${labEsc(n.code)}">${labEsc(n.name)} (${labEsc(n.unit)})</option>`).join('');
  $l('lab-add-nut').innerHTML = nutOpts;
  $l('lab-ratio-a').innerHTML = nutOpts; $l('lab-ratio-b').innerHTML = nutOpts;
  const names = {}; const dl = [];
  LAB.ings.forEach(i => { let label = i.name; if (names[label]) label = i.name + ' #' + i.id; names[label] = i.id; dl.push(`<option value="${labEsc(label)}">`); });
  LAB.nameToId = names;
  $l('lab-ing-list').innerHTML = dl.join('');
}

async function labPickProfile() {
  if (LAB.dirty && !confirm('You changed limits that are not saved. Discard them?')) { $l('lab-profile').value = LAB.profileId; return; }
  await labLoadProfile($l('lab-profile').value);
  labRenderAll();
}

async function labLoadProfile(id) {
  LAB.profileId = id || ''; LAB.dirty = false; LAB.res = null;
  if (!id) { LAB.lines = []; LAB.profileMeta = null; labProfileNote(); return; }
  const r = await labApi('/api/nutrition/profiles/' + id);
  if (!r.ok) { alert(r.d.message || 'Could not load that profile.'); LAB.lines = []; return; }
  LAB.profileMeta = r.d;
  LAB.profileMeta.bounds = r.d.bounds || [];
  LAB.lines = r.d.lines.map(l => Object.assign({}, l));
  labProfileNote();
}

function labProfileNote() {
  const m = LAB.profileMeta;
  $l('lab-profile-note').innerHTML = m ? (m.source ? 'Source of these limits: ' + labEsc(m.source) : 'No source recorded for these limits - add one when you save.') + (LAB.dirty ? ' <b style="color:#b42318">Unsaved changes.</b>' : '') : 'Scratch pad: limits are not saved, and a formula cannot be released without a saved profile.';
}

function labLineLabel(l) {
  const n = LAB.nut[l.code] || { name: l.code, basis: 'asfed' }; const d = LAB.nut[l.per];
  if (l.per === 'asfed') return n.name;
  if (l.per === 'dm') return n.basis === 'dm' ? n.name : n.name + ' (per kg DM)';
  if (labIsGPerMcal(l, n, d)) return n.name + ' (g/Mcal ' + (d ? d.name : l.per) + ')';
  return Number(l.scale) === 100 ? n.name + ' as % of ' + (d ? d.name : l.per) : n.name + ' : ' + (d ? d.name : l.per);
}
function labIsGPerMcal(l, n = LAB.nut[l.code], d = LAB.nut[l.per]) {
  if (!n || !d || !String(l.code).startsWith('SID_') || n.unit !== '%') return false;
  return (d.unit === 'Mcal/kg' && Number(l.scale) === 10) || (d.unit === 'kcal/kg' && Number(l.scale) === 10000);
}
function labLineUnit(l) {
  const n = LAB.nut[l.code] || { unit: '', basis: 'asfed' }; const d = LAB.nut[l.per];
  if (l.per === 'asfed') return n.unit;
  if (l.per === 'dm') return n.basis === 'dm' ? n.unit : n.unit + ' of DM';
  if (labIsGPerMcal(l, n, d)) return 'g/Mcal';
  return Number(l.scale) === 100 ? '% of ' + (d ? d.name : l.per) : 'ratio';
}

/* ---------------------------------------------------------------- modes */
const LAB_HINTS = {
  manual: 'Type the inclusion % of each ingredient. Every nutrient is recomputed live from your ingredient data. Unknown data shows as "no data" and is never counted as zero.',
  optimize: 'Tick the ingredients the solver may use, optionally set min/max % and a price, then press Optimise. It finds the cheapest recipe that meets every REQUIRED limit, then re-checks the answer independently.',
  verify: 'Enter or load a formula. The server recalculates every limit from the raw data, separately from the optimiser, and blocks release if any required limit fails or any needed data is missing.',
};
function labSetMode(m) {
  LAB.mode = m; LAB.res = null;
  ['manual', 'optimize', 'verify'].forEach(k => $l('lab-mode-' + k).classList.toggle('on', k === m));
  $l('lab-hint').textContent = LAB_HINTS[m];
  labRenderAll();
  if (m !== 'optimize' && LAB.rows.some(r => labNum(r.pct) > 0)) labVerifySoon(0);
}
function labRenderAll() { labRenderRows(); labRenderConstraints(); labRenderBounds(); labRenderActions(); labRenderResult(); labProfileNote(); }

function labRenderBounds() {
  const t = $l('lab-profile-bounds'); if (!t) return;
  const bounds = (LAB.profileMeta && LAB.profileMeta.bounds) || [];
  const options = LAB.ings.map(i => `<option value="${i.id}">${labEsc(i.name)}</option>`).join('');
  t.innerHTML = '<tr><th>Ingredient</th><th>Min %</th><th>Max %</th><th>Source</th><th></th></tr>' +
    bounds.map((b, i) => `<tr><td><select onchange="labBoundEdit(${i},'ingredient_id',this.value)">${options.replace(`value="${b.ingredient_id}"`, `value="${b.ingredient_id}" selected`)}</select></td>` +
      `<td><input class="lab-n" type="number" min="0" max="100" step="any" value="${b.min ?? 0}" onchange="labBoundEdit(${i},'min',this.value)"></td>` +
      `<td><input class="lab-n" type="number" min="0" max="100" step="any" value="${b.max ?? 100}" onchange="labBoundEdit(${i},'max',this.value)"></td>` +
      `<td><input value="${labEsc(b.source || '')}" placeholder="Source for cap" onchange="labBoundEdit(${i},'source',this.value)"></td>` +
      `<td><button class="btn-sm btn-danger" onclick="labRemoveBound(${i})">x</button></td></tr>`).join('');
}
function labAddBound() {
  if (!LAB.profileId || !LAB.profileMeta) { alert('Choose a saved profile before adding caps.'); return; }
  const available = LAB.ings.find(i => !(LAB.profileMeta.bounds || []).some(b => Number(b.ingredient_id) === i.id));
  if (!available) return;
  LAB.profileMeta.bounds.push({ ingredient_id: available.id, min: 0, max: 100, source: '' });
  LAB.dirty = true; labRenderBounds(); labProfileNote();
}
function labBoundEdit(i, key, value) {
  if (!LAB.profileMeta) return;
  const b = LAB.profileMeta.bounds[i]; b[key] = key === 'source' ? value : Number(value);
  LAB.dirty = true; labProfileNote();
}
function labRemoveBound(i) { LAB.profileMeta.bounds.splice(i, 1); LAB.dirty = true; labRenderBounds(); labProfileNote(); }

/* ---------------------------------------------------------------- ingredient rows */
function labAddIngredient(id) {
  id = Number(id);
  if (!LAB.ing[id] || LAB.rows.some(r => r.id === id)) return;
  LAB.rows.push({ id, name: LAB.ing[id].name, price: LAB.ing[id].price, pct: '', min: '', max: '', ov: '', use: true });
}
function labAddFromInput() {
  const el = $l('lab-add-ing'); const id = LAB.nameToId[el.value.trim()];
  if (!id) { alert('Pick an ingredient from the list.'); return; }
  labAddIngredient(id); el.value = ''; labRenderRows(); labRenderResult();
}
function labAddAll() { LAB.ings.forEach(i => labAddIngredient(i.id)); labRenderRows(); labRenderResult(); }
function labRemoveRow(id) { LAB.rows = LAB.rows.filter(r => r.id !== id); labRenderRows(); labVerifySoon(); }

function labRenderRows() {
  const opt = LAB.mode === 'optimize'; const t = $l('lab-ing-table');
  if (!LAB.rows.length) { t.innerHTML = '<tr><td class="lab-small">No ingredients yet. Add them above, or load a saved formula.</td></tr>'; return; }
  const batch = labNum($l('lab-batch').value) || 0;
  const head = opt
    ? '<tr><th>Use</th><th>Ingredient</th><th>Price/kg</th><th>Min %</th><th>Max %</th><th>Result %</th><th>kg</th><th>Weigh (kg)</th><th></th></tr>'
    : '<tr><th>Ingredient</th><th>Price/kg</th><th>Inclusion %</th><th>kg</th><th></th></tr>';
  const body = LAB.rows.map((r, k) => {
    const del = `<button class="btn-sm btn-danger" onclick="labRemoveRow(${r.id})">x</button>`;
    const cap = LAB.ing[r.id] ? LAB.ing[r.id].max_pct : 100;
    const nm = `${labEsc(r.name)}${cap < 100 ? ` <span class="lab-small">(max ${cap}%)</span>` : ''}`;
    const noPrice = !(r.price > 0) && !(labNum(r.ov) > 0) ? ' <span class="lab-fail">no price</span>' : '';
    if (opt) {
      const rk = r.weigh === undefined ? '' : r.weigh;
      return `<tr><td><input type="checkbox" ${r.use ? 'checked' : ''} onchange="LAB.rows[${k}].use=this.checked"></td><td>${nm}${noPrice}</td>
        <td><input class="lab-n" type="number" step="0.01" min="0" placeholder="${labFmt(r.price, 2)}" value="${labEsc(r.ov)}" oninput="LAB.rows[${k}].ov=this.value"></td>
        <td><input class="lab-n" type="number" step="0.1" min="0" max="100" value="${labEsc(r.min)}" oninput="LAB.rows[${k}].min=this.value"></td>
        <td><input class="lab-n" type="number" step="0.1" min="0" max="100" value="${labEsc(r.max)}" oninput="LAB.rows[${k}].max=this.value"></td>
        <td id="lab-pct-${r.id}"><b>${r.pct === '' ? '' : labFmt(r.pct, 3)}</b></td><td id="lab-kg-${r.id}">${r.pct === '' ? '' : labFmt(r.pct * batch / 100, 2)}</td><td>${rk === '' ? '' : labFmt(rk, 2)}</td><td>${del}</td></tr>`;
    }
    return `<tr><td>${nm}${noPrice}</td><td>${labFmt(r.price, 2)}</td>
      <td><input class="lab-n" type="number" step="0.001" min="0" max="100" value="${labEsc(r.pct)}" oninput="labPctInput(${k}, this.value)"></td>
      <td id="lab-kg-${r.id}">${labNum(r.pct) === null ? '' : labFmt(r.pct * batch / 100, 2)}</td><td>${del}</td></tr>`;
  }).join('');
  const total = LAB.rows.reduce((s, r) => s + (labNum(r.pct) || 0), 0);
  const foot = opt ? '' : `<tr><th>Total</th><th></th><th id="lab-total">${labFmt(total, 3)}</th><th id="lab-total-kg">${labFmt(total * batch / 100, 2)}</th><th></th></tr>`;
  t.innerHTML = head + body + foot;
}

function labPctInput(k, v) {
  LAB.rows[k].pct = v;
  const batch = labNum($l('lab-batch').value) || 0;
  const kg = $l('lab-kg-' + LAB.rows[k].id); if (kg) kg.textContent = labNum(v) === null ? '' : labFmt(v * batch / 100, 2);
  const total = LAB.rows.reduce((s, r) => s + (labNum(r.pct) || 0), 0);
  if ($l('lab-total')) { $l('lab-total').textContent = labFmt(total, 3); $l('lab-total-kg').textContent = labFmt(total * batch / 100, 2); }
  labVerifySoon();
}
function labNormalize() {
  const total = LAB.rows.reduce((s, r) => s + (labNum(r.pct) || 0), 0);
  if (!(total > 0)) { alert('Enter some inclusions first.'); return; }
  LAB.rows.forEach(r => { if (labNum(r.pct) > 0) r.pct = Math.round(r.pct / total * 100 * 10000) / 10000; });
  labRenderRows(); labVerifySoon(0);
}
function labClear() { LAB.rows.forEach(r => { r.pct = ''; r.weigh = undefined; }); LAB.res = null; labRenderAll(); }

/* ---------------------------------------------------------------- limits table */
function labPayloadLines() {
  return LAB.lines.map(l => ({ code: l.code, per: l.per, scale: l.scale, min: l.min, max: l.max, tolerance: l.tolerance || 0, required: l.required !== false, enabled: l.enabled !== false }));
}
function labRenderConstraints() {
  const t = $l('lab-con-table');
  if (!LAB.lines.length) { t.innerHTML = '<tr><td class="lab-small">No limits yet. Pick a profile, import limits from Excel, or add one below.</td></tr>'; return; }
  const head = '<tr><th>On</th><th>Required</th><th>Nutrient</th><th>Min</th><th>Actual</th><th>Max</th><th>Unit</th><th>Status</th><th>Costs you</th><th></th></tr>';
  t.innerHTML = head + LAB.lines.map((l, i) => `<tr>
    <td><input type="checkbox" ${l.enabled !== false ? 'checked' : ''} onchange="labLineEdit(${i},'enabled',this.checked)"></td>
    <td><input type="checkbox" ${l.required !== false ? 'checked' : ''} title="Unticked = advisory only (shown, never blocks)" onchange="labLineEdit(${i},'required',this.checked)"></td>
    <td>${labEsc(labLineLabel(l))}</td>
    <td><input class="lab-n" type="number" step="any" min="0" value="${l.min === null || l.min === undefined ? '' : l.min}" oninput="labLineEdit(${i},'min',this.value)"></td>
    <td id="lab-act-${i}" style="text-align:right"></td>
    <td><input class="lab-n" type="number" step="any" min="0" value="${l.max === null || l.max === undefined ? '' : l.max}" oninput="labLineEdit(${i},'max',this.value)"></td>
    <td>${labEsc(labLineUnit(l))}</td><td id="lab-st-${i}"></td><td id="lab-note-${i}" class="lab-small"></td>
    <td><button class="btn-sm btn-danger" onclick="labRemoveLine(${i})">x</button></td></tr>`).join('');
  labFillConstraintResults();
}
function labLineEdit(i, key, v) {
  if (key === 'min' || key === 'max') {
    v = labNum(v);
    const ln = LAB.lines[i];
    if (v !== null && ln.min === null && ln.max === null) {                    // first limit typed: it must actually be enforced
      ln.required = true; ln.enabled = true;
      const row = $l('lab-con-table').rows[i + 1];
      if (row) { row.cells[0].firstChild.checked = true; row.cells[1].firstChild.checked = true; }
    }
  }
  LAB.lines[i][key] = v; LAB.dirty = true; LAB.res = LAB.res && LAB.res.kind === 'optimize' ? Object.assign({}, LAB.res, { stale: true }) : LAB.res;
  labProfileNote(); labVerifySoon();
}
function labRemoveLine(i) { LAB.lines.splice(i, 1); LAB.dirty = true; labRenderConstraints(); labProfileNote(); labVerifySoon(); }
function labAddLine() {
  const code = $l('lab-add-nut').value; const n = LAB.nut[code]; if (!n) return;
  LAB.lines.push({ code, per: n.per, scale: n.scale, min: null, max: null, tolerance: 0, required: true, enabled: true });
  LAB.dirty = true; labRenderConstraints(); labProfileNote();
}
function labAddRatio() {
  const a = $l('lab-ratio-a').value, b = $l('lab-ratio-b').value;
  if (a === b) { alert('Pick two different nutrients.'); return; }
  const top = LAB.nut[a], bottom = LAB.nut[b];
  const scale = String(a).startsWith('SID_') && top?.unit === '%' && bottom?.unit === 'Mcal/kg' ? 10
    : (String(a).startsWith('SID_') && top?.unit === '%' && bottom?.unit === 'kcal/kg' ? 10000 : 1);
  LAB.lines.push({ code: a, per: b, scale, min: null, max: null, tolerance: 0, required: true, enabled: true });
  LAB.dirty = true; labRenderConstraints(); labProfileNote();
}
const LAB_SID = ['SID_LYS', 'SID_MET', 'SID_MC', 'SID_THR', 'SID_TRP', 'SID_ARG', 'SID_ILE', 'SID_VAL', 'SID_LEU', 'SID_HIS', 'SID_PHE', 'SID_GLY', 'SID_SER', 'SID_GLYSER', 'SID_CYS', 'SID_TYR', 'SID_TAAA'];
function labAddSidSet() {
  const have = new Set(LAB.lines.map(l => l.code + '|' + l.per));
  let added = 0;
  const push = (code, per, scale) => {
    if (!LAB.nut[code] || (per !== 'asfed' && !LAB.nut[per]) || have.has(code + '|' + per)) return;
    LAB.lines.push({ code, per, scale, min: null, max: null, tolerance: 0, required: true, enabled: true }); added++;
  };
  LAB_SID.forEach(c => push(c, 'asfed', 1));
  LAB_SID.filter(c => c !== 'SID_LYS').forEach(c => push(c, 'SID_LYS', 100));       // each amino acid as % of SID lysine
  LAB.dirty = true; labRenderConstraints(); labProfileNote(); labVerifySoon(0);
  $l('lab-sid-note').textContent = added ? `Added ${added} SID amino acid lines, including each amino acid as a percentage of SID lysine (Lys = 100%). Enter target limits from your breeder specification, then Save profile.` : 'The SID amino acid profile lines are already in this profile.';
}

const LAB_STATUS = { pass: ['lab-pass', 'OK'], fail: ['lab-fail', 'FAIL'], warn: ['lab-warn', 'advisory'], missing: ['lab-miss', 'no data'], info: ['lab-info', '-'], off: ['lab-off', 'off'] };
function labFillConstraintResults() {
  const res = LAB.res && !LAB.res.stale && LAB.res.d && Array.isArray(LAB.res.d.lines) && LAB.res.d.lines.length === LAB.lines.length ? LAB.res.d : null;
  LAB.lines.forEach((l, i) => {
    const act = $l('lab-act-' + i), st = $l('lab-st-' + i), note = $l('lab-note-' + i);
    if (!act) return;
    if (!res) { act.textContent = ''; st.textContent = ''; note.textContent = ''; return; }
    const r = res.lines[i]; const s = LAB_STATUS[r.status] || ['', r.status];
    act.textContent = r.value === null || r.value === undefined ? '-' : labFmt(r.value, 3);
    st.className = s[0]; st.textContent = s[1];
    if (r.status === 'missing' && r.missing_ingredients && r.missing_ingredients.length)
      st.title = 'No value for: ' + r.missing_ingredients.map(id => (LAB.ing[id] || {}).name || id).join(', ');
    const sens = (LAB.res.d.sensitivity || []).filter(x => x.label === r.label);
    note.textContent = sens.map(x => (x.cost_per_kg_per_unit_tighter !== null ? '+' + labFmt(x.cost_per_kg_per_unit_tighter, 2) + '/kg per unit tighter' : '') + (x.saving_per_kg_per_unit_looser !== null ? ', saves ' + labFmt(x.saving_per_kg_per_unit_looser, 2) + ' per unit looser' : '')).join('; ');
  });
}

/* ---------------------------------------------------------------- actions + results */
function labRenderActions() {
  const a = $l('lab-actions'); const m = LAB.mode;
  if (m === 'optimize') a.innerHTML = '<button class="btn-primary" onclick="labOptimize(false)" style="padding:9px 18px">Optimise (least cost)</button> <button class="btn-sm btn-info" onclick="labUseResult()">Use result as manual formula</button>';
  else if (m === 'verify') a.innerHTML = '<button class="btn-primary" onclick="labVerify(false)" style="padding:9px 18px">Verify formula</button> <button class="btn-sm btn-info" onclick="labNormalize()">Normalise to 100%</button> <button class="btn-sm btn-info" onclick="labClear()">Clear</button>';
  else a.innerHTML = '<button class="btn-sm btn-info" onclick="labNormalize()">Normalise to 100%</button> <button class="btn-sm btn-info" onclick="labClear()">Clear</button>';
}

function labVerifySoon(delay) {
  if (LAB.mode === 'optimize') return;
  clearTimeout(LAB.timer);
  LAB.timer = setTimeout(() => labVerify(true), delay === undefined ? 450 : delay);
}
function labRecipe() { return LAB.rows.filter(r => labNum(r.pct) > 0).map(r => ({ ingredient_id: r.id, pct: Number(r.pct) })); }

async function labVerify(live) {
  const recipe = labRecipe();
  if (!recipe.length) { LAB.res = null; labRenderResult(); labFillConstraintResults(); return; }
  const seq = ++LAB.seq;
  const r = await labPost('/api/nutrition/verify', { recipe, lines: labPayloadLines(), profile_id: LAB.profileId || null });
  if (seq !== LAB.seq) return;                       // a newer request is already on its way
  if (!r.ok) { LAB.res = { kind: 'error', d: r.d }; labRenderResult(); return; }
  LAB.res = { kind: 'verify', d: r.d, live };
  labRenderResult(); labFillConstraintResults();
}

async function labOptimize(excludeMissing) {
  const use = LAB.rows.filter(r => r.use);
  if (!use.length) { alert('Tick at least one ingredient.'); return; }
  const bounds = {}, prices = {};
  use.forEach(r => {
    if (labNum(r.min) !== null || labNum(r.max) !== null) bounds[r.id] = { min: labNum(r.min) || 0, max: labNum(r.max) === null ? 100 : labNum(r.max) };
    if (labNum(r.ov) > 0) prices[r.id] = labNum(r.ov);
  });
  $l('lab-messages').innerHTML = '<span class="lab-small">Optimising...</span>';
  const r = await labPost('/api/nutrition/optimize', { ingredient_ids: use.map(x => x.id), lines: labPayloadLines(), bounds, prices, profile_id: LAB.profileId || null,
    batch_kg: labNum($l('lab-batch').value) || 1000, precision_kg: labNum($l('lab-precision').value) || 0.1, exclude_missing: !!excludeMissing });
  if (!r.ok) { LAB.res = { kind: 'error', d: r.d }; labRenderResult(); return; }
  LAB.res = { kind: 'optimize', d: r.d, bounds };
  if (r.d.status === 'optimal') {
    LAB.rows.forEach(x => { x.pct = 0; x.weigh = undefined; });
    r.d.recipe.forEach(it => { const row = LAB.rows.find(x => x.id === it.ingredient_id); if (row) { row.pct = it.pct; row.weigh = it.rounded_kg; } });
    labRenderRows();
  }
  labRenderResult(); labFillConstraintResults();
}
function labUseResult() {
  if (!LAB.res || LAB.res.kind !== 'optimize' || LAB.res.d.status !== 'optimal') { alert('Run the optimiser first.'); return; }
  LAB.rows = LAB.rows.filter(r => labNum(r.pct) > 0);
  labSetMode('manual');
}

function labList(items, cls) { return items.length ? `<ul class="lab-list ${cls || ''}">${items.map(x => `<li>${labEsc(x)}</li>`).join('')}</ul>` : ''; }

function labRenderResult() {
  const msg = $l('lab-messages'), sum = $l('lab-summary'); const R = LAB.res;
  sum.innerHTML = '';
  if (!R) { msg.innerHTML = '<span class="lab-small">Results appear here.</span>'; return; }
  const d = R.d;
  if (R.kind === 'error') { msg.innerHTML = `<div class="lab-banner fail">${labEsc(d.message || 'Something went wrong.')}</div>`; return; }
  if (R.kind === 'verify') {
    const cls = d.status; const word = { pass: 'PASS - meets every required limit', fail: 'FAIL - blocked from release', incomplete: 'INCOMPLETE - cannot be verified (data missing)' }[d.status] || d.status;
    msg.innerHTML = `<div class="lab-banner ${cls}">${labEsc(word)}</div>${labList(d.issues, 'lab-fail')}${labList(d.warnings, 'lab-warn')}`;
    const batch = labNum($l('lab-batch').value) || 0;
    sum.innerHTML = `<b>Total:</b> ${labFmt(d.total_pct, 3)}% &nbsp; <b>Cost:</b> KSh ${labFmt(d.cost_per_kg, 2)}/kg &nbsp; <b>Batch (${batch} kg):</b> KSh ${labFmt(d.cost_per_kg * batch, 0)}`;
    return;
  }
  // optimise
  if (d.status === 'optimal') {
    const ok = d.rounded_ok;
    msg.innerHTML = `<div class="lab-banner optimal">Least-cost recipe found: KSh ${labFmt(d.cost_per_kg, 2)}/kg &nbsp; (batch KSh ${labFmt(d.batch_cost, 0)})</div>` +
      (d.excluded && d.excluded.length ? `<div class="lab-warn">Excluded for missing data: ${labEsc(d.excluded.join(', '))}</div>` : '') +
      (ok ? `<div class="lab-small">Re-checked independently, and still passes after weighing to ${d.precision_kg} kg (KSh ${labFmt(d.rounded_cost_per_kg, 2)}/kg).</div>`
          : `<div class="lab-banner fail">The recipe passes exactly but FAILS after rounding to ${d.precision_kg} kg. Use a finer weighing precision.</div>`) +
      labList(d.warnings || [], 'lab-warn');
    sum.innerHTML = '<span class="lab-small">Open the "Weigh (kg)" column for the batch sheet. Save the formula below to put it under release control.</span>';
  } else if (d.status === 'missing_data') {
    const by = {}; d.missing.forEach(m => { (by[m.ingredient] = by[m.ingredient] || []).push(m.nutrient); });
    msg.innerHTML = `<div class="lab-banner incomplete">Data missing - not guessed as zero</div>` +
      labList(Object.keys(by).map(k => `${k}: no ${[...new Set(by[k])].join(', ')}`), 'lab-warn') +
      '<button class="btn-sm btn-warning" onclick="labOptimize(true)">Exclude those ingredients and optimise</button> <button class="btn-sm btn-info" onclick="labShowData()">Fill in the data</button>';
  } else if (d.status === 'infeasible') {
    msg.innerHTML = `<div class="lab-banner fail">${labEsc(d.message)}</div>${labList(d.diagnosis || [], 'lab-fail')}`;
  } else {
    msg.innerHTML = `<div class="lab-banner fail">${labEsc(d.message || 'The optimiser could not run.')}</div>`;
  }
}

/* ---------------------------------------------------------------- save profile / formula / production */
async function labSaveProfile(asNew) {
  let name = LAB.profileMeta ? LAB.profileMeta.name : '';
  if (asNew || !LAB.profileMeta) { name = prompt('Profile name (e.g. "Layer - production"):', asNew && name ? name + ' (copy)' : name); if (!name) return; }
  let source = LAB.profileMeta ? LAB.profileMeta.source : '';
  if (asNew || !source) { const s = prompt('Where do these limits come from? (standard, nutritionist, table...)', source || ''); if (s === null) return; source = s; }
  const body = { id: asNew ? '' : LAB.profileId, name, species: LAB.profileMeta ? LAB.profileMeta.species : 'other', stage: LAB.profileMeta ? LAB.profileMeta.stage : '', source, notes: LAB.profileMeta ? LAB.profileMeta.notes : '', lines: labPayloadLines(), bounds: LAB.profileMeta ? (LAB.profileMeta.bounds || []) : [] };
  const r = await labPost('/api/nutrition/profiles', body);
  if (!r.ok) { alert(r.d.message || 'Could not save the profile.'); return; }
  LAB.dirty = false; await labInit(); $l('lab-profile').value = r.d.id; await labLoadProfile(r.d.id); labRenderAll();
}

async function labSaveFormula() {
  const out = $l('lab-save-out'); const name = $l('lab-formula-name').value.trim();
  if (!name) { out.innerHTML = '<span class="lab-fail">Give the formula a name.</span>'; return; }
  if (!LAB.profileId) { out.innerHTML = '<span class="lab-fail">Choose and save a requirement profile first. A formula is only released against a saved profile.</span>'; return; }
  if (LAB.dirty) { out.innerHTML = '<span class="lab-fail">The limits on screen differ from the saved profile. Press "Save profile" first: the release check always uses the saved profile.</span>'; return; }
  const recipe = labRecipe();
  if (!recipe.length) { out.innerHTML = '<span class="lab-fail">Enter the inclusions first.</span>'; return; }
  const r = await labPost('/api/nutrition/formulas', { name, profile_id: LAB.profileId, recipe });
  if (!r.ok) { out.innerHTML = `<span class="lab-fail">${labEsc(r.d.message || 'Could not save.')}</span>`; LAB.savedOk = false; return; }
  LAB.savedName = name; LAB.savedOk = !!r.d.releasable;
  out.innerHTML = r.d.releasable ? `<div class="lab-banner pass">Saved and RELEASABLE against "${labEsc(r.d.profile)}".</div>`
    : `<div class="lab-banner fail">Saved, but BLOCKED from production: it does not pass "${labEsc(r.d.profile)}".</div>${labList(r.d.reasons, 'lab-fail')}`;
  labLoadSavedList();
}

async function labUseInProduction() {
  const out = $l('lab-save-out'); const name = $l('lab-formula-name').value.trim();
  if (!LAB.savedOk || LAB.savedName !== name) { out.innerHTML = '<span class="lab-fail">Save the formula first. Production only accepts a formula that passes its saved profile.</span>'; return; }
  await labLoadSavedList();
  const formula = LAB.saved.find(f => f.name === name && f.releasable);
  if (!formula) { out.innerHTML = '<span class="lab-fail">Could not find the released saved formula. Refresh the saved formula list and try again.</span>'; return; }
  const picker = document.getElementById('saved-formula-select');
  if (picker) picker.value = formula.id;
  const batch = labNum($l('lab-batch').value) || 0;
  const batchField = document.getElementById('saved-formula-batch'); if (batchField) batchField.value = batch;
  if (typeof loadSavedFormula === 'function') await loadSavedFormula();
}
async function labLoadSavedList() {
  const r = await labApi('/api/factory/formulas');
  if (!r.ok || !Array.isArray(r.d)) return;
  LAB.saved = r.d;
  $l('lab-load-saved').innerHTML = '<option value="">Load a saved formula...</option>' + r.d.map(f => `<option value="${f.id}">${labEsc(f.name)}</option>`).join('');
}
function labLoadSaved() {
  const f = LAB.saved.find(x => String(x.id) === String($l('lab-load-saved').value));
  if (!f) { alert('Choose a saved formula first.'); return; }
  LAB.rows = []; f.lines.forEach(l => { labAddIngredient(l.ingredient_id); const row = LAB.rows.find(r => r.id === Number(l.ingredient_id)); if (row) row.pct = l.pct; });
  if (LAB.mode === 'optimize') labSetMode('verify');
  $l('lab-formula-name').value = f.name; LAB.res = null;
  labRenderAll(); labVerifySoon(0);
}

/* ---------------------------------------------------------------- panels */
function labTogglePanel(id) { const p = $l(id); p.style.display = p.style.display === 'block' ? 'none' : 'block'; }
async function labLoadAudit() {
  const panel = $l('lab-audit-panel'); panel.style.display = panel.style.display === 'block' ? 'none' : 'block';
  if (panel.style.display !== 'block') return;
  const r = await labApi('/api/nutrition/audit');
  if (!r.ok) { $l('lab-audit-table').innerHTML = `<tr><td>${labEsc(r.d.message)}</td></tr>`; return; }
  $l('lab-audit-table').innerHTML = '<tr><th>Ingredient</th><th>Price/kg</th><th>Price flag</th><th>Missing required nutrient values by profile</th><th>Imported values and source</th></tr>' + r.d.rows.map(row =>
    `<tr><td>${labEsc(row.ingredient)}</td><td>${labFmt(row.price_per_kg,2)}</td><td>${row.missing_price ? 'Missing price' : row.possible_price_outlier ? 'Possible outlier (' + labEsc(row.outlier_method) + ')' : '—'}</td>` +
    `<td>${row.missing_required_nutrients.map(x => labEsc(x.code) + ': ' + x.profiles.map(labEsc).join(', ')).join('<br>') || '—'}</td>` +
    `<td>${row.imported_values.map(x => labEsc(x.code) + ' ' + labFmt(x.value,4) + ' · ' + labEsc(x.source) + ' · ' + labEsc(x.date || '')).join('<br>') || '—'}</td></tr>`).join('');
}
let LAB_PRESETS = [];
async function labLoadPresets() {
  const panel = $l('lab-preset-panel'); panel.style.display = panel.style.display === 'block' ? 'none' : 'block';
  if (panel.style.display !== 'block') return;
  const r = await labApi('/api/nutrition/presets');
  if (!r.ok) { $l('lab-preset-message').textContent = r.d.message || 'Could not load presets.'; return; }
  LAB_PRESETS = r.d; const opts = '<option value="">New preset</option>' + LAB_PRESETS.map(p => `<option value="${p.id}">${labEsc(p.name)} · ${labEsc(p.source)}</option>`).join('');
  $l('lab-preset-select').innerHTML = opts; $l('lab-generate-preset').innerHTML = LAB_PRESETS.map(p => `<option value="${p.id}">${labEsc(p.name)}</option>`).join('');
}
function labPresetPick() {
  const p = LAB_PRESETS.find(x => String(x.id) === $l('lab-preset-select').value);
  const fields = { 'lab-preset-name':p?.name, 'lab-preset-genotype':p?.genotype, 'lab-preset-stage':p?.stage,
    'lab-preset-age-min':p?.age_min_days, 'lab-preset-age-max':p?.age_max_days, 'lab-preset-lys':p?.sid_lys_g_mcal,
    'lab-preset-me-min':p?.me_min_mcal, 'lab-preset-me-max':p?.me_max_mcal, 'lab-preset-source':p?.source };
  Object.entries(fields).forEach(([id,v]) => $l(id).value = v ?? ''); $l('lab-preset-aa').value = p ? JSON.stringify(p.ideal_aa, null, 2) : '{}';
}
async function labSavePreset() {
  let ideal_aa; try { ideal_aa = JSON.parse($l('lab-preset-aa').value || '{}'); } catch (e) { alert('Amino-acid ratio JSON is invalid.'); return; }
  const selected = LAB_PRESETS.find(x => String(x.id) === $l('lab-preset-select').value);
  const body = { id:selected?.id, name:$l('lab-preset-name').value, genotype:$l('lab-preset-genotype').value, stage:$l('lab-preset-stage').value,
    age_min_days:$l('lab-preset-age-min').value, age_max_days:$l('lab-preset-age-max').value, sid_lys_g_mcal:$l('lab-preset-lys').value,
    me_min_mcal:$l('lab-preset-me-min').value, me_max_mcal:$l('lab-preset-me-max').value, source:$l('lab-preset-source').value, ideal_aa };
  const r = await labPost('/api/nutrition/presets', body); $l('lab-preset-message').textContent = r.ok ? 'Preset saved.' : (r.d.message || 'Could not save.');
  if (r.ok) { $l('lab-preset-panel').style.display = 'none'; await labLoadPresets(); $l('lab-preset-select').value = r.d.id; labPresetPick(); }
}
async function labGenerateProfile() {
  const r = await labPost('/api/nutrition/profiles/generate', { preset_id:$l('lab-generate-preset').value,
    target_me_mcal:$l('lab-generate-me').value, age_days:$l('lab-generate-age').value, name:$l('lab-generate-name').value });
  if (!r.ok) { $l('lab-preset-message').textContent = r.d.message || 'Could not generate profile.'; return; }
  $l('lab-preset-message').textContent = `Created editable profile. SID Lys minimum: ${labFmt(r.d.sid_lys_pct,4)}%. Not released.`;
  await labInit(); $l('lab-profile').value = r.d.id; await labLoadProfile(r.d.id); labRenderAll();
}
function labShowData() {
  $l('lab-formulate').style.display = 'none'; $l('lab-data').style.display = 'block';
  if (!LAB.cols) { LAB.cols = new Set(['DM', 'CP']); }
  LAB.lines.forEach(l => { LAB.cols.add(l.code); if (l.per !== 'asfed' && l.per !== 'dm') LAB.cols.add(l.per); if ((LAB.nut[l.code] || {}).basis === 'pct_cp') LAB.cols.add('CP'); });
  labRenderCols(); labRenderMatrix();
}
function labHideData() { $l('lab-data').style.display = 'none'; $l('lab-formulate').style.display = 'block'; labRenderAll(); labVerifySoon(0); }

/* ---------------------------------------------------------------- ingredient data grid */
function labNeeded() {
  const need = new Set();
  LAB.lines.filter(l => l.enabled !== false && (l.min !== null || l.max !== null)).forEach(l => {
    need.add(l.code); if (l.per === 'dm') need.add('DM'); else if (l.per !== 'asfed') need.add(l.per);
    [l.code, l.per].forEach(c => { const b = (LAB.nut[c] || {}).basis; if (b === 'dm') need.add('DM'); if (b === 'pct_cp') need.add('CP'); });
  });
  return need;
}
function labRenderCols() {
  const groups = {}; LAB.nutrients.forEach(n => { (groups[n.group] = groups[n.group] || []).push(n); });
  $l('lab-col-pick').innerHTML = Object.keys(groups).map(g => `<div><b class="lab-small">${labEsc(g)}:</b> ` + groups[g].map(n =>
    `<label style="margin-right:8px;font-size:.82rem"><input type="checkbox" ${LAB.cols.has(n.code) ? 'checked' : ''} onchange="labToggleCol('${n.code}',this.checked)"> ${labEsc(n.name)}</label>`).join('') + '</div>').join('');
}
function labShowSidCols() { LAB_SID.forEach(c => LAB.cols.add(c)); LAB.cols.add('CP'); labRenderCols(); labRenderMatrix(); }
function labToggleCol(code, on) { if (on) LAB.cols.add(code); else LAB.cols.delete(code); labRenderMatrix(); }
function labRenderMatrix() {
  const cols = LAB.nutrients.filter(n => LAB.cols.has(n.code)); const need = labNeeded();
  const canClear = window.currentRole === 'admin';
  const head = '<tr><th>Ingredient</th>' + cols.map(n => `<th title="${labEsc(n.name)} (${labEsc(n.unit)}${n.basis !== 'asfed' ? ', ' + n.basis : ''})">${labEsc(n.code)}<br><span class="lab-small">${labEsc(n.unit)}</span></th>`).join('') + (canClear ? '<th>Actions</th>' : '') + '</tr>';
  const body = LAB.ings.map(i => '<tr><td>' + labEsc(i.name) + '</td>' + cols.map(n => {
    const v = i.values[n.code]; const key = i.id + '|' + n.code; const ch = LAB.changed[key];
    const val = ch !== undefined ? ch : (v ? v.value : '');
    const gap = (val === '' || val === null) && need.has(n.code);
    return `<td><input class="lab-n ${gap ? 'lab-gap' : ''}" type="number" step="any" min="0" value="${labEsc(val)}" title="${labEsc(v ? v.source : (gap ? 'needed but unknown' : 'unknown'))}" oninput="labCellEdit(${i.id},'${n.code}',this)"></td>`;
  }).join('') + (canClear ? `<td><button type="button" class="btn-sm btn-danger" onclick="labClearIngredient(${i.id})">Clear values</button></td>` : '') + '</tr>').join('');
  $l('lab-matrix').innerHTML = head + body;
}
function labCellEdit(id, code, el) { LAB.changed[id + '|' + code] = el.value; el.style.outline = '2px solid #fd7e14'; }
async function labClearIngredient(id) {
  const ingredient = LAB.ings.find(i => Number(i.id) === Number(id));
  if (!ingredient || !confirm(`Clear all nutrient values for ${ingredient.name}? The ingredient, stock, and sales history will be kept.`)) return;
  const r = await labApi('/api/nutrition/ingredients/' + id + '/values', { method: 'DELETE' });
  if (!r.ok) { alert(r.d.message || 'Could not clear nutrient values.'); return; }
  Object.keys(LAB.changed).forEach(k => { if (k.startsWith(id + '|')) delete LAB.changed[k]; });
  $l('lab-cell-msg').textContent = `Cleared ${r.d.cleared || 0} saved nutrient value(s) for ${ingredient.name}.`;
  await labInit(); LAB.res = null; labRenderMatrix();
}
async function labSaveCells() {
  const cells = Object.keys(LAB.changed).map(k => { const [id, code] = k.split('|'); return { ingredient_id: Number(id), code, value: LAB.changed[k] === '' ? null : Number(LAB.changed[k]), source: $l('lab-cell-source').value.trim() }; });
  if (!cells.length) { $l('lab-cell-msg').textContent = 'Nothing changed.'; return; }
  const r = await labPost('/api/nutrition/ingredient-values', { cells });
  $l('lab-cell-msg').textContent = r.ok ? 'Saved ' + r.d.saved + ' value(s).' : (r.d.message || 'Could not save.');
  if (r.ok) { LAB.changed = {}; await labInit(); LAB.res = null; labRenderMatrix(); }
}
async function labAddNutrient() {
  const body = { code: $l('lab-nn-code').value, name: $l('lab-nn-name').value, unit: $l('lab-nn-unit').value, basis: $l('lab-nn-basis').value };
  const r = await labPost('/api/nutrition/nutrients', body);
  if (!r.ok) { alert(r.d.message || 'Could not add the nutrient.'); return; }
  LAB.cols.add(r.d.code); await labInit(); labRenderCols(); labRenderMatrix();
}

/* ---------------------------------------------------------------- Excel / paste import */
function labImportBody(textId, fileId, commit, extra) {
  const f = $l(fileId).files[0];
  if (f) { const fd = new FormData(); fd.append('file', f); fd.append('commit', commit ? 'true' : 'false'); Object.keys(extra || {}).forEach(k => fd.append(k, typeof extra[k] === 'object' ? JSON.stringify(extra[k]) : extra[k])); return { method: 'POST', body: fd }; }
  return { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(Object.assign({ text: $l(textId).value, commit }, extra || {})) };
}
async function labImport(commit) {
  const out = $l('lab-imp-out');
  if (!$l('lab-imp-file').files[0] && !$l('lab-imp-text').value.trim()) { out.innerHTML = '<span class="lab-fail">Paste cells from Excel or choose a file first.</span>'; return; }
  const r = await labApi('/api/nutrition/import/ingredients', labImportBody('lab-imp-text', 'lab-imp-file', commit, { overrides: LAB.overrides, create_missing: $l('lab-imp-create').checked ? 'true' : 'false', apply_max: $l('lab-imp-max').checked ? 'true' : 'false', new_category: $l('lab-imp-cat').value }));
  if (!r.ok) { out.innerHTML = `<span class="lab-fail">${labEsc(r.d.message || 'Import failed.')}</span>`; return; }
  const d = r.d; const opts = LAB.ings.map(i => `<option value="${i.id}">${labEsc(i.name)}</option>`).join('');
  const rows = d.rows.map(x => {
    let who = x.ingredient ? labEsc(x.ingredient) : (x.status === 'new' ? '<b>will be created</b>' : '<span class="lab-fail">no match</span>');
    if (x.status !== 'ok' && x.status !== 'new') who += ` <select onchange="LAB.overrides['${x.row}']=this.value"><option value="">pick...</option>${opts}</select>`;
    return `<tr><td>${labEsc(x.name)}</td><td>${who}</td><td>${x.cells.length} value(s)</td><td class="lab-fail">${labEsc(x.errors.join('; '))}</td></tr>`;
  }).join('');
  out.innerHTML = (commit ? `<div class="lab-banner pass">Imported ${d.written} value(s).${d.created ? ' Created ' + d.created + ' new ingredient(s).' : ''}${d.max_updated ? ' Set max inclusion on ' + d.max_updated + '.' : ''}${d.skipped_rows.length ? ' ' + d.skipped_rows.length + ' row(s) skipped: pick the ingredient in the list and import again.' : ''}</div>` : '') +
    (d.transposed ? '<div class="lab-small">Your sheet had nutrients down the side, so it was flipped.</div>' : '') +
    `<div class="lab-small">Columns read: ${labEsc(d.columns.join(', ') || 'none')}${d.ignored_columns.length ? ' &nbsp; Ignored: ' + labEsc(d.ignored_columns.join(', ')) : ''}</div>` +
    labList(d.unknown_columns, 'lab-warn') +
    `<div class="lab-wrap"><table class="lab"><tr><th>Sheet row</th><th>Ingredient</th><th>Values</th><th>Problems</th></tr>${rows}</table></div><div class="lab-small">${labEsc(d.note)}</div>`;
  if (commit) { LAB.overrides = {}; await labInit(); labRenderMatrix(); }
}

async function labReqImport(commit) {
  const out = $l('lab-req-out');
  if (!$l('lab-req-file').files[0] && !$l('lab-req-text').value.trim()) { out.innerHTML = '<span class="lab-fail">Paste cells from Excel or choose a file first.</span>'; return; }
  const r = await labApi('/api/nutrition/import/requirements', labImportBody('lab-req-text', 'lab-req-file', commit, { profile_name: $l('lab-req-name').value.trim(), wuffda_spec: $l('lab-req-spec')?.value || 'current' }));
  if (!r.ok) { out.innerHTML = `<span class="lab-fail">${labEsc(r.d.message || 'Import failed.')}</span>${labList(r.d.problems || [], 'lab-warn')}`; return; }
  const d = r.d;
  if (commit) {
    out.innerHTML = `<div class="lab-banner pass">Imported ${d.lines} limits.</div>${labList(d.problems, 'lab-warn')}`;
    await labInit(); $l('lab-profile').value = d.id; await labLoadProfile(d.id); labRenderAll(); return;
  }
  const rows = d.lines.map(l => `<tr><td>${labEsc(l.label)}</td><td>${labEsc(l.code)}${l.per !== 'asfed' ? ' / ' + labEsc(l.per) : ''}</td><td>${l.min === null ? '-' : l.min}</td><td>${l.max === null ? '-' : l.max}</td><td class="lab-small">${labEsc(l.notes.join('; '))}</td></tr>`).join('');
  const specWrap = $l('lab-req-spec-wrap');
  if (d.wuffda_specs?.length) {
    $l('lab-req-spec').innerHTML = d.wuffda_specs.map(s => `<option value="${labEsc(s.key)}" ${s.key === d.selected_wuffda_spec ? 'selected' : ''}>${labEsc(s.name)}</option>`).join('');
    specWrap.style.display = 'block';
  } else specWrap.style.display = 'none';
  out.innerHTML = `<div class="lab-wrap"><table class="lab"><tr><th>Row</th><th>Read as</th><th>Min</th><th>Max</th><th>Notes</th></tr>${rows}</table></div>${labList(d.problems, 'lab-warn')}<div class="lab-small">Name the profile and press "Import as profile" to keep these.</div>`;
}

document.addEventListener('DOMContentLoaded', () => { const b = $l('lab-batch'); if (b) b.addEventListener('input', () => { if (LAB.ready) labRenderRows(); }); });
