const orderLines=document.getElementById('lines');
const partChoices=new Map([...document.querySelectorAll('#parts option')].map(option=>[option.value,Number(option.dataset.orderMultiple)||1]));
function showOrderRule(row){
 const sku=row.querySelector('input[name="sku"]');
 const qty=row.querySelector('input[name="quantity"]');
 const multiple=partChoices.get(sku.value.trim())||1;
 qty.min=String(multiple);qty.step=String(multiple);
 let hint=row.querySelector('.order-increment');
 if(!hint){hint=document.createElement('small');hint.className='order-increment';hint.setAttribute('aria-live','polite');qty.parentElement.append(hint);}
 hint.textContent=multiple>1?`Order in multiples of ${multiple} EA (${multiple}, ${multiple*2}, ${multiple*3}…).`:'Whole EA quantities.';
 const value=Number(qty.value);
 qty.setCustomValidity(qty.value && multiple>1 && value%multiple!==0?`Order ${sku.value} in multiples of ${multiple} EA.`:'');
}
document.querySelectorAll('.order-line').forEach(showOrderRule);
orderLines?.addEventListener('input',event=>{const row=event.target.closest('.order-line');if(row)showOrderRule(row);});
document.getElementById('add-line')?.addEventListener('click',()=>{
 const row=document.querySelector('.order-line').cloneNode(true);
 row.querySelectorAll('input').forEach(input=>{if(input.name==='sku'||input.name==='quantity')input.value='';});
 orderLines.append(row);showOrderRule(row);
});
orderLines?.addEventListener('click',event=>{if(event.target.classList.contains('remove-line')&&document.querySelectorAll('.order-line').length>1)event.target.closest('.order-line').remove();});
