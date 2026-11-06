/* Read-only UI helpers for the POS tab. Never calls checkout, sync or stock code. */
(function () {
  var $ = function (id) { return document.getElementById(id); };
  // POS stage navigation only changes which existing panels are shown. It never submits a sale.
  var pos = $('pos-tab'), stageButtons = document.querySelectorAll('.pos-view-tabs [data-pos-stage]');
  var stageNames = ['products', 'cart', 'payment', 'receipt'];
  function stageActionLabel(stage) {
    return {products:'View cart', cart:'Continue to payment', payment:'Review payment', receipt:'New sale'}[stage] || 'View cart';
  }
  window.setPosStage = function (stage) {
    if (!pos || stageNames.indexOf(stage) < 0) return;
    pos.dataset.posStage = stage;
    document.body.classList.toggle('pos-checkout-focus', stage === 'cart' || stage === 'payment' || stage === 'receipt');
    Array.prototype.forEach.call(stageButtons, function (button) {
      var active = button.dataset.posStage === stage;
      button.classList.toggle('is-active', active);
      button.setAttribute('aria-current', active ? 'step' : 'false');
      if (button.dataset.posStage === 'receipt') button.hidden = stage !== 'receipt';
    });
    var go = $('sticky-go'); if (go) go.textContent = stageActionLabel(stage);
  };
  if (pos) window.setPosStage('products');

  var stageGo = $('sticky-go');
  if (stageGo) stageGo.addEventListener('click', function () {
    var stage = pos && pos.dataset.posStage || 'products';
    if (stage === 'products') window.setPosStage('cart');
    else if (stage === 'cart') window.setPosStage('payment');
    else if (stage === 'payment') {
      var submit = $('complete-sale-button');
      if (submit) { submit.scrollIntoView({behavior:'smooth', block:'center'}); submit.focus({preventScroll:true}); }
    } else window.setPosStage('products');
  });

  var shortcutToggle = $('pos-shortcuts-toggle'), shortcutPanel = $('pos-shortcuts-panel');
  if (shortcutToggle && shortcutPanel) shortcutToggle.addEventListener('click', function () {
    var open = shortcutPanel.hidden;
    shortcutPanel.hidden = !open; shortcutToggle.setAttribute('aria-expanded', String(open));
  });
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') {
      if (shortcutPanel && !shortcutPanel.hidden) { shortcutPanel.hidden = true; if (shortcutToggle) shortcutToggle.setAttribute('aria-expanded', 'false'); }
      return;
    }
    if (event.key === 'F2') { event.preventDefault(); window.setPosStage('products'); var search = $('search-input'); if (search) search.focus(); }
    if (event.key === 'F4') { event.preventDefault(); window.setPosStage('cart'); var customer = $('customer-search'); if (customer) customer.focus(); }
    if (event.key === 'F6') { event.preventDefault(); window.setPosStage('cart'); var cartCard = $('pos-cart-card'); if (cartCard) cartCard.scrollIntoView({behavior:'smooth', block:'start'}); }
    if (event.key === 'F8') { event.preventDefault(); window.setPosStage('payment'); var payment = $('checkout-card'); if (payment) payment.scrollIntoView({behavior:'smooth', block:'start'}); }
  });
  var searchInput = $('search-input');
  if (searchInput) searchInput.addEventListener('keydown', function (event) {
    if (event.key === 'Enter' || event.key === 'ArrowDown') {
      var first = document.querySelector('#search-results .pack-btn');
      if (first) { event.preventDefault(); first.focus(); }
    }
  });
  document.addEventListener('keydown', function (event) {
    if (!event.target || !event.target.matches || !event.target.matches('#search-results .pack-btn')) return;
    if (!['ArrowRight','ArrowLeft','ArrowDown','ArrowUp'].includes(event.key)) return;
    var buttons = Array.prototype.slice.call(document.querySelectorAll('#search-results .pack-btn'));
    var current = buttons.indexOf(event.target), cols = window.matchMedia('(max-width:700px)').matches ? 2 : Math.max(1, Math.floor(document.getElementById('search-results').clientWidth / 230));
    var delta = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : event.key === 'ArrowDown' ? cols : -cols;
    if (buttons[current + delta]) { event.preventDefault(); buttons[current + delta].focus(); }
  });

  // Mirror the session context in the desktop sidebar without duplicating API calls.
  function syncSidebar() {
    var user = $('app-sidebar-user'), location = $('app-sidebar-location'), tillMirror = $('till-state-mirror');
    if (user && window.currentUsername) user.textContent = window.currentUsername;
    var select = $('pos-location');
    if (location && select && select.selectedOptions[0]) location.textContent = select.selectedOptions[0].textContent;
    var till = $('till-state'); if (tillMirror && till) tillMirror.textContent = till.textContent;
    var tillLocation = $('till-location-label'); if (tillLocation && select && select.selectedOptions[0]) tillLocation.textContent = select.selectedOptions[0].textContent;
  }
  var tillNode = $('till-state');
  if (tillNode && window.MutationObserver) new MutationObserver(syncSidebar).observe(tillNode, {childList:true, characterData:true, subtree:true});
  var outletSelect = $('pos-location'); if (outletSelect) outletSelect.addEventListener('change', syncSidebar);
  window.addEventListener('load', syncSidebar); setInterval(syncSidebar, 1500);
  // Till panel: open on desktop, collapsed on phones; mirror live till state into the summary.
  var panel = $('till-panel');
  if (panel && window.matchMedia && window.matchMedia('(min-width:701px)').matches) panel.open = true;
  var till = $('till-state'), mirror = $('till-state-mirror');
  function syncTill() { if (till && mirror) mirror.textContent = till.textContent; }
  if (till && mirror && window.MutationObserver) {
    new MutationObserver(syncTill).observe(till, {childList: true, characterData: true, subtree: true});
    syncTill();
  }
  // Sticky bar: mirror the existing total; button only scrolls to the real checkout card.
  var total = $('cart-total'), bar = $('sticky-total');
  function syncTotal() { if (total && bar) bar.textContent = total.textContent; }
  if (total && bar && window.MutationObserver) {
    new MutationObserver(syncTotal).observe(total, {childList: true, characterData: true, subtree: true});
    syncTotal();
  }
})();

