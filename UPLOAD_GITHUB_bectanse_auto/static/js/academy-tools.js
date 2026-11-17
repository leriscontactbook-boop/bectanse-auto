(()=>{
 const screen=document.body.dataset.uiScreen;
 if(!['simulateur','journal'].includes(screen))return;
 const value=id=>document.getElementById(id).value;
 function toast(message,error){const node=document.getElementById('toast');node.textContent=message||'La demande n’a pas abouti';node.className='gs-toast show'+(error?' error':'');setTimeout(()=>node.className='gs-toast',3500);}
 async function request(button,url,body,method='POST'){
  if(button.disabled)return;button.disabled=true;
  try{const r=await fetch(url,{method,headers:{'Content-Type':'application/json'},...(body?{body:JSON.stringify(body)}:{})});const d=await r.json();if(!r.ok||!d.ok)throw new Error(d.error||'La demande n’a pas abouti');return d;}
  catch(e){toast(e.message==='Failed to fetch'?'Connexion interrompue, réessaie':e.message,true);}
  finally{button.disabled=false;}
 }
 if(screen==='simulateur'){
  document.getElementById('simulate')?.addEventListener('click',async function(){
   const body={market:value('market'),timeframe:value('timeframe'),direction:value('direction'),account_size:value('account'),risk_pct:value('risk'),entry_price:value('entry'),stop_loss:value('stop'),tp1:value('tp1'),tp2:value('tp2'),tp3:value('tp3'),notes:value('notes')};
   if(!body.entry_price||!body.stop_loss){toast('Renseigne une entrée et un niveau d’invalidation',true);return;}
   const d=await request(this,'/api/simulateur',body);if(!d)return;
   document.getElementById('riskAmount').textContent=d.risk_amount+' €';document.getElementById('units').textContent=d.theoretical_units;
   ['rr1','rr2','rr3'].forEach(k=>document.getElementById(k).textContent=d[k]===null?'Sans objectif':d[k]+'R');
   const result=document.getElementById('result');result.style.display='block';result.tabIndex=-1;result.focus({preventScroll:true});result.scrollIntoView({block:'nearest'});toast('Simulation enregistrée');
  });
  document.querySelectorAll('.outcome').forEach(s=>{let previous=s.value;s.addEventListener('change',async()=>{const d=await request(s,'/api/simulateur/'+s.dataset.id+'/outcome',{outcome:s.value});if(d){previous=s.value;toast('Résultat enregistré');}else s.value=previous;});});
 }else{
  document.getElementById('save')?.addEventListener('click',async function(){
   const body={market:value('market'),timeframe:value('timeframe'),direction:value('direction'),setup:value('setup'),context:value('context'),entry_price:value('entry'),stop_loss:value('stop'),tp1:value('tp1'),tp2:value('tp2'),tp3:value('tp3'),risk_pct:value('risk'),result_r:value('resultR'),emotion_before:value('emotionBefore'),emotion_after:value('emotionAfter'),plan_followed:document.getElementById('plan').checked,mistake:value('mistake'),notes:value('notes')};
   const d=await request(this,'/api/journal',body);if(d){toast('Décision enregistrée · Score '+d.trade_score.total);setTimeout(()=>location.reload(),800);}
  });
  document.querySelectorAll('.delete').forEach(b=>b.addEventListener('click',async()=>{if(!confirm('Supprimer cette entrée du journal ?'))return;const d=await request(b,'/api/journal/'+b.dataset.id,null,'DELETE');if(d){document.getElementById('entry-'+b.dataset.id).remove();toast('Entrée supprimée');}}));
 }
})();
