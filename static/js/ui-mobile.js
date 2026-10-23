/* Read-only UI helpers for the POS tab. Never calls checkout, sync or stock code. */
(function () {
  var $ = function (id) { return document.getElementById(id); };
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
  var go = $('sticky-go');
  if (go) go.addEventListener('click', function () {
    var c = $('checkout-card');
    if (c) c.scrollIntoView({behavior: 'smooth', block: 'start'});
  });
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
  var PRIMARY = [['pos-tab', 'Sales'], ['stock-tab', 'Stock'], ['sales-tab', 'Ledger'], ['factory-tab', 'Factory']];
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
      var b = document.createElement('button'); b.type = 'button'; b.textContent = p[1];
      b.addEventListener('click', function () { close(); orig.click(); });
      nav.appendChild(b); items.push([b, orig]);
    });
    var rest = btns.filter(function (b) { return !used[tabId(b)]; });
    if (rest.length) {
      var more = document.createElement('button'); more.type = 'button'; more.textContent = 'More';
      more.addEventListener('click', function () { sheet.classList.add('open'); scrim.classList.add('open'); });
      nav.appendChild(more); items.push([more, null]);
      rest.forEach(function (orig) {
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