/* ---- Till panel: on phones, force it open while the till is closed (selling is blocked without it) ---- */
(function () {
  var till = document.getElementById('till-state'), panel = document.getElementById('till-panel');
  if (!till || !panel || !window.MutationObserver) return;
  function check() {
    if (window.matchMedia('(max-width:700px)').matches && /till closed/i.test(till.textContent)) panel.open = true;
  }
  new MutationObserver(check).observe(till, {childList: true, characterData: true, subtree: true});
  check();
})();

/* ---- Cart steppers: decorate the existing qty input. Buttons dispatch the input's own change event,
        so the existing onchange handler in pos.js does all the work. ---- */
(function () {
  var body = document.getElementById('cart-body');
  if (!body || !window.MutationObserver) return;
  function mk(label, aria, delta, input) {
    var b = document.createElement('button');
    b.type = 'button'; b.className = 'ui-stepbtn'; b.textContent = label; b.setAttribute('aria-label', aria);
    b.addEventListener('click', function () {
      var v = parseFloat(input.value) || 0, n = delta < 0 ? (v > 1 ? v - 1 : v) : v + 1;
      if (n === v) return;
      input.value = String(Math.round(n * 1000) / 1000);
      input.dispatchEvent(new Event('change', {bubbles: true}));
    });
    return b;
  }
  function decorate() {
    body.querySelectorAll('td:nth-child(2) input[type="number"]').forEach(function (inp) {
      if (inp.dataset.uiStep) return;
      if (inp.parentNode.querySelector('button')) { inp.dataset.uiStep = 'native'; return; }
      inp.dataset.uiStep = '1';
      inp.setAttribute('inputmode', 'decimal');   // phone keyboard with a decimal point for .25 kg
      inp.parentNode.insertBefore(mk('−', 'Decrease quantity', -1, inp), inp);
      inp.parentNode.insertBefore(mk('+', 'Increase quantity', 1, inp), inp.nextSibling);
    });
  }
  new MutationObserver(decorate).observe(body, {childList: true, subtree: true});
  decorate();
})();

