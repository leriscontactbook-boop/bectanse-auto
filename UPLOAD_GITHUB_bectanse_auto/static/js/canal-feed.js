
const IS_ADMIN = document.body.dataset.channelAdmin === 'true';
const IS_LOCKED = document.body.dataset.channelLocked === 'true';
let searchQuery = '';
let pendingPublications = 0;
let polling = false;
let allMsgs = [];
let lastId = 0;
let currentFilter = 'all';
let pubType = 'signal';
let pubImgFile = null;
let isAtBottom = true;
let oldestId = 0;
let hasMoreOlder = false;
let loadingOlder = false;

const TYPE_TAG = {
  signal:   ['Signal',   'tag-signal'],
  resultat: ['Résultat', 'tag-resultat'],
  alerte:   ['Alerte',   'tag-alerte'],
  annonce:  ['Annonce',  'tag-annonce'],
  message:  ['',            'tag-message'],
};

function escHtml(s){return String(s||'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function safeMedia(value){try{const u=new URL(value,location.origin);return ['https:','http:'].includes(u.protocol)?escHtml(u.href):'';}catch{return '';}}
function linkify(s){return s.replace(/(https?:\/\/[^\s<]+)/g,'<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>');}
function setConnection(text, failed=false){const el=document.getElementById('channel-connection');el.textContent=text;el.dataset.failed=String(failed);}
function emptyState(title,description,retry=false){return `<div class="channel-empty"><span class="channel-empty-mark" aria-hidden="true">B.</span><h2>${title}</h2><p>${description}</p>${retry?'<button type="button" onclick="loadFeed()">Réessayer</button>':''}</div>`;}
function updateNewButton(){const button=document.getElementById('channel-latest');button.hidden=pendingPublications===0;button.textContent=`${pendingPublications} nouvelle${pendingPublications>1?'s publications':' publication'} `;}
function goToLatest(){isAtBottom=true;pendingPublications=0;updateNewButton();scrollToBottom();}
function searchMessages(value){searchQuery=value.trim().toLocaleLowerCase('fr');isAtBottom=false;buildFeed(allMsgs);document.getElementById('chat-feed').scrollTop=0;refreshReactions();}
function openChannelImage(src){const dialog=document.getElementById('channel-media');const image=dialog.querySelector('img');image.src=src;image.classList.remove('is-zoomed');dialog.querySelector('[data-zoom]').setAttribute('aria-pressed','false');dialog.showModal();}
function formatDay(dt){
  const options={timeZone:'Europe/Paris',year:'numeric',month:'2-digit',day:'2-digit'};
  const key=d=>new Intl.DateTimeFormat('en-CA',options).format(d);
  const date=new Date(dt),today=new Date();
  const parts=new Intl.DateTimeFormat('en-CA',options).formatToParts(today);
  const part=t=>Number(parts.find(p=>p.type===t).value);
  const yesterday=new Date(Date.UTC(part('year'),part('month')-1,part('day')-1,12));
  if(key(date)===key(today))return "Aujourd'hui";
  if(key(date)===key(yesterday))return 'Hier';
  return date.toLocaleDateString('fr-FR',{timeZone:'Europe/Paris',day:'numeric',month:'long',year:'numeric'});
}

function formatTime(dt){
  return new Date(dt).toLocaleTimeString('fr-FR',{hour:'2-digit',minute:'2-digit',timeZone:'Europe/Paris'});
}

function setHistoryLoader(show){
  const h = document.getElementById('history-loader');
  if(!h) return;
  h.style.display = show ? 'block' : 'none';
}

function syncCursors(msgs){
  if(!msgs.length){
    return;
  }
  const ids = msgs.map(m=>m.id).filter(Boolean);
  if(!ids.length) return;
  lastId = Math.max(lastId, ...ids);
  oldestId = Math.min(oldestId || ids[0], ...ids);
}

function buildFeed(msgs){
  const feed=document.getElementById('chat-feed');
  const filtered=msgs.filter(m=>(currentFilter==='all'||m.msg_type===currentFilter)&&(!searchQuery||String(m.text_content||'').toLocaleLowerCase('fr').includes(searchQuery)));
  if(!filtered.length){feed.innerHTML=emptyState(searchQuery?'Aucun résultat':currentFilter==='all'?'Le canal est prêt':'Aucune publication dans ce filtre',searchQuery?'La recherche porte sur les publications déjà chargées.':'Les publications de l’équipe apparaîtront ici, dans l’ordre de leur envoi.');updateCount(0);return;}
  const anchor=[...feed.querySelectorAll('[data-id]')].find(e=>e.getBoundingClientRect().bottom>feed.getBoundingClientRect().top);
  const anchorId=anchor?.dataset.id,anchorTop=anchor?.getBoundingClientRect().top;
  let lastDay='',html='';
  [...filtered].sort((a,b)=>a.id-b.id).forEach(m=>{
    const day=formatDay(m.sent_at);
    if(day!==lastDay){html+=`<div class="date-sep" data-day="${escHtml(day)}"><time>${escHtml(day)}</time></div>`;lastDay=day;}
    const type=Object.hasOwn(TYPE_TAG,m.msg_type)?m.msg_type:'message';
    const [label,tag]=TYPE_TAG[type];
    const photo=m.photo_url?safeMedia(m.photo_url):'',audio=m.audio_url?safeMedia(m.audio_url):'';
    const image=photo?`<button type="button" class="publication-media" data-media="${photo}" aria-label="Agrandir le graphique ou l’image de cette publication"><img src="${photo}" alt="Graphique ou image joint à la publication" loading="lazy"><span>Agrandir l’image </span></button>`:'';
    const sound=audio?`<div class="publication-audio"><span>Message audio</span><audio controls preload="none" src="${audio}">Écouter le message</audio></div>`:'';
    const admin=IS_ADMIN?`<button type="button" class="publication-delete" data-delete="${Number(m.id)}" aria-label="Supprimer cette publication">Supprimer</button>`:'';
    html+=`<article class="publication publication--${type}" data-id="${Number(m.id)}" data-type="${type}">
      <header class="publication-header"><span class="publication-avatar" aria-hidden="true">B.</span><div><strong>Bectanse Académie</strong><span>${label||'Publication'} · Canal privé</span></div><time datetime="${escHtml(m.sent_at)}">${formatTime(m.sent_at)}</time></header>
      <div class="publication-body">${label?`<span class="publication-kind ${tag}">${label}</span>`:''}${m.text_content?`<div class="publication-text">${linkify(escHtml(m.text_content))}</div>`:''}${image}${sound}</div>
      <div class="publication-reactions" data-reactions="${Number(m.id)}"></div><footer>${admin}</footer></article>`;
  });
  // Keep unchanged nodes, including playing audio and decoded images, across polls.
  const next=document.createElement('template');next.innerHTML=html;
  const existing=new Map([...feed.querySelectorAll('article[data-id]')].map(e=>[e.dataset.id,e]));
  next.content.querySelectorAll('article[data-id]').forEach(e=>{const old=existing.get(e.dataset.id);if(old){e.querySelector('[data-reactions]').replaceWith(old.querySelector('[data-reactions]').cloneNode(true));}if(old&&old.outerHTML===e.outerHTML)e.replaceWith(old);});
  feed.replaceChildren(next.content);updateCount(filtered.length);
  if(isAtBottom)scrollToBottom();else if(anchorId){const kept=feed.querySelector(`[data-id="${anchorId}"]`);if(kept)feed.scrollTop+=kept.getBoundingClientRect().top-anchorTop;}
}

function mergeMessages(existing, incoming){
  const map = new Map();
  [...existing, ...incoming].forEach(m=>{
    if(!m || !m.id) return;
    map.set(m.id, m);
  });
  return [...map.values()].sort((a,b)=>a.id-b.id);
}

function updateCount(n){
  const el = document.getElementById('msg-count');
  if(el) el.textContent = n > 0 ? `${n} message${n>1?'s':''}` : '';
}

function scrollToBottom(){
  const feed = document.getElementById('chat-feed');
  feed.scrollTop = feed.scrollHeight;
}

// Détecter si on est en bas
document.getElementById('chat-feed').addEventListener('scroll', function(){
  const feed = this;
  isAtBottom = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 60;
  if(isAtBottom){pendingPublications=0;updateNewButton();}
  if(feed.scrollTop < 120 && hasMoreOlder){
    loadOlderMessages();
  }
});

// FILTRE
function setFilter(btn, type){
  document.querySelectorAll('.f-btn').forEach(b=>{b.classList.remove('active');b.setAttribute('aria-pressed','false')});
  btn.classList.add('active');btn.setAttribute('aria-pressed','true');
  currentFilter = type;
  buildFeed(allMsgs);refreshReactions();
}

// LOAD
async function loadFeed(){
  if(IS_LOCKED)return;
  setConnection('Connexion…');
  try{
    const r = await fetch('/api/canal/messages?limit=50');
    if(!r.ok) throw new Error('HTTP '+r.status);
    const data = await r.json();
    allMsgs = data.messages || [];
    hasMoreOlder = Boolean(data.has_more_older);
    oldestId = 0;
    lastId = 0;
    syncCursors(allMsgs);
    isAtBottom = true;
    buildFeed(allMsgs);await refreshReactions();setConnection('À jour');
  }catch(e){
    setConnection('Connexion interrompue',true);
    document.getElementById('chat-feed').innerHTML=emptyState('Le canal est momentanément indisponible','Vérifie ta connexion puis réessaie. Aucune publication n’a été supprimée.',true);
  }
}

// CHARGEMENT HISTORIQUE (scroll haut)
async function loadOlderMessages(){
  if(loadingOlder || !hasMoreOlder || !oldestId) return;
  loadingOlder = true;
  setHistoryLoader(true);
  const feed = document.getElementById('chat-feed');
  const beforeHeight = feed.scrollHeight;
  const beforeTop = feed.scrollTop;
  try{
    const r = await fetch(`/api/canal/messages?before=${oldestId}&limit=50`);
    if(!r.ok) throw new Error('HTTP '+r.status);
    const data = await r.json();
    const older = data.messages || [];
    if(older.length>0){
      allMsgs = mergeMessages(allMsgs, older);
      syncCursors(older);
      buildFeed(allMsgs);
      const afterHeight = feed.scrollHeight;
      feed.scrollTop = beforeTop + (afterHeight - beforeHeight);
    }
    hasMoreOlder = Boolean(data.has_more_older);
    await refreshReactions();
  }catch(e){
    hasMoreOlder = hasMoreOlder;
  }finally{
    loadingOlder = false;
    setHistoryLoader(false);
  }
}

// SUPPRIMER MESSAGE (admin)
async function supprimerMsg(msgId, btn){
  if(!confirm('Supprimer ce message du canal et de Telegram ?')) return;
  btn.disabled = true;
  try{
    const r = await fetch(`/api/canal/supprimer/${msgId}`,{method:'POST',credentials:'same-origin'});
    if(r.ok){
      // Retirer de allMsgs localement
      allMsgs = allMsgs.filter(m=>m.id!==msgId);
      buildFeed(allMsgs);
    } else {
      alert('Erreur lors de la suppression');
      btn.disabled=false;
    }
  }catch(e){
    alert('Erreur réseau');
    btn.disabled=false;
  }
}

// POLLING
async function poll(){
  if(IS_LOCKED||polling||document.hidden)return;
  polling=true;
  try{
    const r=await fetch(`/api/canal/messages?after=${lastId}`,{cache:'no-store'});
    if(!r.ok)throw new Error('HTTP '+r.status);
    const data=await r.json(),news=data.messages||[];
    if(news.length){
      const added=news.filter(m=>!allMsgs.some(old=>old.id===m.id)).length;
      if(!isAtBottom){pendingPublications+=added;updateNewButton();}
      allMsgs=mergeMessages(allMsgs,news);syncCursors(news);buildFeed(allMsgs);
    }
    await refreshReactions();
    setConnection('À jour');
  }catch{setConnection('Reconnexion…',true);}finally{polling=false;}
}

// ADMIN — type
function setPubType(btn, type){
  document.querySelectorAll('.pub-type-btn').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  pubType = type;
}

// ADMIN — auto-resize textarea
function autoResize(el){
  el.style.height='44px';
  el.style.height=Math.min(el.scrollHeight,140)+'px';
}

// ADMIN — Entrée pour envoyer
function onKey(e){
  if(e.key==='Enter' && !e.shiftKey){ e.preventDefault(); publier(); }
}

// ADMIN — image
function onImgSelect(input){
  const file = input.files[0];
  if(!file) return;
  pubImgFile = file;
  const reader = new FileReader();
  reader.onload = e=>{
    const img = document.getElementById('pub-preview-img');
    img.src = e.target.result;
    img.style.display = 'block';
  };
  reader.readAsDataURL(file);
}

// ADMIN — publier
async function publier(){
  const text = document.getElementById('pub-text').value.trim();
  if(!text && !pubImgFile) return;
  const btn = document.getElementById('pub-btn');
  btn.disabled = true;
  try{
    const fd = new FormData();
    fd.append('text', text);
    fd.append('msg_type', pubType);
    if(pubImgFile) fd.append('image', pubImgFile);
    const r = await fetch('/api/canal/publier',{method:'POST',credentials:'same-origin',body:fd});
    if(r.ok){
      document.getElementById('pub-text').value='';
      document.getElementById('pub-text').style.height='44px';
      pubImgFile=null;
      const pi = document.getElementById('pub-preview-img');
      pi.src=''; pi.style.display='none';
      document.getElementById('pub-img').value='';
      await loadFeed();
    }
  }catch(e){}
  btn.disabled=false;
}

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

// INIT
if(!IS_LOCKED){loadFeed();setInterval(poll,6000);}
document.getElementById('chat-feed').addEventListener('click',event=>{const media=event.target.closest('[data-media]');if(media)openChannelImage(media.dataset.media);const remove=event.target.closest('[data-delete]');if(remove&&IS_ADMIN)supprimerMsg(Number(remove.dataset.delete),remove);});
document.getElementById('chat-feed').addEventListener('error',event=>{if(event.target.tagName==='IMG'){const button=event.target.closest('.publication-media');if(button){button.querySelector('span').textContent='Image indisponible · essayer de l’ouvrir';event.target.hidden=true;}}},true);
document.getElementById('channel-search').addEventListener('input',event=>searchMessages(event.target.value));
document.getElementById('channel-latest').addEventListener('click',goToLatest);
const mediaDialog=document.getElementById('channel-media');
mediaDialog.querySelector('[data-close]').addEventListener('click',()=>mediaDialog.close());
mediaDialog.querySelector('[data-zoom]').addEventListener('click',event=>{const zoomed=mediaDialog.querySelector('img').classList.toggle('is-zoomed');event.currentTarget.setAttribute('aria-pressed',String(zoomed));});
mediaDialog.addEventListener('close',()=>mediaDialog.querySelector('img').removeAttribute('src'));
if('serviceWorker' in navigator){ navigator.serviceWorker.register('/sw.js').catch(()=>{}); }
