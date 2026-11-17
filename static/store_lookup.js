(() => {
  const root = document.getElementById('store-lookup');
  if (!root) return;
  const search = document.getElementById('store-search');
  const results = document.getElementById('store-search-results');
  const status = document.getElementById('store-search-status');
  const selected = document.getElementById('id_store_location');
  const fields = ['store','recipient','address','city','state','postal_code','shipping_county','shipping_location_type'];
  let timer, controller, sequence = 0;
  function lock(value) { fields.forEach(name => { document.getElementById(`id_${name}`).readOnly = value; }); }
  function cancel() { clearTimeout(timer); sequence++; if (controller) controller.abort(); }
  function choose(store) {
    cancel();
    selected.value = store.id;
    fields.forEach(name => { document.getElementById(`id_${name}`).value = store[name] || ''; });
    lock(true);
    search.value = store.store;
    results.replaceChildren();
    status.textContent = `Selected Family Dollar #${store.store}: ${store.address}, ${store.city}, ${store.state} ${store.postal_code}.`;
  }
  if (selected.value) { lock(true); status.textContent = 'Store selected. Search again to choose another store, or use manual entry.'; }
  document.getElementById('store-manual').addEventListener('click', () => {
    cancel(); selected.value = ''; search.value = ''; results.replaceChildren(); lock(false);
    status.textContent = 'Manual entry: review all delivery fields below. Changes apply to this order only.';
    document.getElementById('id_store').focus();
  });
  search.addEventListener('input', () => {
    cancel(); results.replaceChildren();
    const query = search.value.trim(), token = sequence;
    if (query.length < 2 && !/^\d+$/.test(query)) { status.textContent = 'Enter a store number or at least two search characters. Your selected delivery address remains below.'; return; }
    status.textContent = 'Searching stores…';
    timer = setTimeout(async () => {
      controller = new AbortController();
      try {
        const response = await fetch(`${root.dataset.searchUrl}?q=${encodeURIComponent(query)}`, {signal:controller.signal, credentials:'same-origin',headers:{Accept:'application/json'}});
        if (!response.ok || !response.headers.get('content-type')?.includes('application/json')) throw new Error('Search unavailable');
        const data = await response.json();
        if (token !== sequence) return;
        results.replaceChildren();
        data.results.forEach(store => {
          const button = document.createElement('button'); button.type = 'button'; button.className = 'secondary';
          button.style.cssText = 'text-align:left;white-space:normal;line-height:1.5';
          button.textContent = `#${store.store} — ${store.address}, ${store.city}, ${store.state} ${store.postal_code}`;
          button.addEventListener('click', () => choose(store)); results.append(button);
        });
        status.textContent = data.results.length ? (data.more ? 'Showing the first 20 matches. Add a city, ZIP or store number to narrow the search.' : `${data.results.length} matching store${data.results.length === 1 ? '' : 's'}. Select one to fill delivery details.`) : 'No matching store. Try another search or enter the address manually.';
      } catch (error) {
        if (error.name !== 'AbortError' && token === sequence) status.textContent = 'Store search is unavailable. Refresh to check your login, or enter the address manually.';
      }
    }, 250);
  });
  search.addEventListener('keydown', event => {
    if (event.key === 'Enter') { event.preventDefault(); results.querySelector('button')?.focus(); }
    if (event.key === 'Escape') { cancel(); results.replaceChildren(); status.textContent = 'Search closed. Delivery details remain below.'; }
  });
})();
