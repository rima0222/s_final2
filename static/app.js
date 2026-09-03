function showTab(name){
 document.querySelectorAll('.tab-panel').forEach(p=>p.classList.toggle('active',p.id===`tab-${name}`));
 document.querySelectorAll('.tab-btn').forEach(b=>b.classList.toggle('active',b.dataset.tab===name));
}
function filterRows(){
 const q=document.getElementById('search').value.trim().toLowerCase();
 document.querySelectorAll('#rows tr').forEach(row=>row.hidden=q&&!row.dataset.search.includes(q));
}
async function refreshStats(){
 try{
  const response=await fetch('/api/stats',{cache:'no-store'});
  if(!response.ok)return;
  const data=await response.json();
  for(const key of ['total_users','active_users','online_users','total_limit_gb','total_used','memory_percent']){
   const el=document.getElementById(key);if(el)el.textContent=data[key]??0;
  }
  for(const [name,item] of Object.entries(data.users||{})){
   const used=document.querySelector(`[data-used="${CSS.escape(name)}"]`);
   if(used)used.textContent=item.used;
   const quota=document.querySelector(`[data-quota="${CSS.escape(name)}"]`);
   if(quota)quota.textContent=item.quota;
   const tcp=document.querySelector(`[data-online-tcp="${CSS.escape(name)}"]`);
   if(tcp){tcp.textContent=item.online_tcp?'● TCP':'○ TCP';tcp.classList.toggle('active',item.online_tcp);}
   const ws=document.querySelector(`[data-online-ws="${CSS.escape(name)}"]`);
   if(ws){ws.textContent=item.online_ws?'● WS':'○ WS';ws.classList.toggle('active',item.online_ws);}
   const days=document.querySelector(`[data-days="${CSS.escape(name)}"]`);
   if(days)days.textContent=`${item.remaining_days} روز`;
   const progress=document.querySelector(`[data-progress="${CSS.escape(name)}"]`);
   if(progress){
    const p=item.limit_bytes>0?Math.min(100,item.used_bytes/item.limit_bytes*100):0;
    progress.style.width=`${p}%`;
   }
  }
 }catch(_){}
}
refreshStats();setInterval(refreshStats,5000);
