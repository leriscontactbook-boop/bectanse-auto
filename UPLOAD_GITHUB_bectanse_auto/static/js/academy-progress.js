(()=>{
 const toast=(message,error=false)=>{const el=document.getElementById('toast');el.textContent=message;el.className='gs-toast show'+(error?' error':'');setTimeout(()=>el.className='gs-toast',3200);};
 async function submit(button,url,body){
  if(button.disabled)return null;button.disabled=true;
  try{const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const data=await r.json();if(!r.ok)throw new Error(data.error||'La demande n’a pas abouti, réessaie');return data;}
  catch(error){toast(error.message||'Connexion interrompue, réessaie',true);return null;}
  finally{button.disabled=false;}
 }
 document.getElementById('save-diagnostic')?.addEventListener('click',async function(){
  const value=id=>document.getElementById(id).value;
  const body={experience:value('experience'),objective:value('objective'),weekly_time:value('weekly'),trading_style:value('style'),confidence:Number(value('confidence'))};
  if(!body.experience||!body.objective||!body.weekly_time||!body.trading_style){toast('Complète les quatre choix pour préparer ton parcours',true);return;}
  const d=await submit(this,'/api/academy/diagnostic',body);if(!d)return;if(d.ok){toast('Parcours créé');setTimeout(()=>location.reload(),700);}else toast(d.error,true);
 });
 document.querySelectorAll('.open-lesson').forEach(a=>a.addEventListener('click',event=>{event.preventDefault();a.closest('.lesson').querySelector('[data-preview]')?.click();}));
 document.querySelectorAll('.complete').forEach(b=>b.addEventListener('click',async()=>{
  const d=await submit(b,'/api/academy/lesson',{lesson_key:b.dataset.key,status:'completed'});if(!d)return;
  if(d.ok){b.textContent='Validée';b.closest('.lesson').classList.add('done');document.getElementById('progress-label').textContent=d.percent+'%';document.getElementById('main-progress').style.width=d.percent+'%';const count=document.getElementById('lesson-count');count.textContent=d.completed+'/'+count.dataset.total;toast('Leçon validée');}else toast(d.error,true);
 }));
 document.querySelectorAll('.quiz-options button').forEach(b=>b.addEventListener('click',async()=>{
  const d=await submit(b,'/api/academy/quiz',{phase_key:b.closest('[data-phase]').dataset.phase,answer:Number(b.dataset.answer)});if(!d)return;toast(d.message||d.error,!d.passed);if(d.passed)setTimeout(()=>location.reload(),700);
 }));
})();
