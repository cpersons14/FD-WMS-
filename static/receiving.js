(() => {
  const catalog = new Map(JSON.parse(document.querySelector('#receipt-catalog').textContent).map(p => [p.sku,p]));
  const bins = JSON.parse(document.querySelector('#receipt-bins').textContent);
  const rows = document.querySelector('#receipt-rows');
  function update(row, refresh=false) {
    const get = n => row.querySelector(`[name="${n}"]`);
    const part = catalog.get(get('sku').value.trim());
    if (refresh) { get('case_pack').value = part?.case_pack || ''; get('pallet_cases').value = part?.pallet_cases || ''; }
    const casePack = Number(get('case_pack').value), pallets = Number(get('pallet_cases').value);
    row.querySelector('.part-description').textContent = part?.description || 'Select a known part';
    const changed = part && (String(part.case_pack || '') !== get('case_pack').value || String(part.pallet_cases || '') !== get('pallet_cases').value);
    row.querySelector('.refresh-pack').hidden = !changed;
    row.querySelector('.pack-info').textContent = `${casePack || '—'} EA/case · ${pallets || '—'} cases/pallet${changed ? ' · Pack changed: review current settings' : ''}`;
    const bin = bins.find(b => b.sku === get('sku').value.trim() && b.location === get('location').value.trim());
    row.querySelector('.bin-state').textContent = !bin ? 'Select an existing part/location' : bin.uncounted ? 'Needs count before receiving' : bin.held ? 'ON HOLD — not available for orders' : 'Counted bin';
    const unit = get('unit').value, q = Number(get('quantity').value), loose = Number(get('loose_eaches').value || 0);
    const factor = unit === 'EA' ? 1 : unit === 'CASE' ? casePack : casePack*pallets;
    const valid = part?.unit === 'EA' && get('quantity').value !== '' && Number.isSafeInteger(q) && q >= 0 && Number.isSafeInteger(loose) && loose >= 0 && (unit !== 'EA' || !loose) && factor > 0;
    row.querySelector('.each-total').textContent = valid ? (q*factor+loose).toLocaleString() : 'Check units / pack';
  }
  rows.addEventListener('input', e => { const row=e.target.closest('.receipt-row'); if(row) update(row,e.target.name==='sku'); });
  rows.addEventListener('change', e => { const row=e.target.closest('.receipt-row'); if(row) update(row,e.target.name==='sku'); });
  rows.addEventListener('click', e => {
    if(e.target.matches('.remove-receipt-row')) e.target.closest('tr').remove();
    if(e.target.matches('.refresh-pack')) update(e.target.closest('tr'),true);
  });
  document.querySelector('#add-receipt-row').addEventListener('click', () => {
    rows.append(document.querySelector('#empty-receipt-row').content.cloneNode(true));
    update(rows.lastElementChild); rows.lastElementChild.querySelector('input').focus();
  });
  rows.querySelectorAll('.receipt-row').forEach(row => update(row));
  const picker=document.querySelector('#receipt-files'), zone=document.querySelector('#upload-zone'), list=document.querySelector('#new-documents');
  let files=[], urls=[];
  function showFiles() {
    urls.forEach(URL.revokeObjectURL); urls=[]; list.replaceChildren();
    const dt = new DataTransfer(); files.forEach(f => dt.items.add(f.file)); picker.files=dt.files;
    files.forEach((item,i) => {
      const card=document.createElement('div');card.className='document-card';
      const url=URL.createObjectURL(item.file);urls.push(url);
      const a=document.createElement('a');a.href=url;a.target='_blank';a.rel='noopener';a.textContent=`Preview ${item.file.name}`;
      const label=document.createElement('label'),check=document.createElement('input');check.type='checkbox';check.name='new_email';check.value=String(i);check.checked=item.email;
      check.addEventListener('change',()=>item.email=check.checked);label.append(check,document.createTextNode('Include in customer email'));
      const remove=document.createElement('button');remove.type='button';remove.className='secondary';remove.textContent='Remove';remove.addEventListener('click',()=>{files.splice(i,1);showFiles();});
      card.append(a,label,remove);list.append(card);
    });
  }
  function addFiles(incoming) {
    const accepted=Array.from(incoming);
    const allowed=new Set(['application/pdf','image/jpeg','image/png']);
    const invalid=accepted.some(f=>!allowed.has(f.type)||!(/\.(pdf|jpe?g|png)$/i).test(f.name)||f.size>6*1024*1024);
    if(invalid || files.length+accepted.length>12 || files.reduce((n,x)=>n+x.file.size,0)+accepted.reduce((n,f)=>n+f.size,0)>12*1024*1024) {
      document.querySelector('#upload-error').textContent='Choose PDF, JPG or PNG files: 6 MB each, 12 MB and 12 files total including saved documents.';
      showFiles();return;
    }
    document.querySelector('#upload-error').textContent='';
    files.push(...accepted.map(file=>({file,email:true})));showFiles();
  }
  picker.addEventListener('change',()=>addFiles(picker.files));
  zone.addEventListener('dragover',e=>{e.preventDefault();zone.classList.add('dragging');});
  zone.addEventListener('dragleave',()=>zone.classList.remove('dragging'));
  zone.addEventListener('drop',e=>{e.preventDefault();zone.classList.remove('dragging');addFiles(e.dataTransfer.files);});
})();
