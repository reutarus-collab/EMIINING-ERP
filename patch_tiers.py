import os, re, shutil, sys
def load(p):
    return open(p, encoding='utf-8').read()
if os.path.exists('templates/tabs/bagprices_tab.html'):
    sys.exit('Already applied (bagprices_tab.html exists). Nothing changed.')
MODEL = '''class ItemPrice(db.Model):
    __tablename__ = 'item_prices'
    id = db.Column(db.Integer, primary_key=True)
    ingredient_id = db.Column(db.Integer, nullable=False)
    pack_kg = db.Column(db.Float, nullable=False)   # 50, 70 ... (1 kg uses retail_price_per_kg)
    price = db.Column(db.Float, nullable=False)     # price of ONE pack
'''
HELPER = '''def _unit_price(ingredient, pack_kg):
    """Price of ONE pack of the given size. No fallback to cost, no guessing."""
    if abs(pack_kg - 1.0) < 0.0005:
        price = ingredient.retail_price_per_kg or 0.0
        if price <= 0:
            raise Exception(f"'{ingredient.name}' has no per-kg price. An admin must set it under Retail Pricing.")
        return price
    for row in ItemPrice.query.filter_by(ingredient_id=ingredient.id).all():
        if abs(row.pack_kg - pack_kg) < 0.0005 and (row.price or 0) > 0:
            return row.price
    raise Exception(f"'{ingredient.name}' has no price for a {pack_kg:g} kg pack. An admin must set it under Bag Prices.")
'''
PACKS_PRE = (
"    items = items_query.all()\n"
"    packs_by_item = {}\n"
"    for pr in ItemPrice.query.order_by(ItemPrice.pack_kg).all():\n"
"        if (pr.price or 0) > 0:\n"
"            packs_by_item.setdefault(pr.ingredient_id, []).append({'pack_kg': pr.pack_kg, 'price': round(pr.price, 2)})\n"
)
B1_NEW = (
"        'priced': (i.retail_price_per_kg or 0) > 0 or bool(packs_by_item.get(i.id)),\n"
"        'packs': packs_by_item.get(i.id, [])\n"
"    } for i in items])"
)
RETAIL_ROUTES = '''PACK_SIZES = (50, 70)
@retail_bp.route('/api/admin/packs')
@roles_required('admin')
def packs_list():
    by_item = {}
    for r in ItemPrice.query.all():
        by_item.setdefault(r.ingredient_id, {})[int(round(r.pack_kg))] = r.price
    out = []
    for i in FeedIngredient.query.order_by(FeedIngredient.name).all():
        prices = by_item.get(i.id, {})
        out.append({'id': i.id, 'name': i.name,
                    'cost_per_kg': i.cost_per_kg or 0,
                    'kg_price': i.retail_price_per_kg or 0,
                    'p50': prices.get(50), 'p70': prices.get(70)})
    return jsonify(out)
@retail_bp.route('/api/admin/packs', methods=['POST'])
@roles_required('admin')
def packs_save():
    data = request.get_json(silent=True) or {}
    try:
        item = db.session.get(FeedIngredient, int(data.get('id')))
    except (TypeError, ValueError):
        return jsonify(status='error', message='Bad item.'), 400
    if not item:
        return jsonify(status='error', message='Item not found.'), 404
    cost = item.cost_per_kg or 0
    new_prices = {}
    for size in PACK_SIZES:
        raw = data.get('p%d' % size)
        if raw is None or raw == '':
            new_prices[size] = None
            continue
        try:
            price = round(float(raw), 2)
        except (TypeError, ValueError):
            return jsonify(status='error', message='Invalid %d kg price.' % size), 400
        if not math.isfinite(price) or price <= 0:
            return jsonify(status='error', message='%d kg price must be above 0.' % size), 400
        if price <= cost * size:
            return jsonify(status='error',
                           message='%d kg price %.2f is not above its cost %.2f (%.2f per kg).' % (size, price, cost * size, cost)), 400
        new_prices[size] = price
    for size, price in new_prices.items():
        row = None
        for r in ItemPrice.query.filter_by(ingredient_id=item.id).all():
            if abs(r.pack_kg - size) < 0.0005:
                row = r
        if price is None:
            if row:
                db.session.delete(row)
        elif row:
            row.price = price
        else:
            db.session.add(ItemPrice(ingredient_id=item.id, pack_kg=float(size), price=price))
    db.session.commit()
    return jsonify(status='success')
'''
NEW_ADD = '''function buildPackOptions(item) {
  const out = [];
  if ((item.retail_price_kg || 0) > 0) out.push({ pack_kg: 1, price: item.retail_price_kg, label: 'KG' });
  (item.packs || []).forEach(p => out.push({ pack_kg: p.pack_kg, price: p.price, label: p.pack_kg + ' kg bag' }));
  return out;
}
function addToCart(itemId) {
  const item = searchResultsCache.find(i => i.id === itemId);
  if (!item) return;
  const options = buildPackOptions(item);
  if (options.length === 0) return alert('This item has no price set.');
  const first = options[0];
  const existing = cart.find(c => c.id === item.id && c.bag_size_kg === first.pack_kg);
  if (existing) {
      existing.qty += 1;
  } else {
      cart.push({ id: item.id, name: item.name, options: options, bag_size_kg: first.pack_kg,
                  unit_type: first.pack_kg === 1 ? 'KG' : 'BAG', unit_price: first.price, qty: 1 });
  }
  renderCart();
}
function updateCartUnit(idx, packKg) {
  const line = cart[idx];
  const opt = line.options.find(o => o.pack_kg === parseFloat(packKg));
  if (!opt) return;
  line.bag_size_kg = opt.pack_kg;
  line.unit_type = opt.pack_kg === 1 ? 'KG' : 'BAG';
  line.unit_price = opt.price;
  renderCart();
}
'''
NEW_RENDER = '''function renderCart() {
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
    const lineTotal = c.qty * c.unit_price;
    subtotal += lineTotal;
    const opts = c.options.map(o =>
      `<option value="${o.pack_kg}" ${o.pack_kg === c.bag_size_kg ? 'selected' : ''}>${escHtml(o.label)}</option>`).join('');
    tbody.innerHTML += `<tr>
      <td><b>${escHtml(c.name)}</b></td>
      <td><input type="number" value="${c.qty}" step="any" min="0.1" onchange="cart[${idx}].qty=Math.max(0.1, parseFloat(this.value)); renderCart()" style="width: 80px; text-align: center; padding: 10px; font-size: 16px; height: 50px;" /></td>
      <td>
        <select onchange="updateCartUnit(${idx}, this.value)" style="height: 50px; font-size: 16px; width: 100%; min-width: 120px;">${opts}</select>
      </td>
      <td style="font-size: 16px;">${c.unit_price.toFixed(2)}</td>
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
'''
TAB = '''<div id="bags-tab" class="tab-content">
  <div class="card">
    <h3>Bag Prices (admin only)</h3>
    <p style="font-size:.9rem;color:#666">Enter the price for ONE bag. Leave blank if the item is not sold in that bag. The per-kg price is set under Retail Pricing. A bag price must be above the cost of the bag.</p>
    <table class="data-table">
      <thead><tr><th>Item</th><th>Cost / kg</th><th>Per-kg price</th><th>50 kg bag</th><th>70 kg bag</th><th>Margin</th><th></th></tr></thead>
      <tbody id="bags-body"><tr><td colspan="7">Loading...</td></tr></tbody>
    </table>
    <div id="bags-msg" style="margin-top:10px"></div>
  </div>
</div>
<script>
function bagMargin(cost, price, size) {
  if (!price || price <= 0) return '';
  return size + ' kg: ' + (((price - cost * size) / price) * 100).toFixed(1) + '%';
}
async function loadBagPrices() {
  const body = document.getElementById('bags-body');
  const r = await fetch('/api/admin/packs', {credentials: 'same-origin'});
  if (!r.ok) { body.innerHTML = '<tr><td colspan="7">Cannot load bag prices.</td></tr>'; return; }
  const rows = await r.json();
  body.innerHTML = rows.map(i =>
    `<tr><td>${retailEsc(i.name)}</td><td>${i.cost_per_kg.toFixed(2)}</td>
     <td>${i.kg_price ? i.kg_price.toFixed(2) : '<span style="color:#b00">none</span>'}</td>
     <td><input type="number" min="0" step="0.01" id="p50-${i.id}" value="${i.p50 || ''}" style="width:110px;padding:6px"></td>
     <td><input type="number" min="0" step="0.01" id="p70-${i.id}" value="${i.p70 || ''}" style="width:110px;padding:6px"></td>
     <td><small>${[bagMargin(i.cost_per_kg, i.p50, 50), bagMargin(i.cost_per_kg, i.p70, 70)].filter(Boolean).join(' | ') || '-'}</small></td>
     <td><button class="btn-sm btn-primary" onclick="saveBagPrices(${i.id})">Save</button></td></tr>`
  ).join('') || '<tr><td colspan="7">No items.</td></tr>';
}
async function saveBagPrices(id) {
  const msg = document.getElementById('bags-msg');
  const payload = {id: id,
                   p50: document.getElementById('p50-' + id).value,
                   p70: document.getElementById('p70-' + id).value};
  const r = await fetch('/api/admin/packs', {
    method: 'POST', credentials: 'same-origin',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload)
  });
  const d = await r.json().catch(() => ({status: 'error', message: 'Server error.'}));
  msg.innerHTML = d.status === 'success'
    ? '<span style="color:green">Saved.</span>'
    : '<span style="color:red">' + retailEsc(d.message) + '</span>';
  if (d.status === 'success') loadBagPrices();
}
</script>
'''
BUTTON = '''  <button class="tab-btn" data-roles="admin" onclick="showTab('bags-tab'); loadBagPrices();">Bag Prices</button>\n'''
fails = []
out = {}
# ---- services/models.py
m = load('services/models.py')
if 'class ItemPrice' in m:
    fails.append('services/models.py: ItemPrice already present')
