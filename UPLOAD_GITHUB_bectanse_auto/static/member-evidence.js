/* Private administrator evidence view. Render untrusted data only as text. */
async function loadMemberEvidence(code, before = 0) {
  const root = document.getElementById('member-evidence');
  if (!root) return;
  root.dataset.code = code;
  root.replaceChildren();
  const add = (tag, text, parent = root) => {
    const node = document.createElement(tag); node.textContent = text; parent.append(node); return node;
  };
  add('h3', 'Traçabilité du profil');
  const status = add('p', 'Chargement…');
  try {
    const params = new URLSearchParams({code, before: String(before)});
    const response = await fetch('/admin/api/membre/tracabilite?' + params, {cache:'no-store'});
    const data = await response.json();
    if (!response.ok || !data.ok) throw Error('unavailable');
    if (!root.isConnected || root.dataset.code !== code) return;
    status.textContent = data.notice;
    add('p', 'E-mail actuel : ' + (data.email || 'Non renseigné'));
    const exportLink = add('a', 'Exporter ce dossier (JSON)');
    exportLink.href = '/admin/api/membre/tracabilite?' + new URLSearchParams({code, before:String(before), export:'1'});
    exportLink.className = 'btn btn-sm aw-secondary';
    add('p','Export limité à 1 000 événements par page ; les paiements sont présentés séparément.');
    const payments = add('details', ''); add('summary', 'Paiements et achats confirmés ('+(data.payments.length+data.credit_purchases.length)+')', payments);
    for (const payment of [...data.payments, ...data.credit_purchases]) {
      const pre=add('pre', JSON.stringify(payment,null,2),payments); pre.style.cssText='white-space:pre-wrap;overflow-wrap:anywhere;font-size:11px';
    }
    if (data.payments_truncated || data.credit_purchases_truncated) add('p','Les 100 dernières opérations de chaque catégorie sont affichées. Consulter la facturation pour les opérations plus anciennes.',payments);
    if (!data.events.length) add('p', 'Aucun événement technique enregistré pour cette période.');
    for (const event of data.events) {
      const item = add('details',''); item.style.cssText='margin-top:12px;border-top:1px solid #555;padding-top:8px';
      add('summary',new Date(event.at).toLocaleString('fr-FR',{timeZone:'Europe/Paris'})+' · '+event.label,item);
      if (event.email) add('p','E-mail lors de l’événement : '+event.email,item);
      if (event.network.peer_ip) add('p','IP du pair réseau observé (peut être un proxy) : '+event.network.peer_ip,item);
      if (event.network.forwarded_ips_unverified?.length) add('p','IP transmises par en-tête, non vérifiées : '+event.network.forwarded_ips_unverified.join(', '),item);
      if (event.network.user_agent_declared) add('p','Navigateur déclaré : '+event.network.user_agent_declared,item);
      const detail=add('pre',JSON.stringify(event.details,null,2),item); detail.style.cssText='white-space:pre-wrap;overflow-wrap:anywhere;font-size:11px';
      add('small','Source : '+event.source+' · événement '+event.id,item);
    }
    const button=(text, cursor)=>{const b=add('button',text);b.type='button';b.className='btn btn-sm aw-secondary';b.addEventListener('click',()=>loadMemberEvidence(code,cursor));};
    if(data.next_before) button('Événements plus anciens',data.next_before);
    if(before) button('Revenir aux plus récents',0);
  } catch (_) {
    status.textContent='Historique indisponible. Aucun événement ne peut être confirmé pour le moment.';
    const retry=add('button','Réessayer');retry.type='button';retry.addEventListener('click',()=>loadMemberEvidence(code,before));
  }
}
