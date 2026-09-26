async function runFormulation() {
    const speciesSelect = document.getElementById('species-select');
    if(!speciesSelect) return;
    const res = await fetch('/api/formulate', { 
        method: 'POST', 
        headers: { 'Content-Type': 'application/json' }, 
        body: JSON.stringify({ 
            species: speciesSelect.value, 
            target_batch_kg: document.getElementById('batch-kg').value 
        }) 
    });
    const data = await res.json();
    document.getElementById('formulation-result').textContent = data.recipe;
}
  
async function loadFactoryDropdowns() {
    const res = await fetch('/api/inventory');
    const items = await res.json();
    const millIn = document.getElementById('mill-input-select');
    const millOut = document.getElementById('mill-output-select');
    const prodOut = document.getElementById('prod-output-select');
    
    if (!millIn) return;
    millIn.innerHTML = ''; millOut.innerHTML = ''; prodOut.innerHTML = '';
    
    items.forEach(i => {
        const opt = `<option value="${i.id}">${i.name} (${i.stock_quantity_kg}kg available)</option>`;
        if (i.category.includes('Raw - Energy')) millIn.innerHTML += opt;
        if (i.category.includes('Raw') || i.category.includes('Finished')) millOut.innerHTML += opt;
        if (i.category === 'Finished Feed') prodOut.innerHTML += opt;
    });
}