else:
    out['services/models.py'] = m.rstrip('\n') + '\n\n\n' + MODEL
# ---- services/pos_service.py
s = load('services/pos_service.py')
price_re = re.compile(r"^([ \t]*)price_per_kg = ingredient\.retail_price_per_kg or 0\.0\n[ \t]*if price_per_kg <= 0:\n[ \t]*raise Exception\([^\n]*\)\n[ \t]*subtotal = qty \* \(price_per_kg \* bag_size_kg\)\n", re.M)
imp_re = re.compile(r'^(from services\.models import .*)$', re.M)
anchor = 'def _num(value, name):\n'
if len(price_re.findall(s)) != 1 or len(imp_re.findall(s)) != 1 or s.count(anchor) != 1:
    fails.append('services/pos_service.py: price block, models import or _num not found exactly once')
else:
    def price_repl(mm):
        i = mm.group(1)
        return (i + "unit_price = _unit_price(ingredient, bag_size_kg)\n" +
                i + "subtotal = qty * unit_price\n" +
                i + "unit_type = 'KG' if abs(bag_size_kg - 1.0) < 0.0005 else ('%g' % bag_size_kg) + 'KG BAG'\n")
    s = price_re.sub(price_repl, s, count=1)
    s = s.replace(anchor, HELPER + anchor, 1)
    s = imp_re.sub(lambda mm: mm.group(1) + ('' if 'ItemPrice' in mm.group(1) else ', ItemPrice'), s, count=1)
    out['services/pos_service.py'] = s
