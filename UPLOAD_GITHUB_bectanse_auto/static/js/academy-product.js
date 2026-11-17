(()=>{
 const screen=document.body.dataset.uiScreen;
 // A direct chapter link opens the native disclosure before moving to its content.
 document.querySelectorAll('[data-course-chapter],[data-course-resume]').forEach(link=>link.addEventListener('click',()=>{
  const href=link.getAttribute('href');if(!href?.startsWith('#'))return;const target=document.querySelector(href);if(!target)return;
  const chapter=target.closest('details.phase');if(chapter)chapter.open=true;
  document.querySelectorAll('[data-course-chapter]').forEach(a=>a.removeAttribute('aria-current'));
  const active=chapter&&document.querySelector('[data-course-chapter][href="#'+chapter.id+'"]');if(active)active.setAttribute('aria-current','step');
 }));
 // Existing real headings become a reading outline; no new routes or content.
 if(document.body.dataset.readingOutline){
  const article=document.querySelector('article.legal,article.article,main');
  if(article){const headings=[...article.querySelectorAll('h2')];if(headings.length>2){
   const nav=document.createElement('nav');nav.className='reading-outline';nav.setAttribute('aria-label','Dans cette page');
   const label=document.createElement('span');label.className='reading-outline-label';label.textContent='Dans cette page';nav.append(label);
   headings.forEach((h,i)=>{if(!h.id)h.id='lecture-'+i;h.style.scrollMarginTop='28px';const a=document.createElement('a');a.href='#'+h.id;a.textContent=h.textContent;nav.append(a)});
   const title=article.querySelector('h1');(title||article.firstElementChild)?.after(nav);
  }}
 }
 // Tables retain their semantics and horizontal inspection, with explicit focus for keyboard users.
 document.querySelectorAll('.gs-table-wrap,.table-wrap').forEach(w=>{w.tabIndex=0;w.setAttribute('role','region');w.setAttribute('aria-label','Tableau défilant horizontalement')});
 // Long profile settings become independent, accessible disclosures. All inputs and handlers remain attached.
 if(screen==='dashboard'){
  document.querySelectorAll('.section-card.robot-section').forEach(section=>{
   const header=section.querySelector(':scope > .section-header'),content=section.querySelector(':scope > .section-body');
   if(!header||!content||section.classList.contains('robot-guide-card'))return;
   const title=header.querySelector('.section-title');if(!title)return;
   const details=document.createElement('details');details.className=section.className+' product-settings-section';details.id=section.id;
   details.open=!!title.id&&['risk-settings','drawdown-settings'].includes(title.id);
   const summary=document.createElement('summary');summary.textContent=title.textContent;summary.id=title.id;details.append(summary);
   [...section.children].forEach(child=>{if(child!==header)details.append(child)});section.replaceWith(details);
  });
  const reveal=()=>{const id=location.hash.slice(1);if(!id)return;const target=document.getElementById(id);const d=target?.closest('details');if(d)d.open=true};
  addEventListener('hashchange',reveal);reveal();
 }
})();
(()=>{
 const root=document.querySelector('.analysis-workbench');
 if(root){root.dataset.view='input';const setView=view=>{root.dataset.view=view;document.querySelectorAll('[data-analysis-view]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.analysisView===view)))};
 document.querySelectorAll('[data-analysis-view]').forEach(b=>b.addEventListener('click',()=>setView(b.dataset.analysisView)));
 const result=document.getElementById('result');if(result)new MutationObserver(()=>{if(getComputedStyle(result).display!=='none')setView('result')}).observe(result,{attributes:true,attributeFilter:['class','style']});
 }
 const search=document.getElementById('resource-search');if(search)search.addEventListener('input',()=>{const q=search.value.trim().toLocaleLowerCase('fr');let count=0;document.querySelectorAll('.video-card,.pdf-card').forEach(card=>{const match=card.textContent.toLocaleLowerCase('fr').includes(q);card.hidden=!match;if(match)count++});document.getElementById('resource-filter-status').textContent=q?count+' ressource'+(count>1?'s':'')+' trouvée'+(count>1?'s':''):''});
 if(document.body.classList.contains('admin-workspace')){const main=document.getElementById('workspace-content');if(!main)return;const headings=[...main.querySelectorAll('h2')].filter(h=>!h.closest('dialog,.section:not(.active)'));if(headings.length>=3){const nav=document.createElement('nav');nav.className='admin-section-index';nav.setAttribute('aria-label','Sections de cet espace');headings.forEach((h,i)=>{if(!h.id)h.id='admin-section-'+i;h.style.scrollMarginTop='100px';const a=document.createElement('a');a.href='#'+h.id;a.textContent=h.textContent;nav.append(a)});main.firstElementChild?.after(nav)}}
})();

// Use the actual window viewport on installed iOS apps as well as Safari.
// CSS dvh can keep Safari's toolbar reservation after returning to a PWA.
(()=>{
 const root=document.documentElement;
 let frame=0;
 function update(){
  frame=0;
  const viewport=window.visualViewport;
  const editing=document.activeElement?.matches('input,textarea,[contenteditable="true"]');
  const keyboard=editing&&viewport&&viewport.scale===1&&window.innerHeight-viewport.height>120;
  const height=keyboard?viewport.height:window.innerHeight;
  root.style.setProperty('--app-viewport-height',Math.round(height)+'px');
  root.style.setProperty('--app-height',Math.round(height)+'px');
  const nav=document.querySelector('.m-univ-nav');
  if(nav&&nav.getBoundingClientRect().height)root.style.setProperty('--app-nav-rendered-height',nav.getBoundingClientRect().height+'px');
 }
 function schedule(){if(!frame)frame=requestAnimationFrame(update)}
 update();window.addEventListener('resize',schedule,{passive:true});
 window.addEventListener('orientationchange',schedule,{passive:true});
 window.addEventListener('pageshow',schedule);
 document.addEventListener('visibilitychange',schedule);
 document.addEventListener('focusin',schedule);document.addEventListener('focusout',schedule);
 window.visualViewport?.addEventListener('resize',schedule,{passive:true});
 const nav=document.querySelector('.m-univ-nav');if(nav&&window.ResizeObserver)new ResizeObserver(schedule).observe(nav);
})();

// Prevent accidental double-tap zoom, while retaining browser accessibility zoom.
(()=>{
 const installed=matchMedia('(display-mode: standalone)').matches||navigator.standalone===true;
 if(!installed)return;
 const meta=document.querySelector('meta[name="viewport"]');
 if(meta)meta.content='width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no, viewport-fit=cover';
 document.addEventListener('gesturestart',event=>event.preventDefault(),{passive:false});
 document.addEventListener('gesturechange',event=>event.preventDefault(),{passive:false});
 document.addEventListener('touchmove',event=>{if(event.touches.length>1)event.preventDefault()},{passive:false});
})();
