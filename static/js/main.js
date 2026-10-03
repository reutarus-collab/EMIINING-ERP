function showTab(tabId) {
    document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
    
    const targetTab = document.getElementById(tabId);
    if(targetTab) targetTab.classList.add('active');
    
    const btn = document.querySelector(`button[onclick="showTab('${tabId}')"]`);
    if(btn) btn.classList.add('active');
  
    // Trigger ledger load if requested
    if (tabId === 'sales-tab' && typeof loadSalesHistory === 'function') loadSalesHistory();
}

document.addEventListener('DOMContentLoaded', async () => {
    let role = 'sales';
    try {
        const r = await fetch('/api/me', { credentials: 'same-origin' });
        if (r.ok) role = (await r.json()).role;
    } catch (e) {}
    window.currentRole = role;

    document.querySelectorAll('[data-roles]').forEach(el => {
        if (!el.dataset.roles.split(',').includes(role)) el.style.display = 'none';
    });

    if (typeof loadCustomers === 'function') loadCustomers();
    if (typeof searchProducts === 'function') searchProducts();

    if (['admin', 'accountant', 'warehouse'].includes(role)) {
        if (typeof loadPODropdown === 'function') loadPODropdown();
        if (typeof loadFactoryDropdowns === 'function') loadFactoryDropdowns();
    }
});