# ---- routes/pos.py
r = load('routes/pos.py')
a1 = "    return jsonify([{\n        'id': i.id, 'name': i.name, 'category': i.category,\n        'available_stock_kg'"
b1 = "        'priced': (i.retail_price_per_kg or 0) > 0\n    } for i in items_query.all()])"
if r.count(a1) != 1 or r.count(b1) != 1:
    fails.append('routes/pos.py: search_products return block not found exactly once')
else:
    r = r.replace(a1, PACKS_PRE + a1, 1).replace(b1, B1_NEW, 1)
    out['routes/pos.py'] = 'from services.models import ItemPrice\n' + r
# ---- routes/retail.py
t = load('routes/retail.py')
imp2 = imp_re.findall(t)
if len(imp2) != 1 or 'def packs_list' in t or 'import math' not in t:
    fails.append('routes/retail.py: models import not found once, math missing, or already patched')
else:
    t = imp_re.sub(lambda mm: mm.group(1) + ('' if 'ItemPrice' in mm.group(1) else ', ItemPrice'), t, count=1)
    out['routes/retail.py'] = t.rstrip('\n') + '\n\n\n' + RETAIL_ROUTES
# ---- static/js/pos.js
j = load('static/js/pos.js')
a = j.find('function addToCart(itemId) {')
b = j.find('function calculateChange() {')
c = j.find('function renderCart() {')
d = j.find('function clearCart() {')
pt = re.findall(r'^[ \t]*const priceText = [^\n]*\n', j, re.M)
if min(a, b, c, d) < 0 or not (a < b <= c < d) or len(pt) != 1 or j.count('c.qty * c.retail_price_kg * c.bag_size_kg') != 1:
    fails.append('static/js/pos.js: cart functions, priceText or subtotal line not found as expected')
