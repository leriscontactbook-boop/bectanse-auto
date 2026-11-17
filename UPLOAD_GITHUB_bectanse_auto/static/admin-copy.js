(() => {
  'use strict';
  const $=id=>document.getElementById(id), preview=document.body.dataset.preview==='true';
  const csrf=document.querySelector('meta[name="csrf-token"]').content;
  let activationFeedback=false;
  let state=null,view='accounts',busy=false,loading=false,selectedCode=null,scope='connected';
  const columns=['Compte','Connexion','Accès / abonnement','Copie ON/OFF','Préparation','Broker / Serveur','Login','Plateforme','Lots','Solde','Équité','Équité %','Évolution %','Positions ouvertes','P&L ouvert','P&L jour','P&L semaine','P&L mois','P&L total','Dépôts','Retraits','Crédit actuel','Ajustements','Données au','Actions'];
  const hiddenColumns=new Set([2,3,4,19,20,21,22]);
  const decisions={PENDING:'À examiner',APPROVED:'Approuvé',PAUSED:'Pause demandée',REJECTED:'Refusé'};
  const validation={PENDING:'Vérification en attente',VALIDATED_SIMULATION:'Paramètres vérifiés',FAILED:'Paramètres à corriger',SUPERSEDED:'Ancienne version',LEGACY_REQUEST:'Compte à rattacher'};
  const actions={SETTINGS_UPDATED:'Réglages de copie modifiés',MASTER_ASSIGNED:'Maître rattaché',MASTER_REMOVED:'Maître retiré',LOTS_UPDATED:'Lots modifiés',MASTER_SAVED:'Compte maître enregistré',APPROVED:'Rattachement approuvé',PAUSED:'Pause demandée',REJECTED:'Demande refusée'};
  const errors={REAL_TRADING_FORBIDDEN:'Le pilote accepte uniquement les comptes démo.',TERMINAL_DISCONNECTED:'Le terminal MT5 est déconnecté.',SOURCE_STALE:'Le compte maître ne transmet plus de données récentes.',STOP_LOSS_REQUIRED:'Un stop loss est nécessaire.',TRADING_PERMISSION_DENIED:'Le terminal ne permet pas le trading automatique.',ACCOUNT_IDENTITY_CHANGED:'Le compte connecté a changé.',SYMBOL_UNAVAILABLE:'Instrument indisponible.',ORDER_CHECK_REJECTED:'Le broker a refusé la vérification de l’ordre.'};
  const date=v=>v?new Date(v).toLocaleString('fr-FR',{timeZone:'Europe/Paris'}):'Aucune connexion reçue';
  function el(tag,value,cls){const n=document.createElement(tag);if(value!==undefined)n.textContent=String(value);if(cls)n.className=cls;return n;}
  function message(value,id='message'){const n=$(id);n.textContent=value;n.hidden=!value;}
  async function api(path,body){
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),12000);
    try{
      const r=await fetch('/admin/api/copy-trading'+path,{signal:controller.signal,method:body?'POST':'GET',credentials:'same-origin',cache:'no-store',headers:body?{'Content-Type':'application/json','X-CSRF-Token':csrf}:{},body:body?JSON.stringify(body):undefined});
      const d=await r.json();if(!r.ok||!d.ok)throw new Error(d.message||d.error||'Impossible de charger les comptes.');return d;
    }catch(error){if(error.name==='AbortError')throw new Error('Le serveur tarde à répondre. L’état sera revérifié automatiquement.');throw error;}
    finally{clearTimeout(timer);}
  }
  function refreshSwitch(){for(const b of $('detail-copy-switch').querySelectorAll('button'))b.disabled=preview||busy;}

  function showMaster(){if(!state)return;const m=state.master||{},form=$('master-form');for(const name of ['alias','login','server','environment'])form.elements[name].value=m[name]||(name==='environment'?'DEMO':'');form.elements.password.value='';form.dataset.revision=m.revision||0;form.elements.password.required=!m.has_password;form.querySelector('[type="submit"]').disabled=preview||busy;$('password-help').textContent=m.has_password?'Mot de passe enregistré. Laissez vide pour le conserver.':'Le mot de passe est chiffré et n’est jamais affiché.';message(preview?'Aperçu local : l’enregistrement n’est pas disponible.':'','form-message');$('master-dialog').showModal();}
  function identity(name,role,master){const td=el('td'),wrap=el('div',undefined,'account-name'),icon=el('span',master?'':'','account-symbol'),label=el('div');label.append(el('strong',name),el('small',role));wrap.append(icon,label);td.append(wrap);return td;}
  function cell(value,cls){return el('td',value??'—',cls);}
  function status(value,cls){const td=el('td');td.append(el('span',value,'status '+(cls||'')));return td;}
  function manage(label,fn){const td=el('td'),b=el('button',label,'row-action');b.type='button';b.addEventListener('click',fn);td.append(b);return td;}
  function detailField(label,value){const line=el('div');line.append(el('dt',label),el('dd',value??'—'));$('detail-fields').append(line);}
  let assignment=null, assignmentGeneration=0, searchTimer=null;
  let brokerCatalog=[];
  function resetCredentials(){for(const id of ['assisted-broker','assisted-server','assisted-login','assisted-password'])$(id).value='';}
  function credentialMode(){
    const enabled=!!assignment&&$('assignment-account').value==='new';
    $('assignment-credentials').hidden=!enabled;$('assignment-credentials').disabled=!enabled;
  }
  async function loadBrokers(){try{const data=await api('/brokers');brokerCatalog=data.brokers;
    $('assisted-brokers').replaceChildren(...brokerCatalog.map(b=>{const o=el('option');o.value=b.name;return o;}));
  }catch(e){message('Catalogue indisponible. Vous pouvez saisir le broker et le serveur exacts.','assignment-message');}}
  $('assisted-broker').addEventListener('input',()=>{
    const broker=brokerCatalog.find(b=>b.name.toLowerCase()===$('assisted-broker').value.toLowerCase());
    $('assisted-servers').replaceChildren(...(broker?.servers||[]).map(s=>{const o=el('option');o.value=s;return o;}));
  });
  function updateAssignmentButton(){
    credentialMode();
    $('assignment-submit').disabled=preview||busy||!assignment||!$('assignment-account').value||(assignment.version===0&&$('assignment-master').value==='')||(!assignment.active&&$('assignment-master').value!=='');
  }
  async function selectMember(code){
    resetCredentials();const generation=++assignmentGeneration;assignment=null;updateAssignmentButton();
    message('Chargement du compte…','assignment-message');
    try{
      const data=await api('/assignment?code='+encodeURIComponent(code));
      if(generation!==assignmentGeneration||!$('assignment-dialog').open)return;
      assignment=data;$('assignment-results').replaceChildren();$('assignment-member').textContent=data.name+' · '+(data.active?'Accès actif':'Accès inactif');
      $('assignment-account').replaceChildren();
      for(const a of data.accounts){const option=el('option',[a.name,a.login_masked,a.server].filter(Boolean).join(' · '));option.value=a.id;option.disabled=Boolean(data.account_id&&data.account_id!==a.id);$('assignment-account').append(option);}
      if(!data.account_id&&data.active){const option=el('option','Ajouter un compte MT5 pour ce membre');option.value='new';$('assignment-account').append(option);}
      if(data.account_id)$('assignment-account').value=data.account_id;
      $('assignment-master').replaceChildren();const none=el('option','Aucun');none.value='';$('assignment-master').append(none);
      if(data.master){const m=data.master;const option=el('option',[m.alias,m.server,m.login,m.environment==='DEMO'?'Démo':'Réel'].join(' · '));option.value=m.revision;option.disabled=!data.active;$('assignment-master').append(option);if(data.assignment&&data.assignment.login===m.login&&data.assignment.server===m.server)$('assignment-master').value=m.revision;}
      message(!data.accounts.length?'Renseignez les identifiants MT5 ci-dessous pour enregistrer le compte de ce membre.':!data.active?'Accès inactif : seul le retrait du maître est autorisé.':!data.master?'Configurez d’abord le compte maître.':'Le rattachement prépare le compte. L’activation reste soumise au choix du membre et aux contrôles du serveur.','assignment-message');
      updateAssignmentButton();
    }catch(e){if(generation===assignmentGeneration)message(e.message,'assignment-message');}
  }
  function openAssignment(code){
    resetCredentials();loadBrokers();assignment=null;assignmentGeneration++;$('assignment-search').value='';$('assignment-results').replaceChildren();$('assignment-account').replaceChildren();$('assignment-master').replaceChildren();$('assignment-member').textContent='';message('','assignment-message');updateAssignmentButton();
    if($('details').open)$('details').close();$('assignment-dialog').showModal();
    if(code)selectMember(code);else $('assignment-search').focus();
  }
  $('add-follower').addEventListener('click',()=>openAssignment());
  $('add-manual-account').addEventListener('click',()=>{
    if(preview||busy)return;
    $('manual-account-form').reset();message('','manual-account-message');
    $('manual-account-dialog').showModal();loadBrokers();
  });
  $('manual-account-cancel').addEventListener('click',()=>{if(!busy)$('manual-account-dialog').close();});
  $('manual-account-dialog').addEventListener('cancel',e=>{if(busy)e.preventDefault();});
  $('manual-account-dialog').addEventListener('close',()=>$('manual-account-form').reset());
  $('manual-account-form').addEventListener('submit',async e=>{
    e.preventDefault();if(busy||preview)return;
    busy=true;$('manual-account-submit').disabled=true;$('manual-account-cancel').disabled=true;
    const form=$('manual-account-form');
    const payload=Object.fromEntries(new FormData(form));
    try{
      const saved=await api('/manual-account',payload);
      form.reset();$('manual-account-dialog').close();message(saved.message);scope='all';
      await load();await openAssignment(saved.code);
    }catch(error){message(error.message,'manual-account-message');}
    finally{delete payload.password;form.elements.password.value='';busy=false;$('manual-account-submit').disabled=false;$('manual-account-cancel').disabled=false;updateAssignmentButton();}
  });

  $('choose-master').addEventListener('click',()=>openAssignment(selectedCode));
  $('assignment-dialog').addEventListener('close',()=>{assignmentGeneration++;clearTimeout(searchTimer);resetCredentials();});
  $('assignment-account').addEventListener('change',updateAssignmentButton);
  $('assignment-master').addEventListener('change',updateAssignmentButton);
  $('assignment-search').addEventListener('input',()=>{
    clearTimeout(searchTimer);const q=$('assignment-search').value.trim(),generation=++assignmentGeneration;
    assignment=null;resetCredentials();updateAssignmentButton();$('assignment-results').replaceChildren();
    if(q.length<2)return;
    searchTimer=setTimeout(async()=>{try{const data=await api('/members?q='+encodeURIComponent(q));if(generation!==assignmentGeneration)return;
      for(const m of data.members){const button=el('button',m.name+' · '+m.email+' · '+(m.active?'Actif':'Inactif'),'secondary');button.type='button';button.addEventListener('click',()=>selectMember(m.code));$('assignment-results').append(button);}
      if(!data.members.length)$('assignment-results').append(el('p','Aucun membre trouvé.'));
    }catch(e){if(generation===assignmentGeneration)message(e.message,'assignment-message');}},250);
  });
  $('assignment-form').addEventListener('submit',async event=>{
    event.preventDefault();if(busy||!assignment||preview)return;
    busy=true;updateAssignmentButton();const data={code:assignment.code,version:assignment.version,account_id:Number($('assignment-account').value),master_revision:$('assignment-master').value===''?null:Number($('assignment-master').value)};
    try{
      if($('assignment-account').value==='new'){
        const saved=await api('/account',{code:data.code,broker:$('assisted-broker').value,server:$('assisted-server').value,login:$('assisted-login').value,password:$('assisted-password').value});
        resetCredentials();await selectMember(data.code);data.account_id=saved.account_id;
        $('assignment-account').value=String(saved.account_id);$('assignment-master').value=data.master_revision===null?'':String(data.master_revision);credentialMode();
        message('Compte enregistré. Rattachement en cours…','assignment-message');
      }
      const result=await api('/assignment',data);$('assignment-dialog').close();message(result.message);scope='all';await load();}
    catch(e){message(e.message,'assignment-message');}finally{busy=false;updateAssignmentButton();}
  });
  function syncDetailStatus(row){
    if(!row.onboarding)return;
    refreshSwitch();
    const o=row.onboarding,c=o.control||{},real=row.real_connection;
    const values={
      'État client':o.message,
      'Préparation serveur':preparationLabel(row),
      'Motif':o.message,
      'Connexion':o.connected?'MT5 connecté':real?'Connexion MT5 à revérifier':'Connexion MT5 attendue',
      'Copie prête':o.execution_enabled?'Active':c.desired==='ACTIVE'?'ON — démarrage automatique en cours':'OFF — activation non demandée',
      'Demande du membre':c.desired==='ACTIVE'?'ON demandé (membre ou administrateur)':'OFF',
      'Dernière communication':real?date(real.observed_at*1000):date(row.last_seen)
    };
    if(activationFeedback)message(o.execution_enabled?'Copie active, confirmée par le moteur.':o.message,'detail-message');
    for(const line of $('detail-fields').children){const label=line.querySelector('dt')?.textContent;if(label in values)line.querySelector('dd').textContent=values[label];}
  }
  function renderPositions(row){$('detail-positions').replaceChildren();if(!row.positions.length)$('detail-positions').append(el('p',row.online?'Aucune position signalée.':'Positions indisponibles tant que le compte n’est pas raccordé.'));for(const p of row.positions){const line=el('p',[p.symbol,p.side,p.volume!==undefined?p.volume+' lot(s)':'',p.state].filter(Boolean).join(' · '),'position-line');$('detail-positions').append(line);}}
  const settingGroups=[
    ['Volumes et risque',[
      ['mode_risque','Mode de calcul','select','Lots fixes',['Lots fixes','Lot par symbole',"Copier les lots de l'envoyeur",'Risque par solde (Balance)','Risque par capitaux (Equity)','Risque en %']],
      ['lots','Lot fixe','number',0.01,0.01,100],['lots_max','Lot maximum par position','number',5,0.01,1000],
      ['multiplicateur','Multiplicateur des lots du maître','number',1,0.01,100],
      ['risque_pct','Risque par trade (%)','number',1,0.01,100],
      ['risque_balance_pct','Risque sur le solde (%)','number',1,0.01,100],
      ['risque_equity_pct','Risque sur les capitaux (%)','number',1,0.01,100],
      ['forcer_lot_minimum','Utiliser le lot minimum du courtier si nécessaire','checkbox',false],
    ]],
    ['Exécution et ordres',[
      ['slippage','Slippage maximum (points)','number',100,0,1000],
      ['inverser_trades','Inverser le sens des trades','checkbox',false],
      ['copier_ordres_en_attente','Copier les ordres en attente','checkbox',true],
      ['convertir_pending_invalide','Convertir un ordre en attente invalide en ordre au marché','checkbox',false],
      ['copier_sl','Copier le stop loss','checkbox',true],
    ]],
    ['Protection du solde',[
      ['drawdown_actif','Fermer les copies au seuil de perte','checkbox',false],
      ['drawdown_pct','Seuil de perte du solde (%)','number',5,0.01,100],
      ['drawdown_gain_actif','Fermer les copies au seuil de gain','checkbox',false],
      ['drawdown_gain_pct','Seuil de gain du solde (%)','number',5,0.01,10000],
    ]],
    ['Objectifs et annonces',[
      ['objectif_actif','Suspendre les nouvelles copies aux objectifs','checkbox',false],
      ['objectif_periode','Période des objectifs','select','Mensuel',['Quotidien','Hebdomadaire','Mensuel']],
      ['objectif_gain_pct','Objectif de gain (%)','number',5,0.01,10000],
      ['objectif_perte_pct','Limite de perte (%)','number',3,0.01,100],
      ['filtre_news','Suspendre les nouvelles entrées autour des annonces majeures','checkbox',false],
    ]],
  ];
  // Use the exact persisted API spelling while keeping natural typography in labels.
  settingGroups[0][1][0][4][2]="Copier les lots de l'envoyeur";
  function symbolSettingRow(symbol='',lot=''){
    const row=el('div',undefined,'symbol-setting-row'),name=el('input'),value=el('input'),remove=el('button','Retirer','secondary');
    name.type='text';name.value=symbol;name.placeholder='XAUUSD';name.maxLength=64;name.setAttribute('aria-label','Symbole de l’instrument');name.dataset.symbolName='true';
    value.type='number';value.value=lot;value.min='0.01';value.max='100';value.step='0.01';value.placeholder='0,01';value.setAttribute('aria-label','Lots pour cet instrument');value.dataset.symbolLot='true';
    remove.type='button';remove.addEventListener('click',()=>row.remove());row.append(name,value,remove);$('symbol-settings-rows').append(row);
  }
  function renderSettings(row){
    const form=$('lots-form'),box=$('client-settings-fields');box.replaceChildren();
    form.dataset.version=row.version??'';form.dataset.account=row.account_id??'';
    const values={...(state.settings_defaults||{}),...row.settings};
    for(const [title,fields] of settingGroups){
      const section=el('fieldset',undefined,'settings-group');section.append(el('legend',title));
      for(const [key,label,type,fallback,min,max] of fields){
        const wrapper=el('label',undefined,type==='checkbox'?'setting-toggle':'setting-field'),input=el(type==='select'?'select':'input');
        input.id=key==='lots'?'account-lots':'setting-'+key;input.name=key;wrapper.htmlFor=input.id;
        const value=values[key]??fallback;
        if(type==='select'){for(const choice of min){const option=el('option',choice);option.value=choice;input.append(option);}input.value=value;}
        else{input.type=type;if(type==='checkbox')input.checked=value===true;else{input.value=value;input.min=min;input.max=max;input.step=key==='slippage'?'1':'0.01';input.required=true;}}
        if(type==='checkbox')wrapper.append(input,el('span',label));else wrapper.append(el('span',label),input);
        section.append(wrapper);
      }
      box.append(section);
      if(title==='Volumes et risque'){
        const symbols=el('fieldset',undefined,'settings-group settings-symbols');symbols.append(el('legend','Lots par symbole'));
        symbols.append(el('p','Utilisés lorsque le mode « Lot par symbole » est sélectionné. Les modes en pourcentage utilisent la perte calculée jusqu’au stop loss.','form-note'));
        const rows=el('div');rows.id='symbol-settings-rows';symbols.append(rows);box.append(symbols);
        for(const [symbol,lot] of Object.entries(values.lot_symboles||{}))symbolSettingRow(symbol,lot);
        const add=el('button','Ajouter un instrument','secondary');add.type='button';add.addEventListener('click',()=>symbolSettingRow());symbols.append(add);
      }
    }
    box.append(el('p','Objectifs : reprise à la période suivante, heure de Paris. Filtre d’annonces : 15 minutes avant et après les annonces majeures. Les protections gèrent uniquement les positions copiées par Bectanse.','form-note'));
    form.elements.copier_ordres_en_attente.addEventListener('change',()=>{if(!form.elements.copier_ordres_en_attente.checked)form.elements.convertir_pending_invalide.checked=false;});
    form.elements.convertir_pending_invalide.addEventListener('change',()=>{if(form.elements.convertir_pending_invalide.checked)form.elements.copier_ordres_en_attente.checked=true;});
    for(const field of form.querySelectorAll('input,select,button'))field.disabled=preview||busy||!row.account_id||row.version===null;
    $('settings-version').textContent=row.version!==null?'Version '+row.version:'';
    message(row.report_current?'Réglages reçus par le moteur.':'Réglages enregistrés ; synchronisation du moteur en attente.','lots-message');
  }
  function readSettings(){
    const form=$('lots-form'),settings={};
    for(const [,fields] of settingGroups)for(const [key,,type] of fields){const input=form.elements[key];settings[key]=type==='checkbox'?input.checked:type==='number'?Number(input.value):input.value;}
    settings.lot_symboles={};
    for(const row of $('symbol-settings-rows').children){
      const symbol=row.querySelector('[data-symbol-name]').value.trim(),input=row.querySelector('[data-symbol-lot]'),raw=input.value;
      if(!symbol&&!raw)continue;
      if(!symbol||!raw||!input.checkValidity())throw new Error('Renseignez un symbole et un lot valide pour chaque instrument.');
      if(Object.hasOwn(settings.lot_symboles,symbol))throw new Error('Cet instrument figure plusieurs fois : '+symbol);
      settings.lot_symboles[symbol]=Number(raw);
    }
    if(settings.mode_risque==='Lot par symbole'&&!Object.keys(settings.lot_symboles).length)throw new Error('Ajoutez au moins un instrument pour le mode Lot par symbole.');
    return settings;
  }

  function showDetails(row){if(selectedCode!==row.code)activationFeedback=false;selectedCode=row.code;$('detail-engine-state').textContent=copyLabel(row);renderSettings(row);$('detail-name').textContent=row.name;$('detail-account').textContent=[row.account,row.server].filter(Boolean).join(' · ');$('detail-fields').replaceChildren();if(row.onboarding)detailField('État client',row.onboarding.message);detailField('Préparation serveur',row.preparation?({PREPARING:'Préparation en cours',BLOCKED:'Bloqué',ERROR:'Erreur temporaire',UNASSIGNED:'Aucun maître'}[row.preparation.state]||'À vérifier'):'Non demandée');if(row.preparation?.message)detailField('Motif',row.preparation.message);detailField('Maître choisi',row.master_assignment?row.master_assignment.server+' · '+row.master_assignment.login:'Aucun choix enregistré');detailField('Connexion',row.connection_label||'À vérifier');detailField('Accès',row.membership_label||'À vérifier');detailField('Abonnement',row.billing_status||'Non renseigné');detailField('Copie prête',row.copy_ready?'Oui':'Non');detailField('Demande du membre',row.requested?'Activation demandée':'Activation non demandée');detailField('Décision',decisions[row.decision]||'À examiner');detailField('Paramètres',validation[row.validation]||'À vérifier');detailField('Dernière communication',date(row.last_seen));detailField('Mode de calcul',row.settings.mode_risque||'—');detailField('Lots',row.settings.mode_risque==='Lot par symbole'?Object.entries(row.settings.lot_symboles||{}).map(([k,v])=>k+' : '+v).join(' / '):row.settings.lots);if(row.settings.copier_sl!==undefined)detailField('Copier le stop loss',row.settings.copier_sl?'Oui':'Non');$('detail-incidents').replaceChildren();for(const error of row.errors)$('detail-incidents').append(el('p',errors[error]||'Vérification nécessaire ('+error+').'));renderPositions(row);renderFinancials(row);$('detail-actions').replaceChildren();for(const [label,decision,cls] of [['Refuser','REJECTED','secondary'],['Mettre en pause','PAUSED','secondary'],['Approuver et démarrer','APPROVED','primary']]){const b=el('button',label,cls);b.disabled=preview||busy||row.version===null;b.addEventListener('click',()=>decision==='APPROVED'?adminActivation(state.followers.find(r=>r.code===row.code)||row,'activate',b):review(state.followers.find(r=>r.code===row.code)||row,decision));$('detail-actions').append(b);}message(row.version===null?'Cette ancienne demande doit d’abord être rattachée à un compte MT5.':'','detail-message');if(!$('details').open)$('details').showModal();syncDetailStatus(row);loadOperational(row);}
  let operationalGeneration=0;
  // A retry is the same business request.  Keep its id until the account is
  // explicitly switched OFF (or its configuration version changes), so a
  // response loss or a double click cannot reset the activation deadline.
  const activationRequestIds=new Map();
  async function adminActivation(row,action,button){
    if(busy||preview)return;busy=true;activationFeedback=true;refreshSwitch();button.disabled=true;
    try{
      const requestKey=[row.code,row.version,action].join(':');
      const requestId=activationRequestIds.get(requestKey)||crypto.randomUUID().replaceAll('-','');
      activationRequestIds.set(requestKey,requestId);
      const command={code:row.code,version:row.version,action,request_id:requestId};
      message('Transmission de la demande…','detail-message');
      const result=await api('/activation',command);
      if(action==='disable'){
        for(const key of activationRequestIds.keys())if(key.startsWith(row.code+':'))activationRequestIds.delete(key);
      }
      message(result.execution_enabled?'Copie confirmée active par le moteur.':action==='activate'?'Connexion et synchronisation automatiques en cours.':'OFF enregistré. Les nouvelles entrées s’arrêtent ; les copies existantes restent suivies et clôturées automatiquement.','detail-message');
      await load();
    }catch(e){message('Commande non confirmée : '+e.message,'detail-message');}finally{busy=false;button.disabled=false;refreshSwitch();}
  }
  async function loadOperational(row){
    $('detail-copy-switch').replaceChildren();
    for(const [action,label] of [['activate','ON — Activer la copie'],['disable','OFF — Arrêter les nouvelles entrées']]){
      const button=el('button',label,action==='activate'?'primary':'secondary');button.type='button';button.disabled=preview||busy||!row.account_id;
      button.addEventListener('click',()=>adminActivation(row,action,button));$('detail-copy-switch').append(button);
    }
    const generation=++operationalGeneration;
    if(preview||!row.account_id)return;
    try{
      const data=await api('/operational?code='+encodeURIComponent(row.code));
      if(generation!==operationalGeneration||selectedCode!==row.code||!$('details').open)return;
      detailField('Autorisation opérationnelle',({AUTHORIZED:'Vérifiée',NOT_AUTHORIZED:'Automatique au démarrage',EXPIRED:'Renouvellement automatique',CONFIGURATION_CHANGED:'Vérification automatique en cours'})[data.state]||'À vérifier');
      const candidate=data.candidate;
      if(candidate){
        detailField('Instruments vérifiés',candidate.instruments.map(p=>p.source+' → '+p.target).join(', '));
        detailField('Portée des preuves','Contrôles MT5 en lecture seule ; exécution réelle non testée.');
        detailField('Validité des preuves',date(candidate.expires_at*1000));
      }else detailField('Preuves de compatibilité','Aucune preuve valide reçue pour cette configuration.');
    }catch(error){if(generation===operationalGeneration&&selectedCode===row.code)message(error.message,'detail-message');}
  }
  async function review(row,decision){if(busy)return;busy=true;for(const b of $('detail-actions').querySelectorAll('button'))b.disabled=true;try{await api('/review',{code:row.code,version:row.version,decision});$('details').close();message(decision==='APPROVED'?'Rattachement approuvé. Pour démarrer, ouvrez Gérer puis cliquez sur ON — Activer la copie.':'La décision a été enregistrée.');await load();}catch(e){message(e.message,'detail-message');}finally{busy=false;if($('details').open)for(const b of $('detail-actions').querySelectorAll('button'))b.disabled=preview||row.version===null;}}
  $('safe-recovery').addEventListener('click',async()=>{
    if(busy||preview)return;
    busy=true;$('safe-recovery').disabled=true;
    try{
      const result=await api('/safe-recovery',{});
      message(result.queued?`${result.queued} vérification(s) MT5 relancée(s). Aucun ordre ni réglage n’a été modifié.`:'Aucun compte éligible à relancer : les protections financières et les sessions actives sont conservées.');
      await load();
    }catch(error){message(error.message);}
    finally{busy=false;$('safe-recovery').disabled=preview;}
  });
  $('lots-form').addEventListener('submit',async event=>{
    event.preventDefault();if(busy||preview)return;
    const form=event.currentTarget,savedCode=selectedCode;busy=true;for(const field of form.querySelectorAll('input,select,button'))field.disabled=true;
    try{
      const settings=readSettings();
      const result=await api('/settings',{code:savedCode,account_id:Number(form.dataset.account),version:Number(form.dataset.version),settings});
      form.dataset.version=result.version;
      if(result.unchanged){message('Réglages déjà enregistrés.','lots-message');return;}
      message('Réglages enregistrés. Synchronisation automatique en cours…','lots-message');
      await load();
      const updated=state?.followers.find(r=>r.code===savedCode);
      if(updated&&selectedCode===savedCode){showDetails(updated);message('Réglages enregistrés. Synchronisation automatique en cours…','lots-message');}
      for(let i=0;i<8&&$('details').open&&selectedCode===savedCode;i++){
        const row=state?.followers.find(r=>r.code===selectedCode);
        if(row?.version===result.version&&row.report_current){message(row.decision==='APPROVED'&&row.requested&&!row.errors.length?'Tous les réglages ont été reçus par le moteur.':'Réglages reçus par le moteur. L’état ON/OFF du compte est conservé.','lots-message');break;}
        await new Promise(resolve=>setTimeout(resolve,1000));await load();
      }
    }catch(error){message(error.message,'lots-message');}
    finally{busy=false;refreshSwitch();for(const field of form.querySelectorAll('input,select,button'))field.disabled=preview||!form.dataset.account;for(const button of $('detail-actions').querySelectorAll('button'))button.disabled=preview||!form.dataset.version;}
  });
  function applyColumns(){document.querySelectorAll('table tr').forEach(tr=>[...tr.children].forEach((cell,index)=>{cell.hidden=hiddenColumns.has(index);}));}
  function exportAccounts(){
    if(!state)return;
    const rows=[columns.slice(0,-1),...visibleRows().map(r=>[r.name,r.connection_label||'À vérifier',r.membership_label||'À vérifier',copyLabel(r),preparationLabel(r),r.server,r.account,r.statistics?.platform||'MT5',r.settings.lots??'',...statValues(r)])];
    const quote=v=>'"'+String(v??'').replace(/^[=+@\-\t\r]/,"'$&").replaceAll('"','""')+'"';
    const blob=new Blob(['\ufeff'+rows.map(row=>row.map(quote).join(';')).join('\r\n')],{type:'text/csv;charset=utf-8'}),url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download='bectanse-comptes-filtres.csv';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  const scopes=[
    ['connected','Connectés',r=>(r.onboarding?.connected??r.connected)===true],
    ['ready','Copie prête',r=>r.onboarding?r.onboarding.state==='READY_PENDING_AUTHORIZATION'||r.onboarding.execution_enabled:r.copy_ready===true],
    ['active','Accès actifs',r=>r.membership==='ACTIVE'],
    ['inactive','Accès inactifs',r=>r.membership==='INACTIVE'],
    ['on','Copie ON',r=>r.onboarding?r.onboarding.execution_enabled:r.requested===true],
    ['off','Copie OFF',r=>r.onboarding?!r.onboarding.execution_enabled:r.requested!==true],
    ['offline','Non connectés',r=>(r.onboarding?.connected??r.connected)!==true],
    ['preparing','Préparation',r=>r.preparation?.state==='PREPARING'],
    ['blocked','Protections',r=>r.onboarding?.state==='BLOCKED_RISK'],
    ['all','Tous',()=>true]
  ];
  function visibleRows(){
    const q=$('search').value.toLowerCase(),filter=$('filter').value;
    const predicate=view==='requests'?r=>r.requested&&r.decision==='PENDING':scopes.find(x=>x[0]===scope)[2];
    return state.followers.filter(r=>predicate(r)&&(filter==='ALL'||r.decision===filter)&&[r.name,r.email,r.account,r.server].join(' ').toLowerCase().includes(q));
  }
  const preparationLabel=r=>r.onboarding?({ACTIVATION_PENDING:'Activation technique · moins de 2 min',PAUSE_PENDING:'Pause à confirmer',REGISTERED:'Enregistré',VERIFIED:'Compte vérifié',CONNECTED:'Compte connecté',ERROR:'Erreur d’activation',BLOCKED_RISK:'Protection financière',BLOCKED:'Vérification requise',UNASSIGNED:'Non rattaché',READY_PENDING_AUTHORIZATION:'Activation membre attendue',ACTIVE:'Copie active',PAUSED:'En pause'}[r.onboarding.state]||'À vérifier'):({PREPARING:'En préparation',BLOCKED:'Vérification requise',ERROR:'Erreur temporaire',UNASSIGNED:'Non rattaché'}[r.preparation?.state]||'Non demandée');
  const copyLabel=r=>r.onboarding?(r.onboarding.execution_enabled?'ON · confirmée':r.onboarding.state==='ERROR'?'ON · erreur à résoudre':r.onboarding.control?.desired==='ACTIVE'?'ON · activation en cours':'OFF · '+preparationLabel(r)):r.copy_ready?'ON · prête':r.requested?'ON · en attente':'OFF';
  function money(value,stats){if(value===null||value===undefined||!Number.isFinite(Number(value)))return '—';return Number(value).toLocaleString('fr-FR',{maximumFractionDigits:2,minimumFractionDigits:2})+' '+(stats?.currency||'');}
  const percent=v=>v===null||v===undefined?'—':Number(v).toLocaleString('fr-FR',{minimumFractionDigits:2,maximumFractionDigits:2})+' %';
  const metricKeys=['balance','equity','equity_percent','evolution_percent','positions','open_pnl','daily','weekly','monthly','total','deposits','withdrawals','credit','adjustments','observed_at'];
  const metricHelp={equity_percent:'P&L ouvert, swaps inclus ÷ solde × 100. Crédits exclus du dénominateur.',evolution_percent:'P&L net réalisé ÷ dépôts nets × 100, sur l’historique disponible. Affiché uniquement si le solde est réconcilié et les dépôts nets positifs.',total:'P&L net de trading sur l’historique disponible, frais et swaps inclus. Dépôts, retraits et crédits exclus.',credit:'Crédit ou bonus actuel accordé par le courtier : ce n’est pas un gain.',adjustments:'Corrections et mouvements non classés comme trading, dépôts ou crédits.'};
  function statValues(r){const s=r.statistics||{};return metricKeys.map(k=>k==='positions'?r.open_positions??'—':k==='observed_at'?s.observed_at?date(s.observed_at)+(s.fresh?' · récent':' · ancien'):'Non disponible':k.endsWith('_percent')?percent(s[k]):money(s[k],s));}
  function statCells(r){const s=r.statistics||{};return statValues(r).map((v,i)=>{const k=metricKeys[i],signed=['open_pnl','equity_percent','evolution_percent','daily','weekly','monthly','total'].includes(k),n=cell(v,'numeric '+(signed&&s[k]>0?'positive':signed&&s[k]<0?'negative':''));if(metricHelp[k])n.title=metricHelp[k];if(k==='total'&&!s.history_complete)n.title+=' Historique limité ou non réconcilié : le total ne garantit pas toute la vie du compte.';return n;});}
  function renderFinancials(row){let journalButton=$('detail-journal');if(!journalButton){journalButton=el('button','Voir le journal complet','secondary');journalButton.id='detail-journal';journalButton.type='button';$('detail-financials').before(journalButton);}journalButton.disabled=!row.account_id;journalButton.onclick=()=>window.openClientJournal(row);const box=$('detail-financials');box.replaceChildren();statValues(row).forEach((v,i)=>{const line=el('div');line.append(el('dt',columns[9+i]),el('dd',v));box.append(line);});}
  function render(){
    if(!state)return;
    const all=state.followers,pending=all.filter(r=>r.requested&&r.decision==='PENDING');
    $('request-count').textContent=pending.length;$('request-count').hidden=!pending.length;
    $('configure-master').textContent=state.master?'Configurer le maître':'+ Ajouter le compte maître';
    $('configure-master').hidden=view==='activity';$('accounts-view').hidden=view==='activity';$('activity-view').hidden=view!=='activity';
    $('page-title').textContent={accounts:'Comptes de trading',requests:'Demandes de connexion',activity:'Historique'}[view];
    $('page-description').textContent={accounts:'Connexion, accès et activation : un état distinct pour chaque compte.',requests:'Les demandes à examiner. Une demande ne signifie pas que le compte est connecté.',activity:'Les modifications apportées aux comptes et aux demandes.'}[view];
    $('account-tabs').hidden=view!=='accounts';$('account-tabs').replaceChildren();
    for(const [key,label,predicate] of scopes){
      const button=el('button',label+' '+all.filter(predicate).length,scope===key?'selected':'');
      button.type='button';button.setAttribute('aria-pressed',String(scope===key));
      button.addEventListener('click',()=>{scope=key;render();});$('account-tabs').append(button);
    }
    $('scope-description').textContent=view==='requests'?'Aucune demande ne sera activée par ce tri.':scope==='connected'?'Rapport récent du moteur, version à jour et aucune erreur signalée.':scope==='ready'?'Connecté, accès actif, bouton ON et validation administrateur à jour.':'Les filtres peuvent se recouper : accès actif et bouton ON ne prouvent pas une connexion.';
    const rows=visibleRows();$('accounts').replaceChildren();
    const m=state.master,q=$('search').value.toLowerCase();
    const showMasterRow=view==='accounts'&&m&&['connected','all'].includes(scope)&&$('filter').value==='ALL'&&!q;
    if(showMasterRow){
      const tr=el('tr',undefined,'master-row');
      tr.append(identity(m.alias,'Maître · '+(m.environment==='DEMO'?'Démo':'Réel'),true),status(m.online?'MT5 connecté':'Données indisponibles',m.online?'good':'pending'),cell('—'),cell('—'),cell('—'),cell(m.server),cell(m.login),cell('MT5'),cell('—'),...statCells(m),manage('Configurer',showMaster));$('accounts').append(tr);
    }
    for(const r of rows){
      const tr=el('tr',undefined,r.connected?'follower':'');
      tr.append(identity(r.name,r.onboarding?copyLabel(r):((r.connected?'Compte connecté':r.version===null?'Ancienne demande':'Compte membre')+' · '+(decisions[r.decision]||'À examiner')),false),status(r.onboarding?.connected?'MT5 connecté':r.connection_label||'À vérifier',(r.onboarding?.connected??r.connected)?'good':'pending'),cell(r.membership_label||'À vérifier'),status(copyLabel(r),(r.onboarding?.execution_enabled??r.copy_ready)?'good':'pending'),cell(preparationLabel(r)),cell(r.server),cell(r.account),cell(r.statistics?.platform||'MT5'),cell(r.settings.mode_risque==='Lot par symbole'?'Par symbole':r.settings.lots),...statCells(r),manage('Gérer',()=>showDetails(r)));
      const journalButton=el('button','Journal','secondary');journalButton.type='button';journalButton.disabled=!r.account_id;journalButton.addEventListener('click',()=>window.openClientJournal(r));tr.lastElementChild.append(journalButton);
      const name=tr.querySelector('.account-name strong');name.tabIndex=0;name.setAttribute('role','button');name.addEventListener('click',()=>showDetails(r));name.addEventListener('keydown',e=>{if(e.key==='Enter')showDetails(r);});$('accounts').append(tr);
    }
    $('empty').hidden=Boolean(rows.length);$('empty-title').textContent='Aucun compte dans cette catégorie';$('empty-description').textContent='Les autres comptes restent disponibles dans les onglets. Aucun membre n’a été supprimé.';$('empty-add').hidden=true;
    $('count').textContent=rows.length+' compte(s) affiché(s) sur '+all.length+' · maître exclu des compteurs';
    $('events').replaceChildren();for(const e of state.events){const line=el('div',undefined,'event');line.append(el('span',(actions[e.action]||'Action administrative')+' · '+(e.target==='master'?'Compte maître':e.target)),el('time',date(e.at)));$('events').append(line);}
    if(!state.events.length)$('events').append(el('p','Aucune action enregistrée.','activity-empty'));
    applyColumns();$('export').disabled=!rows.length;$('page-note').textContent=view==='activity'?'':'ON indique le choix du membre. La copie exige aussi une connexion et une autorisation valides. État actualisé automatiquement. P&L nets de frais et swaps, hors dépôts, retraits et crédits. Jour, semaine (lundi) et mois en heure de Paris. Équité % = P&L ouvert / solde. Évolution % = P&L réalisé / dépôts nets, si réconcilié. Total limité à l’historique disponible. Relevés MT5 toutes les 2 s environ, historique toutes les 5 s ; « ancien » au-delà de 15 s.';
  }
  async function load(){if(loading)return;loading=true;$('refresh').disabled=true;try{state=await api('');render();if($('details').open){const current=state.followers.find(r=>r.code===selectedCode);$('detail-engine-state').textContent=current?copyLabel(current):'État du compte indisponible';if(current){syncDetailStatus(current);renderPositions(current);renderFinancials(current);}}$('updated').textContent='Actualisé à '+new Date().toLocaleTimeString('fr-FR',{timeZone:'Europe/Paris',hour:'2-digit',minute:'2-digit'});}catch(e){$('detail-engine-state').textContent='État du moteur indisponible';message(e.message);state=null;$('account-tabs').replaceChildren();$('scope-description').textContent='État indisponible : aucune connexion ne peut être confirmée.';$('accounts').replaceChildren();$('empty').hidden=false;$('empty-title').textContent='Comptes indisponibles';$('empty-description').textContent='Réessayez dans quelques instants.';$('empty-add').hidden=true;$('updated').textContent='Actualisation interrompue';$('count').textContent='—';$('configure-master').disabled=true;}finally{loading=false;$('refresh').disabled=false;if(state)$('configure-master').disabled=false;}}
  document.querySelector('.table-scroll thead tr').replaceChildren(...columns.map(label=>el('th',label)));
  columns.forEach((label,index)=>{if(index===0||index===columns.length-1)return;const line=el('label'),input=el('input');input.type='checkbox';input.checked=!hiddenColumns.has(index);input.addEventListener('change',()=>{input.checked?hiddenColumns.delete(index):hiddenColumns.add(index);applyColumns();});line.append(input,document.createTextNode(label));$('column-options').append(line);});
  $('export').addEventListener('click',exportAccounts);
  $('master-form').addEventListener('submit',async event=>{event.preventDefault();if(busy||preview)return;busy=true;event.submitter.disabled=true;const data=Object.fromEntries(new FormData(event.target));data.revision=Number(event.target.dataset.revision||0);try{await api('/master',data);event.target.elements.password.value='';$('master-dialog').close();message('Compte maître enregistré. La connexion reste à vérifier.');await load();}catch(e){message(e.message,'form-message');}finally{busy=false;event.submitter.disabled=false;}});
  document.querySelectorAll('[data-view]').forEach(button=>button.addEventListener('click',()=>{view=button.dataset.view;document.querySelectorAll('[data-view]').forEach(b=>b.classList.toggle('active',b===button));$('search').value='';$('filter').value='ALL';render();}));
  document.querySelectorAll('[data-close]').forEach(b=>b.addEventListener('click',()=>$(b.dataset.close).close()));$('configure-master').addEventListener('click',showMaster);$('empty-add').addEventListener('click',showMaster);$('search').addEventListener('input',render);$('filter').addEventListener('change',render);$('refresh').addEventListener('click',load);load();let refreshTick=0;setInterval(()=>{refreshTick++;if(!document.hidden&&!busy&&($('details').open||refreshTick%2===0))load();},1000);
})();
