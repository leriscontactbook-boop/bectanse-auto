(() => {
  const run=document.getElementById('ca-run'),toggle=document.getElementById('ca-toggle'),feedback=document.getElementById('ca-feedback');
  async function command(path,body){
    run.disabled=toggle.disabled=true;
    feedback.textContent='Traitement en cours…';
    try{
      const r=await fetch('/admin/api/conversion-agent/'+path,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':document.body.dataset.csrf},body:JSON.stringify(body)});
      const d=await r.json();if(!r.ok||!d.ok)throw Error(d.error||'Opération indisponible.');
      if(d.busy){feedback.textContent='Une analyse est déjà en cours. Actualisez dans un instant.';return;}
      if(d.already_done){feedback.textContent='Le rapport du jour existe déjà. Une seconde exécution n’ajoute pas de relance.';return;}
      if(d.paused){feedback.textContent='L’agent est en pause. Réactivez-le pour lancer une analyse.';return;}
      location.href='/admin/conversion-agent'+(d.report_id?'?report='+encodeURIComponent(d.report_id):'');
    }catch(e){feedback.textContent=e.message||'La réponse du serveur est indisponible. Vérifiez le rapport avant de réessayer.';}
    finally{run.disabled=toggle.disabled=false;}
  }
  run.addEventListener('click',async()=>{if(await window.adminConfirm('Lancer l’analyse du jour ?','L’agent prépare les relances justifiées et les transmet au moteur Brevo, selon ses horaires et plafonds. Un seul rapport est produit par jour.'))command('run',{});});
  toggle.addEventListener('click',async()=>{const enabled=toggle.dataset.enabled!=='true';if(await window.adminConfirm(enabled?'Réactiver l’agent ?':'Mettre l’agent en pause ?',enabled?'Les analyses quotidiennes et les relances admissibles reprennent.':'Les actions en attente de cet agent sont suspendues. Les autres parcours marketing restent inchangés.'))command('settings',{enabled});});
})();
