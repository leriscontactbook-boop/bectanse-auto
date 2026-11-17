/* Navigation adapter for isolated renders of the production member templates. */
(() => {
  const emit = (type, path) => parent.postMessage({source: 'bectanse-mobile-tour', type, path}, '*');
  const sheet = document.getElementById('mAppSheet');
  const backdrop = document.querySelector('.m-app-sheet-backdrop');
  const trigger = document.querySelector('.m-app-more');
  function toggleMenu(open) {
    sheet?.classList.toggle('is-open', open);
    backdrop?.classList.toggle('is-open', open);
    sheet?.setAttribute('aria-hidden', String(!open));
    trigger?.setAttribute('aria-expanded', String(open));
  }
  document.addEventListener('click', event => {
    if (event.target.closest('.m-app-more')) { toggleMenu(true); return; }
    if (event.target.closest('[data-m-app-close],.m-app-sheet-backdrop')) { toggleMenu(false); return; }
    const link = event.target.closest('a');
    if (link) {
      const url = new URL(link.getAttribute('href'), document.baseURI);
      if (url.origin === 'https://www.tradingview.com') return;
      event.preventDefault();
      emit('navigate', url.pathname);
      return;
    }
    if (event.target.closest('button')) emit('restricted');
  });
  document.addEventListener('keydown', event => { if (event.key === 'Escape') toggleMenu(false); });
  document.addEventListener('submit', event => event.preventDefault());
  // Public demonstration content only; never reads member messages or account APIs.
  const market = document.getElementById('market-card');
  if (market) {
    document.getElementById('market-text').textContent = 'Or · XAUUSD';
    document.getElementById('market-detail').textContent = 'Graphique TradingView · cotations du fournisseur';
    const holder = document.getElementById('tradingview_gold');
    holder.className = 'tradingview-widget-container';
    holder.innerHTML = '<div class="tradingview-widget-container__widget"></div><div class="tradingview-widget-copyright">Graphique XAUUSD par TradingView</div>';
    const chart = document.createElement("script");
    chart.src = "https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js";
    chart.async = true;
    chart.textContent = JSON.stringify({autosize:true,symbol:"OANDA:XAUUSD",interval:"60",timezone:"Europe/Paris",theme:"dark",style:"1",locale:"fr",allow_symbol_change:false,hide_top_toolbar:false,hide_side_toolbar:true,hide_volume:true,save_image:false,details:false,withdateranges:false});
    chart.onerror = () => { holder.querySelector(".tradingview-widget-container__widget").textContent = "Graphique indisponible — réessaie en rechargeant la page"; };
    holder.appendChild(chart);
    market.querySelector('.os-chart-toggle')?.remove();
    const panel = document.querySelector('.os-explorer-lock-panel');
    if (panel) {
      const section = panel.closest('section');
      section.querySelector('.os-panel-title-row p').textContent = 'Exemples de publications sur l’or';
      section.querySelector('.os-panel-title-row > strong').textContent = 'DÉMO';
      panel.outerHTML = `<div class="tour-signals"><p class="tour-demo-note">Signaux fictifs · illustration de l’interface, à ne pas exécuter</p>
        <article class="tour-signal"><header><strong>XAUUSD <span>Achat</span></strong><small>Exemple · 09:15</small></header><p>Zone de travail sur l’or</p><div class="tour-levels"><span>Entrée<b>2 340 – 2 342</b></span><span>Stop loss<b>2 332</b></span><span>Objectif 1<b>2 352</b></span><span>Objectif 2<b>2 362</b></span></div><footer>Scénario d’exemple · niveaux fictifs</footer></article>
        <article class="tour-signal"><header><strong>Suivi de position</strong><small>Exemple · 10:05</small></header><p>Premier objectif atteint dans cet exemple. Le suivi précise les ajustements du scénario et rappelle la gestion du risque.</p><span class="tour-status">Mise à jour du scénario</span></article></div>`;
    }
    const eco = document.getElementById('eco-list');
    if (eco) {
      const section=eco.closest('section');
      section.querySelector('h2').textContent='Calendrier économique';
      section.querySelector('.os-panel-title-row p').textContent='Exemple de journée · horaires fictifs, heure de Paris';
      eco.innerHTML='<p class="tour-demo-note">Démonstration · ces annonces ne sont pas celles du jour</p>'+[
        ['14:30','USD','Inflation américaine · IPC','Impact élevé'],
        ['16:00','USD','Indice de confiance des consommateurs','Impact modéré'],
        ['20:00','USD','Décision de taux de la Fed','Impact élevé']
      ].map(([time,currency,title,impact])=>`<article class="tour-event"><time>${time}</time><div><small>${currency} · ${impact}</small><strong>${title}</strong></div></article>`).join('');
    }
  }

  emit('ready', location.pathname);
})();
