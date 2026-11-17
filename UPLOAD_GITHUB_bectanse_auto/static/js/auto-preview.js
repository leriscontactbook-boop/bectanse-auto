/* Public illustration only: this component never calls trading or account APIs. */
(() => {
 const host=document.querySelector('.auto-demo'); if(!host)return;
 const button=host.querySelector('[role="switch"]');
 button.addEventListener('click',()=>{
  const active=button.getAttribute('aria-checked')!=='true';
  button.setAttribute('aria-checked',String(active));host.classList.toggle('is-paused',!active);
  host.querySelector('.auto-switch-label').textContent=active?'Actif':'En pause';
  host.querySelector('.auto-transfer-status').textContent=active?'Transmission illustrée vers MetaTrader 5':'Nouvelles transmissions en pause';
  host.querySelector('.auto-receiver-status').textContent=active?'Position reçue · exemple':'Position déjà reçue · conservée';
 });
})();