else:
    j = j[:a] + NEW_ADD + '\n' + j[b:c] + NEW_RENDER + j[d:]
    j = j.replace('c.qty * c.retail_price_kg * c.bag_size_kg', 'c.qty * c.unit_price')
    def pt_repl(mm):
        i = mm.group(1)
        return (i + "const tiers = [];\n" +
                i + "if ((i.retail_price_kg || 0) > 0) tiers.push('KSh ' + i.retail_price_kg + '/kg');\n" +
                i + "(i.packs || []).forEach(p => tiers.push(p.pack_kg + 'kg bag KSh ' + p.price));\n" +
                i + "const priceText = tiers.length ? tiers.join(' | ') : '<b style=\"color:#b00\">NO PRICE</b>';\n")
    j = re.sub(r'^([ \t]*)const priceText = [^\n]*\n', pt_repl, j, count=1, flags=re.M)
    j = j.replace(' | Bag: ${i.bag_size_kg}kg', '')
    out['static/js/pos.js'] = j
# ---- templates/dashboard.html
dd = load('templates/dashboard.html')
bm = list(re.finditer(r"^.*showTab\('pricing-tab'\).*\n", dd, re.M))
inc = '{% include "tabs/retail_tab.html" %}'
if len(bm) != 1 or dd.count(inc) != 1:
    fails.append('templates/dashboard.html: pricing button or retail include not found exactly once')
else:
    dd = dd.replace(bm[0].group(0), bm[0].group(0) + BUTTON, 1)
    dd = dd.replace(inc, inc + '\n{% include "tabs/bagprices_tab.html" %}', 1)
    dd = dd.replace('/static/js/pos.js?v=9', '/static/js/pos.js?v=10')
    out['templates/dashboard.html'] = dd
if fails:
    print('NOTHING CHANGED. Problems:')
    for f in fails:
        print(' -', f)
    sys.exit(1)
for path, text in out.items():
    shutil.copy(path, path + '.bak')
    open(path, 'w', encoding='utf-8', newline='\n').write(text)
    print('PATCHED', path)
open('templates/tabs/bagprices_tab.html', 'w', encoding='utf-8', newline='\n').write(TAB)
print('CREATED templates/tabs/bagprices_tab.html')
