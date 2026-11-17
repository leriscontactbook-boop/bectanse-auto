(() => {
  'use strict';
  if (document.querySelector('[data-autumn-campaign]')) return;
  fetch('/api/academy-campaign', {cache: 'no-store'})
    .then(r => {if (!r.ok) throw Error('unavailable'); return r.json();})
    .then(campaign => {
      if (!campaign.active) return;
      const started = performance.now();
      const deadline = Number(campaign.ends_at) * 1000;
      const serverStart = Number(campaign.server_now) * 1000;
      if (!Number.isFinite(deadline) || !Number.isFinite(serverStart)) return;
      const nodes = [], originals = [], links = [];
      let observer;
      const prices = { '1_month': [500, 300, 'mois'], '3_months': [1000, 800, '3 mois'], '1_year': [4000, 3800, 'an'] };
      const money = n => n.toLocaleString('fr-FR') + ' €';
      const codeButton = '<button type="button" class="autumn-code" data-copy-autumn aria-label="Copier le code AUTOMNE200">AUTOMNE200 <span>Copier</span></button>';
      const timer = '<span class="autumn-countdown" role="timer" aria-label="Temps restant avant la fin de l’offre"></span>';
      const banner = document.createElement('aside');
      banner.className = 'autumn-banner'; banner.dataset.autumnCampaign = 'banner';
      banner.setAttribute('aria-label', 'Offre spéciale Académie');
      banner.innerHTML = '<button type="button" class="autumn-close" aria-label="Masquer la promotion">×</button><div><strong>200 € offerts sur toutes les formules Académie</strong><small>Premier paiement · Fin ce soir à minuit, Paris</small></div><div class="autumn-banner-actions">'+codeButton+timer+'<a class="autumn-cta" href="/vip#offres">Voir les offres −200 € </a></div>';
      document.body.append(banner); nodes.push(banner);
      banner.querySelector('.autumn-close').addEventListener('click', () => banner.remove());
      if (location.pathname.replace(/\/$/, '') === '/vip') {
        const hero = document.createElement('section');
        hero.className = 'autumn-hero'; hero.dataset.autumnCampaign = 'hero';
        hero.innerHTML = '<div class="autumn-hero-copy"><p class="autumn-eyebrow">L’offre spéciale de l’automne</p><h2>Ton prochain chapitre.<br><em>200 € de moins pour commencer.</em></h2><p>Tous les accès de l’Académie, quelle que soit ta formule : formation, Journal, Trader Lab, Bectanse Auto et Canal VIP.</p><div class="autumn-hero-actions"><a class="autumn-cta" href="#offres">Je profite des 200 € offerts </a>'+codeButton+'</div><p class="autumn-deadline">Jusqu’à ce soir minuit · Heure de Paris</p>'+timer+'<p class="autumn-terms">200 € de réduction sur le premier paiement. Puis tarif standard : 500 €/mois, 1 000 €/3 mois ou 4 000 €/an, selon la formule. Renouvellement automatique jusqu’à résiliation. Non cumulable avec un autre code.</p></div><div class="autumn-ticket" aria-label="200 euros offerts avec AUTOMNE200"><span>BECTANSE ACADÉMIE</span><strong>200<span>€</span></strong><b>OFFERTS</b><p>SUR TON PREMIER PAIEMENT</p><div>AUTOMNE200</div><small>1 MOIS · 3 MOIS · 1 AN</small></div>';
        const main = document.querySelector('main');
        const placement = document.querySelector('#capture');
        if (main) { if (placement) placement.before(hero); else main.prepend(hero); nodes.push(hero); }
        if (main && !document.getElementById('offres')) {
          const offers = document.createElement('section'); offers.id = 'offres'; offers.className = 'autumn-offers';
          offers.innerHTML = '<h2>Choisis ta formule · 200 € offerts</h2><div class="autumn-offer-grid">' + Object.entries(prices).map(([id,p]) => '<article><h3>'+({'1_month':'1 mois','3_months':'3 mois','1_year':'1 an'}[id])+'</h3><p><s>'+money(p[0])+'</s> <strong>'+money(p[1])+'</strong></p><p>Premier paiement avec AUTOMNE200</p><a class="autumn-cta" href="/abonnement/checkout/'+id+'">Choisir cette formule →</a><small>Puis '+money(p[0])+' / '+p[2]+' · renouvellement automatique jusqu’à résiliation.</small></article>').join('')+'</div>';
          hero.after(offers); nodes.push(offers);
        }
        document.querySelectorAll(placement ? '.mobile-sticky a' : '.mobile-sticky a, .nav-actions .btn').forEach(link => {
          links.push([link, link.getAttribute('href'), link.innerHTML]);
          link.href = '#offres'; link.textContent = 'Profiter des 200 € offerts';
        });
        if ('IntersectionObserver' in window) {
          const visible = new Set();
          observer = new IntersectionObserver(entries => {
            entries.forEach(e => e.isIntersecting ? visible.add(e.target) : visible.delete(e.target));
            banner.hidden = visible.size > 0;
          });
          observer.observe(hero);
          document.querySelectorAll('.founder-first, .journey-app').forEach(section => observer.observe(section));
          const offers = document.querySelector('#offres'); if (offers) observer.observe(offers);
        }
      }
      document.querySelectorAll('[data-plan]').forEach(card => {
        const p = prices[card.dataset.plan]; const price = card.querySelector('.price');
        if (!p || !price) return;
        originals.push([price, price.innerHTML]);
        price.innerHTML = '<s class="autumn-old">'+money(p[0])+'</s> '+money(p[1]);
        const note = document.createElement('p'); note.className = 'autumn-price-note';
        note.innerHTML = 'Réduction <strong>AUTOMNE200</strong> appliquée automatiquement';
        price.after(note); nodes.push(note);
        const per = card.querySelector('.per');
        if (per) {originals.push([per, per.innerHTML]); per.textContent = 'Première période : '+money(p[1])+'. Puis '+money(p[0])+' / '+p[2]+' · renouvellement automatique';}
      });
      const from = document.querySelector('.value-total > div:last-child strong');
      if (from) {originals.push([from, from.innerHTML]); from.textContent = '300 € avec AUTOMNE200';}
      const modal = document.createElement('dialog');
      modal.className = 'autumn-modal'; modal.dataset.autumnCampaign = 'popup';
      modal.setAttribute('aria-labelledby', 'autumn-popup-title');
      modal.innerHTML = '<button class="autumn-close" type="button" aria-label="Fermer l’offre">×</button><p class="autumn-eyebrow">BECTANSE ACADÉMIE · OFFRE SPÉCIALE</p><h2 id="autumn-popup-title">200 € <em>OFFERTS</em></h2><p>Sur ton premier paiement.<br>Toutes les formules Académie.</p><div class="autumn-popup-code">'+codeButton+'</div><p class="autumn-deadline">Jusqu’à ce soir minuit · Heure de Paris</p>'+timer+'<a class="autumn-cta" href="/vip#offres">Je profite des 200 € offerts →</a><small>Réduction appliquée automatiquement au paiement.<br>Non cumulable avec un autre code.</small>';
      document.body.append(modal); nodes.push(modal);
      const seenKey = 'academy-promo-seen-' + campaign.ends_at;
      const closeModal = () => { if (modal.open) modal.close(); };
      modal.querySelector('.autumn-close').addEventListener('click', closeModal);
      modal.addEventListener('click', e => { if (e.target === modal) { const r = modal.getBoundingClientRect(); if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) closeModal(); } });
      modal.querySelector('a').addEventListener('click', () => { closeModal(); if (location.pathname.replace(/\/$/, '') === '/vip') modal.querySelector('a').href = '#offres'; });
      let seen = false; try { seen = sessionStorage.getItem(seenKey) === '1'; } catch (_) {}
      if (!seen && typeof modal.showModal === 'function') { modal.showModal(); try { sessionStorage.setItem(seenKey, '1'); } catch (_) {} }
      document.querySelectorAll('[data-copy-autumn]').forEach(button => button.addEventListener('click', async () => {
        try {await navigator.clipboard.writeText('AUTOMNE200'); button.querySelector('span').textContent = 'Copié ';}
        catch (_) {button.querySelector('span').textContent = 'À saisir au paiement';}
      }));
      let interval;
      function tick() {
        const left = Math.max(0, Math.ceil((deadline - serverStart - (performance.now() - started)) / 1000));
        if (!left) {
          closeModal(); nodes.forEach(n => n.remove()); originals.forEach(([n, html]) => {n.innerHTML = html;});
          links.forEach(([n, href, html]) => {n.setAttribute('href', href); n.innerHTML = html;});
          if (observer) observer.disconnect();
          clearInterval(interval); return;
        }
        const d = Math.floor(left / 86400), h = Math.floor(left / 3600) % 24;
        const m = Math.floor(left / 60) % 60, s = left % 60;
        document.querySelectorAll('.autumn-countdown').forEach(n => {n.textContent = `${d}j ${String(h).padStart(2,'0')}h ${String(m).padStart(2,'0')}m ${String(s).padStart(2,'0')}s`;});
      }
      interval = setInterval(tick, 1000); tick();
    }).catch(() => {});
})();