/* ---- Bottom nav: built from the existing tab buttons the user's role can see ---- */
(function () {
  var PRIMARY = [['dashboard-tab', 'Home', '⌂'], ['pos-tab', 'Sell', '▣'], ['stock-tab', 'Inventory', '▧'], ['report-tab', 'Reports', '▥']];
  function visibleButtons() {
    return Array.prototype.filter.call(document.querySelectorAll('.nav-tabs .tab-btn'), function (b) { return b.style.display !== 'none'; });
  }
  function tabId(b) { var m = /showTab\('([^']+)'\)/.exec(b.getAttribute('onclick') || ''); return m ? m[1] : ''; }
  function build() {
    if (document.querySelector('.ui-bottom-nav')) return;
    var btns = visibleButtons(), byId = {};
    btns.forEach(function (b) { byId[tabId(b)] = b; });
    var nav = document.createElement('nav'); nav.className = 'ui-bottom-nav'; nav.setAttribute('aria-label', 'Main');
    var scrim = document.createElement('div'); scrim.className = 'ui-more-scrim';
    var sheet = document.createElement('div'); sheet.className = 'ui-more-sheet';
    var used = {}, items = [];
    function close() { sheet.classList.remove('open'); scrim.classList.remove('open'); }
    PRIMARY.forEach(function (p) {
      var orig = byId[p[0]]; if (!orig) return;
      used[p[0]] = 1;
      var b = document.createElement('button'); b.type = 'button';
      b.innerHTML = '<span class="ui-nav-icon" aria-hidden="true">' + p[2] + '</span><span>' + p[1] + '</span>';
      b.addEventListener('click', function () { close(); orig.click(); });
      nav.appendChild(b); items.push([b, orig]);
    });
    var rest = btns.filter(function (b) { return !used[tabId(b)]; });
    if (rest.length) {
      var more = document.createElement('button'); more.type = 'button'; more.innerHTML = '<span class="ui-nav-icon" aria-hidden="true">⋯</span><span>More</span>';
      more.addEventListener('click', function () { sheet.classList.add('open'); scrim.classList.add('open'); });
      nav.appendChild(more); items.push([more, null]);
      var lastGroup = '';
      rest.forEach(function (orig) {
        var parentGroup = orig.closest('.nav-group');
        var groupName = parentGroup ? (parentGroup.dataset.group || 'Modules') : 'More modules';
        if (groupName !== lastGroup) {
          var heading = document.createElement('div'); heading.className = 'ui-more-category'; heading.textContent = groupName;
          sheet.appendChild(heading); lastGroup = groupName;
        }
        var b = document.createElement('button'); b.type = 'button'; b.textContent = orig.textContent.trim();
        b.addEventListener('click', function () { close(); orig.click(); });
        sheet.appendChild(b); items.push([b, orig]);
      });
    }
    scrim.addEventListener('click', close);
    document.body.appendChild(scrim); document.body.appendChild(sheet); document.body.appendChild(nav);
    function paint() {
      var moreActive = false;
      items.forEach(function (it) {
        var on = it[1] && it[1].classList.contains('active');
        if (it[1] && on && !used[tabId(it[1])]) moreActive = true;
        if (it[1]) it[0].classList.toggle('is-active', on);
      });
      items.forEach(function (it) { if (!it[1]) it[0].classList.toggle('is-active', moreActive); });
    }
    var bar = document.querySelector('.nav-tabs');
    if (bar && window.MutationObserver) new MutationObserver(paint).observe(bar, {attributes: true, subtree: true, attributeFilter: ['class']});
    paint();
  }
  // Wait until main.js has applied role filtering (it sets window.currentRole).
  var tries = 0, t = setInterval(function () {
    if (window.currentRole || ++tries > 50) { clearInterval(t); build(); }
  }, 100);
})();

/* ---- Wide tables on phones: label every cell from its column header so CSS can show each row as a card.
        Display only. Never touches values, ids, handlers or row order. Re-runs when JS renders new rows. ---- */
(function () {
  var MIN_COLS = 5;                       // tables with fewer columns already fit; leave them alone
  var queued = false;
  function headerLabels(table) {
    var row = table.querySelector('thead tr') || table.querySelector('tr');
    if (!row) return [];
    var labels = [];
    Array.prototype.forEach.call(row.children, function (th) {
      var span = parseInt(th.getAttribute('colspan') || '1', 10) || 1;
      var text = (th.textContent || '').replace(/\s+/g, ' ').trim();
      for (var i = 0; i < span; i++) labels.push(text);
    });
    return labels;
  }
  function run() {
    queued = false;
    var tables = document.querySelectorAll('.tab-content table:not([data-no-stack]), .modal table:not([data-no-stack])');
    Array.prototype.forEach.call(tables, function (table) {
      if (table.closest('.cart-table, #cart-body')) return;            // POS cart has its own mobile layout
      var labels = headerLabels(table);
      if (labels.length < MIN_COLS) { table.classList.remove('m-stack'); return; }
      table.classList.add('m-stack');
      Array.prototype.forEach.call(table.querySelectorAll('tbody tr, tfoot tr'), function (tr) {
        var col = 0;
        Array.prototype.forEach.call(tr.children, function (td) {
          var span = parseInt(td.getAttribute('colspan') || '1', 10) || 1;
          if (span > 1) { td.classList.add('m-wide'); col += span; return; }
          var label = labels[col] || '';
          if (td.getAttribute('data-label') !== label) td.setAttribute('data-label', label);
          if (!label) td.classList.add('m-nolabel');
          col++;
        });
      });
    });
  }
  function queue() { if (!queued) { queued = true; (window.requestAnimationFrame || setTimeout)(run); } }
  if (!window.MutationObserver) return;
  new MutationObserver(queue).observe(document.body, {childList: true, subtree: true});   // childList only, so our own attribute writes cannot loop
  queue();
})();
