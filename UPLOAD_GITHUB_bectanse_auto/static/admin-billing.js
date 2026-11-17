(() => {
  let page=0, busy=false, loadId=0;
  const $=id=>document.getElementById(id);
  const message=(text,error=false)=>{$('message').textContent=text;$('message').classList.toggle('aw-error',error);};
  const labels={new:'Nouveau',in_progress:'En cours',review:'À vérifier',resolved:'Résolu',running:'En cours',completed:'Terminé',success:'Réussi',succeeded:'Réussi',failed:'Échec',processing:'Traitement en cours',external_payment:'Paiement externe',gift:'Cadeau',manual_extension:'Prolongation manuelle',historical:'Historique exceptionnel',test:'Compte de test',retry_requested:'Vérification demandée',incident_status:'Classement de l’incident',exception_documented:'Justificatif enregistré',event_retry:'Reprise d’un événement'};
  const label=value=>labels[value]||value||'—';
  const date=value=>value&&!isNaN(Date.parse(value))?new Date(value).toLocaleString('fr-FR',{dateStyle:'short',timeStyle:'short'}):'Non renseigné';
  const el=(tag,text,cls)=>{const node=document.createElement(tag);node.textContent=text;if(cls)node.className=cls;return node;};
  async function post(path,body={}) {
    const response=await fetch('/admin/api/billing/'+path,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':billingCSRF},body:JSON.stringify(body)});
    const data=await response.json();if(!response.ok||!data.ok)throw Error(data.error||'Vérification indisponible');return data;
  }
  const cell=(row,text)=>{const td=el('td',text);row.append(td);return td;};
  function auditList(id,items,kind){
    const root=$(id);root.replaceChildren();
    if(!items.length){root.append(el('p','Aucun élément enregistré.','aw-module-empty'));return;}
    for(const item of items){
      const row=el('article','','aw-audit-row'),head=el('div','','aw-audit-heading');
      head.append(el('strong',label(item.action||item.kind||item.status)),el('time',date(item.created_at||item.started_at)));row.append(head);
      if(item.member_code)row.append(el('p',item.member_code));
      if(kind==='exceptions'){row.append(el('p',item.reason),el('small','Référence : '+item.source+' · Fin : '+date(item.expires_at)));}
      if(kind==='runs'&&item.finished_at)row.append(el('small','Terminé le '+date(item.finished_at)));
      const details=item.summary||item.details;
      if(details&&Object.keys(details).length){const disclosure=el('details','','aw-audit-details');disclosure.append(el('summary','Détails du contrôle'),el('pre',JSON.stringify(details,null,2)));row.append(disclosure);}
      root.append(row);
    }
  }
  async function load() {
    const id=++loadId; $('incidents').setAttribute('aria-busy','true');
    try {
      const response=await fetch('/admin/api/billing?status='+encodeURIComponent($('filter').value)+'&page='+page);const data=await response.json();
      if(!response.ok||!data.ok)throw Error(data.error||'Chargement indisponible');if(id!==loadId)return;
      auditList('runs',data.runs||[],'runs');auditList('history',data.actions||[],'history');auditList('exceptions',data.exceptions||[],'exceptions');
      const pending=$('pending-events');pending.replaceChildren();
      for(const event of data.pending_events||[]){const row=el('article','','aw-audit-row');row.append(el('strong',label(event.status)),el('p',event.event_id),el('p',event.last_error||''));const b=el('button','Retraiter');b.onclick=()=>run('event-retry',{event_id:event.event_id},b);row.append(b);pending.append(row);}
      if(!pending.children.length)pending.append(el('p','Aucun événement à reprendre.','aw-module-empty'));
      $('incidents').replaceChildren();$('page').textContent='Page '+(page+1);$('previous').disabled=page===0;$('next').disabled=data.incidents.length<100;
      for(const item of data.incidents) {
        const row=document.createElement('tr');const member=cell(row,'');member.append(el('strong',item.member_code||'Système'),el('p',label(item.kind)),el('small',item.last_error||''));
        let amount='';if(item.amount!==null&&item.amount!==undefined){try{amount=new Intl.NumberFormat('fr-FR',{style:'currency',currency:item.currency||'EUR'}).format(item.amount/100);}catch{amount=(item.amount/100).toLocaleString('fr-FR')+' '+item.currency;}}
        const payment=cell(row,'');for(const text of [amount,item.customer_id,item.subscription_id,item.invoice_id].filter(Boolean))payment.append(el('p',text));
        const state=cell(row,'');state.append(el('span',label(item.status),'aw-billing-badge'),el('p','Paiement : '+label(item.payment_state)),el('p','Accès : '+label(item.access_state)),el('small','Tentatives : '+item.attempts+' · '+date(item.checked_at)));
        const actions=cell(row,'');const controls=el('div','','aw-billing-row-actions');actions.append(controls);
        if(item.subscription_id){const button=el('button','Revérifier');button.onclick=()=>run('retry',{incident_id:item.id},button);controls.append(button);}
        const select=document.createElement('select');select.setAttribute('aria-label','État de l’incident '+item.id);for(const value of ['new','in_progress','review','resolved']){const option=el('option',label(value));option.value=value;select.append(option);}select.value=item.status;
        const save=el('button','Classer');save.onclick=async()=>{if(busy)return;const reason=await adminConfirm('Classer cet incident','Indiquez le motif de ce changement. Le classement ne modifie pas les accès.',{input:{label:'Motif',minLength:5},label:'Enregistrer'});if(reason)run('status',{incident_id:item.id,status:select.value,reason},save);};controls.append(select,save);$('incidents').append(row);
      }
      if(!data.incidents.length){const row=document.createElement('tr');const c=cell(row,'Aucun incident pour ce filtre.');c.colSpan=4;c.className='aw-module-empty';$('incidents').append(row);}
    }catch(error){message('Impossible de charger la facturation. Réessayez avec Actualiser.',true);}
    finally{if(id===loadId)$('incidents').removeAttribute('aria-busy');}
  }
  async function run(path,body={},button){
    if(busy)return;busy=true;if(button)button.disabled=true;message('Vérification en cours…');
    try{await post(path,body);message('Opération enregistrée. Les résultats ont été actualisés.');await load();}
    catch(error){message('Opération impossible : '+error.message,true);}
    finally{busy=false;if(button)button.disabled=false;}
  }
  $('refresh').onclick=()=>{message('');load();};$('filter').onchange=()=>{page=0;load();};$('previous').onclick=()=>{page=Math.max(0,page-1);load();};$('next').onclick=()=>{page++;load();};
  $('reconcile').onclick=async()=>{if(await adminConfirm('Vérifier les paiements Stripe ?','Cette commande lance une nouvelle vérification des paiements et des droits associés.'))run('reconcile',{},$('reconcile'));};
  $('exception').onsubmit=async event=>{event.preventDefault();if(busy)return;const form=event.target;const data=Object.fromEntries(new FormData(form));data.expires_at=new Date(data.expires_at).toISOString();if(await adminConfirm('Enregistrer ce justificatif ?', 'Cette justification sera ajoutée au journal d’audit pour '+data.member_code+'.'))run('exception',data,form.querySelector('button'));};load();
})();
