# Run from ~/EMIINING-ERP:  python ui_cleanup_patch.py
#  1. removes Customer Maize Milling from the Factory tab
#  2. removes the Ration Formulator tab
#  3. makes Purchase Orders usable on a phone (modals fit the screen, tables scroll)
#  4. factory user: Purchase Orders + Sales Ledger (no Ration Formulator)
#  5. Cash paid-in / paid-out for ALL users
import sys

def read(p): return open(p, encoding='utf-8').read()
def write(p, s): open(p, 'w', encoding='utf-8').write(s)

if 'po-mobile-css' in read('templates/tabs/po_tab.html'):
    sys.exit('Already applied.')

def rep(path, old, new):
    s = read(path)
    if s.count(old) != 1:
        sys.exit('FAILED: expected text not found exactly once in %s:\n%s' % (path, old[:80]))
    write(path, s.replace(old, new, 1))

# ---- 2. Ration Formulator tab removed, factory tab renamed, Sales Ledger for factory
D = 'templates/dashboard.html'
rep(D, '  <button class="tab-btn" data-roles="admin,accountant,warehouse,factory" onclick="showTab(\'matrix-tab\')">🧮 Ration Formulator</button>\n', '')
rep(D, '{% include "tabs/formulator_tab.html" %}\n', '')
rep(D, 'data-roles="admin,accountant,sales" onclick="showTab(\'sales-tab\')"',
       'data-roles="admin,accountant,sales,factory" onclick="showTab(\'sales-tab\')"')
rep(D, '🏭 Factory & Milling', '🏭 Factory')

# ---- 1. Customer Maize Milling removed from the Factory tab
F = 'templates/tabs/factory_tab.html'
s = read(F)
start_marker = '      <div style="background:#f8f9fa; padding:15px; border-radius:6px;">\n        <h4>1. Customer Maize Milling</h4>'
end_marker = '        <div id="mill-message" role="status" style="margin-top:10px"></div>\n      </div>\n'
a = s.find(start_marker)
b = s.find(end_marker, a)
if a < 0 or b < 0 or s.count(start_marker) != 1:
    sys.exit('FAILED: milling form block not found in ' + F)
s = s[:a] + s[b + len(end_marker):]
c = s.find('<div class="card">\n  <h4>Recent customer milling jobs</h4>')
d = s.find('<div class="card">\n    <h4>Recent production batches and losses</h4>')
if c < 0 or d < 0 or d < c:
    sys.exit('FAILED: milling history card not found in ' + F)
s = s[:c] + s[d:]
write(F, s)
rep(F, '🏭 Factory Milling & Batch Manufacturing', '🏭 Factory Batch Manufacturing')
rep(F, '<h4>2. Finished Feed Batch Production</h4>', '<h4>Finished Feed Batch Production</h4>')
rep(F, 'display: grid; grid-template-columns: 1fr 1fr; gap: 20px;', 'display: grid; grid-template-columns: 1fr; gap: 20px;')

# ---- 4/5. permissions
A = 'routes/auth.py'
# make sure the factory user can reach Purchase Orders (safe if already done)
if 'data-roles="admin,accountant,warehouse" onclick="showTab(\'po-tab\')' in read(D):
    rep(D, 'data-roles="admin,accountant,warehouse" onclick="showTab(\'po-tab\')',
           'data-roles="admin,accountant,warehouse,factory" onclick="showTab(\'po-tab\')')
if "'/api/po', '/api/suppliers',\n                '/api/factory'," not in read(A):
    rep(A, "    'factory': ('/', '/api/me', '/api/inventory', '/api/locations', '/api/products',\n                '/api/factory',",
           "    'factory': ('/', '/api/me', '/api/inventory', '/api/locations', '/api/products',\n                '/api/po', '/api/suppliers',\n                '/api/factory',")
rep(A, "'/api/factory', '/api/stock', '/api/expenses'),",
       "'/api/factory', '/api/stock', '/api/expenses',\n                  '/api/till/current', '/api/till/open', '/api/till/close',\n                  '/api/till/cash-movements'),")
rep(A, "'/api/till/close', '/api/till/cash-movements', '/api/reports/daily'),",
       "'/api/till/close', '/api/till/cash-movements', '/api/reports/daily',\n                '/api/sales-history'),")
rep('routes/pos.py',
    "@pos_bp.route('/api/till/cash-movements', methods=['GET', 'POST'])\n@roles_required('admin', 'accountant', 'sales')",
    "@pos_bp.route('/api/till/cash-movements', methods=['GET', 'POST'])\n@roles_required('admin', 'accountant', 'sales', 'warehouse', 'factory')")
rep('templates/tabs/pos_tab.html',
    '<div class="card" data-roles="admin,accountant,sales">\n    <h4>Cash paid-in / paid-out',
    '<div class="card">\n    <h4>Cash paid-in / paid-out')

# ---- 3. phone layout for Purchase Orders
rep('templates/tabs/po_tab.html',
    '<div id="po-tab" class="tab-content">',
    '''<style id="po-mobile-css">
@media (max-width: 820px) {
  #create-po-modal, #create-item-modal, #receive-grn-modal, #view-po-modal, #create-supplier-modal {
    position: fixed !important; top: 6px !important; left: 6px !important; right: 6px !important;
    width: auto !important; max-width: none !important; transform: none !important;
    max-height: calc(100vh - 12px); overflow-y: auto; -webkit-overflow-scrolling: touch;
    padding: 12px !important; box-sizing: border-box;
  }
  #po-tab table { display: block; max-width: 100%; overflow-x: auto; -webkit-overflow-scrolling: touch; }
  #po-tab input, #po-tab select, #po-tab textarea { max-width: 100%; box-sizing: border-box; }
  #po-tab input.grn-stock-kg { width: 100px !important; }
  #po-tab button { min-height: 44px; }
  #po-tab div[style*="display:flex"], #po-tab div[style*="display: flex"] { flex-wrap: wrap; gap: 8px; }
  #po-tab [style*="grid-template-columns"] { grid-template-columns: 1fr !important; }
  #po-tab .form-group input, #po-tab .form-group select { width: 100%; }
}
</style>
<div id="po-tab" class="tab-content">''')
print('Applied. Reload the web app and hard-refresh the page.')