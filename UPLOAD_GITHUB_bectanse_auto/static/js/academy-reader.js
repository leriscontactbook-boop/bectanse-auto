(()=>{
 const reader=document.getElementById('lesson-reader');
 if(!reader)return;
 const content=document.getElementById('lesson-reader-content'), status=document.getElementById('lesson-reader-status');
 let version=0;
 document.addEventListener('click',async event=>{
  const button=event.target.closest('[data-preview]');if(!button)return;
  const current=++version;reader.dataset.kind=button.dataset.kind;
  document.getElementById('lesson-reader-title').textContent=button.dataset.title;
  document.getElementById('lesson-reader-kind').textContent=button.dataset.kind==='video'?'Vidéo de formation':'Document de formation';
  document.getElementById('lesson-reader-open').href=button.dataset.preview;
  content.replaceChildren();status.textContent='Ouverture de la ressource…';reader.showModal();
  try{
   const response=await fetch(button.dataset.preview+'?format=preview',{headers:{Accept:'application/json'}});
   const data=await response.json();if(!response.ok||!data.ok)throw new Error();
   if(current!==version||!reader.open)return;
   fetch('/api/academy/lesson',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({lesson_key:button.dataset.key,status:'started'})}).catch(()=>{});
   const frame=document.createElement('iframe');frame.src=data.url;frame.title=button.dataset.title;
   frame.allow='fullscreen; picture-in-picture; encrypted-media';frame.allowFullscreen=true;
   frame.addEventListener('load',()=>{if(current===version)status.textContent='';},{once:true});
   content.append(frame);
   status.textContent='Si le lecteur reste indisponible, ouvre la ressource avec le lien ci-dessous';
  }catch{if(current===version)status.textContent='L’aperçu est indisponible pour le moment. Tu peux ouvrir la ressource séparément';}
 });
 reader.querySelector('[data-reader-close]').addEventListener('click',()=>reader.close());
 reader.addEventListener('close',()=>{version++;content.replaceChildren();});
 document.addEventListener('error',event=>{if(event.target.matches?.('.lesson-preview img')){event.target.hidden=true;event.target.parentElement.classList.add('without-thumbnail');}},true);
})();
