(() => {
  const offer = document.querySelector('[data-renewal-offer]');
  if (!offer) return;
  const deadline = Date.parse(offer.dataset.deadline);
  const endOffer = () => {
    offer.classList.add('is-ended');
    document.querySelectorAll('[data-renewal-plan]').forEach(a => { a.href='/abonnement/checkout/'+a.dataset.renewalPlan; });
    document.querySelectorAll('[data-standard-price]').forEach(p => {p.textContent=p.dataset.standardPrice;});
    offer.querySelector('.renewal-ended').hidden = false;
    offer.querySelector('h2').textContent = 'Retrouve ton espace membre';
    offer.querySelector('.renewal-window').textContent = 'Offre terminée';
    offer.querySelector('h2 + p').hidden = true;
    offer.querySelector('small').hidden = true;
  };
  if (!Number.isFinite(deadline)) { offer.hidden = true; return; }
  offer.querySelector('[data-deadline-label]').textContent = new Intl.DateTimeFormat('fr-FR', {
    day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Paris'
  }).format(deadline) + ' (heure de Paris)';
  let timer;
  const tick = () => {
    const remaining = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
    if (!remaining) { endOffer(); clearInterval(timer); return; }
    for (const [key, value] of Object.entries({hours:Math.floor(remaining / 3600), minutes:Math.floor(remaining / 60) % 60, seconds:remaining % 60})) {
      offer.querySelector('[data-' + key + ']').textContent = String(value).padStart(2, '0');
    }
  };
  tick();
  if (Date.now() < deadline) timer = setInterval(tick, 1000);
  document.addEventListener('visibilitychange', tick);
  offer.querySelector('[data-copy-renewal]').addEventListener('click', async () => {
    if (Date.now() >= deadline) { tick(); return; }
    const status = offer.querySelector('.renewal-copy-status');
    try { await navigator.clipboard.writeText('RETOUR100'); status.textContent = 'Code copié'; }
    catch (_) { status.textContent = 'Ton code à saisir : RETOUR100'; }
  });
})();
