setInterval(async()=>{
 if(document.hidden)return;
 const status=document.getElementById('refresh-status');
 try{
  const response=await fetch(location.href,{credentials:'same-origin',cache:'no-store'});
  if(!response.ok||response.redirected)throw new Error('Session or connection unavailable');
  const page=new DOMParser().parseFromString(await response.text(),'text/html');
  const table=page.querySelector('.panel.flush');
  if(!table)throw new Error('Inventory unavailable');
  document.querySelector('.panel.flush').replaceWith(table);
  const pagination=page.querySelector('.pagination');
  if(pagination)document.querySelector('.pagination').replaceWith(pagination);
  status.textContent='Updated '+new Date().toLocaleTimeString()+'. Updates every 30 seconds.';
 }catch(error){status.textContent='Updates paused. Refresh or sign in again to confirm availability.';}
},30000);
