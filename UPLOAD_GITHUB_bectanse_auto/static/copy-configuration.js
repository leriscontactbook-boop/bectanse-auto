/* Member consent is explicit; active status requires the executor acknowledgement. */
(() => {
  const select = document.getElementById('copy-account-id');
  const status = document.getElementById('copy-configuration-status');
  if (!select || !status) return;
  let initialized = false;
  let busy = false;
  const errorLabels = {
    SOURCE_STALE: 'Compte maître indisponible ou données trop anciennes',
    CONFIGURATION_UNAVAILABLE: 'Réglages non reçus : nouvelles entrées suspendues',
    EXECUTION_UNCERTAIN: 'Exécution à vérifier chez le broker : aucun renvoi automatique',
    UNRESOLVED_EXECUTION: 'Une exécution précédente reste à confirmer',
    UNSUPPORTED_LOT_MODE: 'Choisir Lots fixes ou Lot par symbole pour ce pilote',
    STOP_LOSS_REQUIRED: 'Un stop-loss valide et sa copie sont obligatoires',
    ENTRIES_PAUSED: 'Nouvelles entrées en pause',
    ORDER_CHECK_REJECTED: 'Ordre refusé lors de la vérification préalable',
    VOLUME_BELOW_MINIMUM: 'Lot inférieur au minimum broker : copie ignorée',
    SYMBOL_NOT_MAPPED: 'Actif non associé au compte destinataire',
    INCOMPATIBLE_SYMBOL: 'Caractéristiques de l’actif incompatibles',
    DEDICATED_ACCOUNT_REQUIRED: 'Le compte destinataire doit être réservé au pilote',
    EXPOSURE_LIMIT: 'Plafond de positions ou de lots atteint',
    ENTRY_EXPIRED: 'Entrée trop ancienne : copie ignorée',
    MASTER_PRICE_DEVIATION: 'Écart de prix avec le maître trop important',
    SPREAD_LIMIT: 'Spread supérieur au plafond',
    BASELINE_ESTABLISHED: 'Démarrage terminé : en attente de nouvelles positions du maître',
  };
  const explain = code => errorLabels[code] || (code.startsWith('UNSUPPORTED_OPTION_')
    ? 'Une option non prise en charge est activée : vérifier les réglages du pilote'
    : `Vérification nécessaire (${code})`);
  window.refreshCopyConfiguration = async () => {
    if (busy) return;
    busy = true;
    try {
      const response = await fetch('/api/copy/configuration', {credentials: 'same-origin', cache: 'no-store'});
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error('unavailable');
      const current = data.current;
      const pilot = data.pilot;
      const selected = initialized ? select.value : String(current?.account_id || '');
      select.replaceChildren(new Option('Sélectionner un compte MT5', ''));
      for (const account of data.accounts) {
        select.add(new Option(`${account.name || 'MT5'} · ${account.login_masked} · ${account.server}`, account.id));
      }
      select.value = selected;
      select.disabled = data.accounts.length === 0;
      initialized = true;
      const labels = {
        PENDING: 'En attente de validation en simulation',
        VALIDATED_SIMULATION: 'Paramètres validés en simulation',
        FAILED: 'Échec de validation — enregistre une nouvelle demande',
        SUPERSEDED: 'Configuration remplacée',
      };
      status.textContent = current && pilot?.authorized
        ? `Version ${current.version} · demande ${current.requested_enabled ? 'activée' : 'en pause'} · pilote démo uniquement.`
        : current
        ? `Version ${current.version} · ${labels[current.status] || 'Statut inconnu'} · demande ${current.requested_enabled ? 'activée' : 'en pause'}. Aucun ordre envoyé.`
        : 'Choisis un compte et enregistre tes paramètres pour démarrer la validation.';
      if(data.preparation)status.textContent='Préparation : '+({PREPARING:'en cours',BLOCKED:'bloquée',UNASSIGNED:'aucun maître associé',ERROR:'vérification indisponible'}[data.preparation.state]||'à vérifier')+'. '+(data.preparation.message||'Aucune copie réelle active.');
      let controls=document.getElementById('copy-member-preparation');
      if(!controls){controls=document.createElement('div');controls.id='copy-member-preparation';status.after(controls);}
      controls.replaceChildren();
      if(data.preparation){
        const summary=document.createElement('p');
        summary.textContent=current?.assigned?'Service de copie configuré.':'Configuration du service de copie en attente.';
        controls.append(summary);
        const stateResponse=await fetch('/api/copy/execution',{credentials:'same-origin',cache:'no-store'});
        if(!stateResponse.ok)throw new Error('État du moteur indisponible.');
        const execution=await stateResponse.json();
        const stateLabels={AUTHORIZATION_ACCEPTED:'Activation reçue. Démarrage automatique en cours.',STARTING:'Démarrage en cours',REQUEST_RECEIVED:'Activation reçue. Démarrage automatique en cours.',ACTIVE:'Copie active',PAUSED:'En pause',STOPPED:'Arrêtée',UNCONFIRMED:'Synchronisation automatique en cours'};
        status.textContent=stateLabels[execution.confirmed]||'État à vérifier';
        if(execution.confirmed==='ACTIVE'&&!execution.execution_enabled)status.textContent='Synchronisation automatique en cours.';
        const messages={OPERATIONAL_RELEASE_REQUIRED:'Les vérifications automatiques des instruments sont en cours.',TERMINAL_EXECUTION_NOT_READY:'Le terminal ne permet pas encore l’exécution.',CONNECTION_EVIDENCE_STALE_OR_CHANGED:'La connexion doit être confirmée à nouveau.',UNSUPPORTED_OPTION_COPIER_ORDRES_EN_ATTENTE:'La copie des ordres en attente n’est pas encore prise en charge.',UNSUPPORTED_OPTION_OBJECTIF_ACTIF:'La protection par objectif doit être prise en charge avant l’activation.'};
        const actionButtons={};
        for(const [action,label] of [[data.onboarding?.can_resume?'resume':'activate','ON'],['pause','OFF']]){
          const button=document.createElement('button');button.type='button';button.textContent=label;button.disabled=!current;
          button.addEventListener('click',async()=>{
            const command={action,version:current.version,request_id:crypto.randomUUID()};
            // Keep the same id for a retry after a lost response.
            button.disabled=true;
            try{
              let response;
              try{response=await fetch('/api/copy/activation',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':data.csrf},body:JSON.stringify(command)});}
              catch(_){response=await fetch('/api/copy/activation',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':data.csrf},body:JSON.stringify(command)});}
              const result=await response.json();
              if(!response.ok||!result.ok)throw new Error(messages[result.error]||result.error||'Commande non confirmée.');
              summary.textContent=action==='pause'?'OFF enregistré. Nouvelles entrées arrêtées ; suivi et clôture des copies existantes maintenus.':'ON enregistré. Le moteur démarre automatiquement.';
            }catch(e){summary.textContent=e.message;}
            finally{button.disabled=false;}
          });
          actionButtons[label]=button;
          controls.append(button);
        }
        const nextButton=actionButtons[execution.desired==='ACTIVE'?'OFF':'ON'];
        window.requestMemberCopyToggle=()=>{if(nextButton&&!nextButton.disabled)nextButton.click();};
        for(const legacy of document.querySelectorAll('#copyToggleBtn,.copy-content-btn')){
          legacy.textContent=execution.desired==='ACTIVE'?'OFF — arrêter les nouvelles copies':'ON — activer la copie';
          legacy.disabled=!nextButton||nextButton.disabled;
          legacy.onclick=()=>window.requestMemberCopyToggle();
        }
      }
      if(data.onboarding)status.textContent=data.onboarding.message;
      const runtime = document.getElementById('copy-pilot-runtime');
      if (runtime) {
        runtime.replaceChildren();
        if(data.real_connection){
          const real=data.real_connection, stats=real.statistics||{};
          const line=document.createElement('p');
          const money=value=>value===null||value===undefined?'Indisponible':String(value)+' '+(stats.currency||'');
          line.textContent=(real.connected?'Compte MT5 connecté':'Données MT5 périmées')+' · Solde : '+money(stats.balance)+' · Équité : '+money(stats.equity)+' · Résultat ouvert : '+money(stats.unrealized_profit)+' · Résultat réalisé sur la période collectée : '+money(stats.realized_net_in_window);
          runtime.append(line);
          const at=document.createElement('p');at.textContent='Dernière observation : '+new Date(real.observed_at*1000).toLocaleString('fr-FR');runtime.append(at);
          for(const position of real.positions||[]){const row=document.createElement('p');row.textContent=[position.symbol,position.side,position.volume!==undefined?position.volume+' lot(s)':''].filter(Boolean).join(' · ');runtime.append(row);}
        } else if (pilot?.authorized || pilot?.report) {
          const report = pilot.report;
          const summary = document.createElement('p');
          summary.textContent = !pilot.online ? 'Moteur démo hors ligne ou statut périmé : le suivi des positions n’est pas confirmé.'
            : `Moteur démo connecté · version reçue ${report.version} · ${report.open_positions} position(s) · ${report.pending_commands} commande(s) à confirmer.`;
          runtime.append(summary);
          if (report?.errors?.length) {
            const errors = document.createElement('p');
            errors.textContent = report.errors.map(explain).join(' · ');
            runtime.append(errors);
          }
          for (const position of report?.positions || []) {
            const line = document.createElement('p');
            const states = {OPEN: 'Ouverte', OPENING: 'Ouverture à confirmer', CLOSED: 'Fermée', CLOSED_LOCAL: 'Fermée sur le compte destinataire', SKIPPED: 'Non copiée'};
            line.textContent = `${position.symbol || 'Position'} · ${states[position.state] || position.state} · ${position.error ? explain(position.error) : 'suivi enregistré'}`;
            runtime.append(line);
          }
        }
      }
    } catch (_) {
      status.textContent = 'Statut indisponible. La validation de tes paramètres n’est pas confirmée.';
      const runtime = document.getElementById('copy-pilot-runtime');
      if (runtime) runtime.textContent = 'Suivi du moteur indisponible : son état actuel n’est pas confirmé.';
    } finally {
      busy = false;
    }
  };
  window.refreshCopyConfiguration();
  setInterval(() => {
    if (!document.hidden) window.refreshCopyConfiguration();
  }, 10000);
})();
