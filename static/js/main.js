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

// Aggressively load all module dropdowns on page boot
document.addEventListener('DOMContentLoaded', () => {
    if(typeof loadCustomers === 'function') loadCustomers();
    if(typeof searchProducts === 'function') searchProducts();
    if(typeof loadPODropdown === 'function') loadPODropdown();
    if(typeof loadFactoryDropdowns === 'function') loadFactoryDropdowns();
});