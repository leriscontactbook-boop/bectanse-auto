/* Shared accessibility and guarded actions for the existing server-backed screens. */
window.adminConfirm = (title, message, options = {}) => new Promise(resolve => {
  if (document.querySelector('dialog[data-admin-confirm][open]')) { resolve(false); return; }
  const dialog = document.createElement('dialog'); dialog.className = 'aw-action-dialog'; dialog.dataset.adminConfirm='true';
  const heading = document.createElement('h2'); heading.id = 'aw-confirm-title'; heading.textContent = title;
  const copy = document.createElement('p'); copy.textContent = message;
  dialog.setAttribute('aria-labelledby', heading.id); dialog.append(heading, copy);
  let input;
  if (options.input) {
    const label = document.createElement('label'); label.textContent = options.input.label;
    input = document.createElement('input'); input.type = options.input.type || 'text'; input.required = true;
    input.value = options.input.value || ''; input.minLength = options.input.minLength || 1;
    input.style.cssText = 'display:block;width:100%;padding:11px;margin:8px 0 16px;border:1px solid #ded8eb;border-radius:8px;font:inherit';
    label.append(input); dialog.append(label);
  }
  const actions = document.createElement('div'); actions.className = 'aw-confirm-actions';
  const cancel = document.createElement('button'); cancel.type='button';cancel.className='btn aw-secondary';cancel.textContent='Annuler';
  const accept = document.createElement('button'); accept.type='button';accept.className='btn '+(options.danger?'btn-red':'btn-purple');accept.textContent=options.label||'Confirmer';
  let result = false;
  cancel.onclick=()=>dialog.close();
  accept.onclick=()=>{if(input&&!input.reportValidity())return;result=input?input.value:true;dialog.close();};
  actions.append(cancel,accept);dialog.append(actions);document.body.append(dialog);
  dialog.addEventListener('close',()=>{dialog.remove();resolve(result);},{once:true});dialog.showModal();
  (input||cancel).focus();
});
(() => {
  // Associate the legacy field labels with their actual inputs, including dynamic profiles.
  let fieldId = 0;
  const labelFields = root => root.querySelectorAll('.field').forEach(field => {
    const label = field.querySelector('label'), input = field.querySelector('input:not([type=hidden]),select,textarea');
    if (!label || !input || label.htmlFor) return;
    input.id ||= 'admin-field-'+(++fieldId); label.htmlFor = input.id;
  });
  labelFields(document);
  const content = document.getElementById('profil-content');
  if (content) new MutationObserver(()=>labelFields(content)).observe(content,{childList:true});
  // Retain the existing modal IDs and APIs while providing native focus trapping and Escape.
  document.querySelectorAll('.modal-overlay').forEach(old => {
    const dialog = document.createElement('dialog');
    for (const attr of old.attributes) dialog.setAttribute(attr.name,attr.value);
    while(old.firstChild)dialog.append(old.firstChild);old.replaceWith(dialog);
    const title = dialog.querySelector('.modal-title');
    if(title){title.id ||= dialog.id+'-title';dialog.setAttribute('aria-labelledby',title.id);}
    if(dialog.classList.contains('open'))dialog.showModal();
    new MutationObserver(()=>{
      if(dialog.classList.contains('open')&&!dialog.open)dialog.showModal();
      if(!dialog.classList.contains('open')&&dialog.open)dialog.close();
    }).observe(dialog,{attributes:true,attributeFilter:['class']});
    dialog.addEventListener('close',()=>{dialog.classList.remove('open');if(dialog.id==='profil-modal'&&typeof fermerProfilAdmin==='function')fermerProfilAdmin();});
  });
  if (!document.body.classList.contains('admin-panel-workspace')) return;
  // One operation at a time per action, with network errors made visible.
  const mutations = ['ajusterJours','changerSuspension','msgMembre','supprimerMembre','envoyerNotifGlobale','envoyerNotifIndividuelle','ajouterAnnonce','supprimerAnnonce','envoyerTelegram','ajouterVideo','ajouterPDF','supprimerPDF','supprimerCanalMsg','restaurerCanalMsg','sauverProfilAdmin'];
  for(const name of mutations){
    const original=window[name];if(typeof original!=='function')continue;let busy=false;
    window[name]=async function(...args){
      if(busy)return;busy=true;
      const buttons=[...document.querySelectorAll('button[onclick]')].filter(b=>b.getAttribute('onclick').startsWith(name+'(')&&!b.disabled);
      buttons.forEach(b=>{b.disabled=true;b.setAttribute('aria-busy','true');});
      try{return await original.apply(this,args);}catch(error){toast('Action impossible. Vérifiez votre connexion et réessayez.','error');}
      finally{busy=false;buttons.forEach(b=>{b.disabled=false;b.removeAttribute('aria-busy');});}
    };
  }
})();
document.addEventListener('click', e => {
 const button=e.target.closest('[data-delete-pdf]');
 if(button)supprimerPDF(Number(button.dataset.deletePdf),button.dataset.pdfTitle);
});
