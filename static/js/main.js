function toggleNavGroup(button) {
    const group = button && button.closest('.nav-group');
    if (!group) return;
    const open = group.classList.toggle('is-open');
    button.setAttribute('aria-expanded', String(open));
}

function showTab(tabId) {
    document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
    
    const targetTab = document.getElementById(tabId);
    if(targetTab) targetTab.classList.add('active');
    
    const btn = Array.from(document.querySelectorAll('.nav-tabs .tab-btn')).find(el =>
        (el.getAttribute('onclick') || '').includes(`showTab('${tabId}')`));
    if(btn) btn.classList.add('active');
    const group = btn && btn.closest('.nav-group');
    if (group && !group.classList.contains('is-open')) {
        group.classList.add('is-open');
        const toggle = group.querySelector('.nav-category-toggle');
        if (toggle) toggle.setAttribute('aria-expanded', 'true');
    }

    const pages = {
        'dashboard-tab':['Dashboard','Your branch operations at a glance'],
        'pos-tab':['Quick POS','Fast product search, a clear cart and checkout'],
        'stock-tab':['Inventory','Stock on hand, outlet transfers and adjustments'],
        'po-tab':['Purchasing & GRN','Purchase orders, expected deliveries and receiving'],
        'sales-tab':['Sales history','Receipts, returns and product performance'],
        'customers-tab':['Customers','Customer profiles, balances and credit'],
        'till-tab':['Till management','Cashier sessions and drawer movements'],
        'report-tab':['Reports','Daily sales, expenses and till reconciliation'],
        'insights-tab':['Business insights','Product, customer and discount trends'],
        'matrix-tab':['Formulation Lab','Feed formula design and nutrient analysis'],
        'factory-tab':['Factory','Production batches and milling'],
        'expenses-tab':['Expenses','Outlet costs and payment records'],
        'money-tab':['Money & leaks','Cash walk, financial statements and exposure'],
        'pricing-tab':['Retail pricing','Per kilogram selling prices'],
        'bags-tab':['Bag prices','Configured bag sizes and prices'],
        'payables-tab':['Supplier payables','Supplier balances and payments']
    };
    const page = pages[tabId] || ['Business workspace','EMINING FEEDS ERP'];
    const title = document.getElementById('module-heading-title');
    const subtitle = document.getElementById('module-heading-subtitle');
    const kicker = document.getElementById('module-heading-kicker');
    if (title) title.textContent = page[0];
    if (subtitle) subtitle.textContent = page[1];
    if (kicker) kicker.textContent = 'EMINING FEEDS · OPERATIONS';
  
    // Trigger ledger load if requested
    if (tabId === 'sales-tab' && typeof loadSalesHistory === 'function') loadSalesHistory();
    if (tabId === 'dashboard-tab' && typeof loadDashboard === 'function') loadDashboard();
}

document.addEventListener('DOMContentLoaded', async () => {
    let role = 'sales';
    try {
        const r = await fetch('/api/me', { credentials: 'same-origin' });
        if (r.ok) {
            const me = await r.json();
            role = me.role;
            window.staffCreditLimitMax = me.staff_credit_limit_max;
        }
    } catch (e) {}
    window.currentRole = role;
    showTab('dashboard-tab');
    const limitBox = document.getElementById('nc-limit');
    if (limitBox && window.staffCreditLimitMax != null && !['admin', 'accountant'].includes(role)) {
        limitBox.placeholder = 'Credit limit in KSh, up to ' + Number(window.staffCreditLimitMax).toLocaleString('en-KE') + ' (0 = cash-only)';
        limitBox.max = window.staffCreditLimitMax;
    }

    document.querySelectorAll('[data-roles]').forEach(el => {
        if (!el.dataset.roles.split(',').includes(role)) el.style.display = 'none';
    });
    document.querySelectorAll('.nav-group').forEach(group => {
        const visible = Array.from(group.querySelectorAll('.nav-group-subnav .tab-btn')).some(button => button.style.display !== 'none');
        group.style.display = visible ? '' : 'none';
    });

    if (typeof loadCustomers === 'function') loadCustomers();
    if (typeof loadCategories === 'function') loadCategories();
    if (typeof searchProducts === 'function') searchProducts();

    if (['admin', 'accountant', 'warehouse', 'factory'].includes(role)) {
        if (typeof loadPODropdown === 'function') loadPODropdown();
        if (typeof loadFactoryDropdowns === 'function') loadFactoryDropdowns();
    }
});

// Reload every on-screen item list after something changed stock or the item list
// (new PO, goods received, production, milling, transfer, adjustment, new item).
window.refreshItemViews = async function () {
    const jobs = [];
    const call = (name) => { if (typeof window[name] === 'function') jobs.push(Promise.resolve().then(() => window[name]()).catch(e => console.error(name, e))); };
    call('po_loadInventory'); call('po_loadHistory'); call('loadStock'); call('searchProducts'); call('loadFactoryDropdowns');
    await Promise.all(jobs);
};
