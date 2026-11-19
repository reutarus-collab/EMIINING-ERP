const {JSDOM, VirtualConsole} = require('jsdom');
const fs = require('fs');
const R = '/home/claude/emining_live/';
const rd = p => fs.readFileSync(R + p, 'utf8');
const pos = rd('templates/tabs/pos_tab.html');
const till = rd('templates/tabs/till_tab.html');
const catalog = [
  {id:1,name:'Dairy Meal',category:'Finished Feed',priced:true,is_service:false,available_stock_kg:93.0,retail_price_kg:50,packs:[{pack_kg:50,price:2300}]},
  {id:2,name:'Wheat Bran',category:'Raw - Energy',priced:true,is_service:false,available_stock_kg:0,retail_price_kg:40,packs:[]}
];
const stub = `
window.__log = [];
window.matchMedia = q => ({matches:false, media:q, addListener(){}, removeListener(){}, addEventListener(){}, removeEventListener(){}});
window.scrollTo = () => {}; Element.prototype.scrollIntoView = function(){};
window.fetch = async (url) => {
  const u = String(url);
  const ok = (body) => new Response(JSON.stringify(body), {status:200, headers:{'Content-Type':'application/json'}});
  if (u.startsWith('/api/me')) return ok({username:'t', role:'admin', location_id:1});
  if (u.startsWith('/api/locations')) return ok([{id:1,name:'Main Factory'}]);
  if (u.startsWith('/api/till/current')) return ok({session:null, location_name:'Main Factory'});
  if (u.startsWith('/api/products/search')) return ok(${JSON.stringify(catalog)});
  return ok([]);
};
window.showTab = (id) => window.__log.push('showTab:' + id);
window.confirm = () => true; window.alert = (m) => window.__log.push('alert:' + m);
`;
const html = `<!DOCTYPE html><html><body>
<div id="till-host">${till}</div>
${pos}
<script>${stub}</script>
<script>${rd('static/js/offline-store.js')}</script>
<script>${rd('static/js/ui-mobile.js')}</script>
<script>${rd('static/js/pos.js')}</script>
<script>
(async () => {
  const out = []; const t = (name, cond, extra) => out.push((cond ? 'PASS ' : 'FAIL ') + name + (cond ? '' : '  -> ' + extra));
  const $ = id => document.getElementById(id);
  activeLocationId = '1'; window.activeLocationId = '1'; try { await refreshTill(); } catch (e) { out.push('refreshTill threw: ' + e.message.slice(0,120)); } await reloadCatalog(); await new Promise(r => setTimeout(r, 400));
  out.push('DEBUG till-state=' + ($('till-state')||{}).textContent + ' | cache=' + (typeof catalogCache!=='undefined'?catalogCache.length:'n/a') + ' | loadedAt=' + catalogLoadedAt + ' | results=' + ($('search-results')||{}).innerHTML.slice(0,120));
  try {
    // catalogue render
    t('grid renders 2 cards', document.querySelectorAll('.pos-product-card').length === 2, document.querySelectorAll('.pos-product-card').length);
    t('stock shown as whole kg', /93 kg/.test($('search-results').textContent), $('search-results').textContent.slice(0,200));
    const outBtn = document.querySelector('.pack-btn.is-out');
    t('out-of-stock pack is greyed and tappable', !!outBtn, 'none');
    if (outBtn) { outBtn.click(); t('tap out-of-stock shows toast', /Wheat Bran is out of stock/.test(($('pos-toast')||{}).textContent||''), ($('pos-toast')||{}).textContent); }

    // add to cart
    addToCart(1, 50);
    t('cart row rendered', document.querySelectorAll('#cart-body .cart-row').length === 1, $('cart-body').innerHTML.slice(0,200));
    t('cart total 2300', $('cart-total').textContent === '2300.00', $('cart-total').textContent);
    t('sticky total 2300', $('sticky-total').textContent === '2300.00', $('sticky-total').textContent);
    t('in-cart badge on pack', /×1/.test(document.querySelector('.pack-btn.in-cart')?.textContent || ''), 'no in-cart');
    bumpQty(0, 1);
    t('stepper blocks oversell (2 bags = 100 kg > 93 kg in stock)', $('cart-total').textContent === '2300.00' && window.__log.some(m => /Only 93\\.00 kg of Dairy Meal/.test(m)), $('cart-total').textContent + ' ' + window.__log.join('|'));
    bumpQty(0, -1);

    // tenders
    quickTender('MPESA', 2300);
    t('M-Pesa note shows for M-Pesa', $('pos-other-pay').open === true && document.querySelector('.pos-payment-note').hidden === false, 'open=' + $('pos-other-pay').open + ' hidden=' + document.querySelector('.pos-payment-note').hidden);
    t('deduction link visible only inside row', document.querySelector('.fee-toggle').style.display !== 'none', 'hidden');
    document.querySelector('.fee-toggle').click();
    t('deduction field reveals on tap', document.querySelector('.split-row').classList.contains('show-fee'), 'no class');
    quickTender('CASH', 2300);
    t('M-Pesa note hides for cash', document.querySelector('.pos-payment-note').hidden === true, 'visible');

    // pay guard
    window.tillChecked = true; currentTill = null;
    posGoPay();
    t('till closed -> sheet', /Till is closed/.test($('pos-modal').textContent) && $('pos-modal').hidden === false, $('pos-modal').textContent);
    posModalClose();
    currentTill = {id: 1};
    posGoPay();
    t('till open -> payment stage', $('pos-tab').dataset.posStage === 'payment', $('pos-tab').dataset.posStage);
    setPosStage('products');

    // hold / resume
    holdCart();
    t('hold sheet opens', !!$('hold-label'), 'no input');
    $('hold-label').value = 'Mzee Kiprono';
    confirmHoldCart();
    t('cart empty after hold', cart.length === 0, cart.length);
    t('held saved on this phone', heldSales().length === 1 && heldSales()[0].label === 'Mzee Kiprono', JSON.stringify(heldSales()).slice(0,120));
    t('held badge shows', $('pos-held-btn').hidden === false && $('pos-held-btn').textContent === 'Held 1', $('pos-held-btn').textContent);
    showHeldSales();
    t('held list shows label and total', /Mzee Kiprono/.test($('pos-modal').textContent) && /2,300/.test($('pos-modal').textContent), $('pos-modal').textContent.slice(0,200));
    resumeHeld(heldSales()[0].id);
    t('resume restores cart', cart.length === 1 && cart[0].name === 'Dairy Meal', JSON.stringify(cart).slice(0,100));
    t('resume removes held entry', heldSales().length === 0 && $('pos-held-btn').hidden === true, heldSales().length);
    t('cart total after resume 2300', $('cart-total').textContent === '2300.00', $('cart-total').textContent);

    // empty-cart guard and clear
    cancelSale();
    posGoPay();
    t('empty cart -> toast, no stage change', /Cart is empty/.test($('pos-toast').textContent) && $('pos-tab').dataset.posStage === 'products', $('pos-toast').textContent);
    t('legacy ids present', ['btn-hold','btn-restore','cart-body','payment-lines','search-results','customer-select','complete-sale-button'].every(i => !!$(i)), 'missing');
  } catch (e) { out.push('ERROR ' + e.stack); }
  console.log(out.join('\\n'));
})();
</script></body></html>`;
const vc = new VirtualConsole();
vc.on('log', m => console.log(m)); vc.on('jsdomError', e => console.log('JSDOM ERROR: ' + (e.detail && e.detail.stack || e.message).split('\n').slice(0,3).join(' | ')));
new JSDOM(html, {runScripts: 'dangerously', url: 'http://localhost/', virtualConsole: vc, pretendToBeVisual: true, beforeParse(w){ const f = require('fake-indexeddb'); w.indexedDB = f.indexedDB; w.IDBKeyRange = f.IDBKeyRange; w.Response = Response; w.Headers = Headers; w.Request = Request; }});
