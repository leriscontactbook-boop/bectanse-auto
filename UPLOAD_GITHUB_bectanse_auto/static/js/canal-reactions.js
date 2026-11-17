// Reactions are independent of the publication body so audio keeps playing.
let reactionToken = '';
let reactionEmojis = [];
const reactionState = new Map();
const reactionPending = new Set();
function paintReactions(id){
  const host=document.querySelector(`[data-reactions="${id}"]`);
  if(!host || !reactionToken)return;
  const rows=reactionState.get(String(id))||[];
  const buttons=reactionEmojis.map(emoji=>{
    const row=rows.find(r=>r.emoji===emoji), active=Boolean(row?.reacted);
    return `<button type="button" class="reaction" data-reaction="${emoji}" aria-pressed="${active}" aria-label="${active?'Retirer':'Ajouter'} la réaction ${emoji}${row?.count?', '+row.count+(row.count>1?' réactions':' réaction'):''}" ${reactionPending.has(String(id))?'disabled':''}><span>${emoji}</span>${row?.count?`<span class="reaction-count">${Number(row.count)}</span>`:''}</button>`;
  }).join('');
  const markup=`<div class="reaction-options" role="group" aria-label="Réagir à cette publication">${buttons}</div>`;
  if(host.innerHTML!==markup)host.innerHTML=markup;
}
async function refreshReactions(){
  const ids=[...document.querySelectorAll('[data-reactions]')].map(e=>e.dataset.reactions);
  if(!ids.length)return;
  try{
    for(let i=0;i<ids.length;i+=200){
      const r=await fetch('/api/canal/reactions?ids='+ids.slice(i,i+200).join(','),{cache:'no-store'});
      if(!r.ok)throw new Error();
      const data=await r.json();reactionToken=data.csrf;reactionEmojis=data.emojis;
      Object.entries(data.reactions).forEach(([id,rows])=>{if(!reactionPending.has(id)){reactionState.set(id,rows);paintReactions(id);}});
    }
  }catch{document.getElementById('reaction-status').textContent='Les réactions sont momentanément indisponibles';}
}
async function reactToPublication(button){
  const id=button.closest('[data-reactions]').dataset.reactions;
  if(reactionPending.has(id)||!reactionToken)return;
  const emoji=button.dataset.reaction;
  const active=button.getAttribute('aria-pressed')!=='true';
  reactionPending.add(id);paintReactions(id);
  const status=document.getElementById('reaction-status');status.textContent='';
  try{
    const r=await fetch(`/api/canal/messages/${id}/reactions`,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':reactionToken},body:JSON.stringify({emoji,active})});
    if(!r.ok)throw new Error();
    const data=await r.json();reactionState.set(id,data.reactions[id]);
    status.textContent=active?'Réaction ajoutée':'Réaction retirée';
  }catch{status.textContent='La réaction n’a pas été enregistrée, réessaie';}
  finally{reactionPending.delete(id);paintReactions(id);document.querySelector(`[data-reactions="${id}"] [data-reaction="${emoji}"]`)?.focus({preventScroll:true});}
}
document.getElementById('chat-feed').addEventListener('click',event=>{const button=event.target.closest('[data-reaction]');if(button)reactToPublication(button);});
refreshReactions();