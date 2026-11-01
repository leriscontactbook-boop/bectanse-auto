/* Real member templates, rendered without account data in a sandboxed mobile viewport. */
(() => {
  if (customElements.get('academy-preview')) return;
  const screens = [
    ['accueil', 'Accueil', 'Ton espace, au même endroit'],
    ['academie', 'Académie', 'Un parcours pour construire ta méthode'],
    ['trader-lab', 'Trader Lab', 'Les outils derrière tes décisions'],
    ['live', 'Live', 'Les analyses et messages de l’équipe'],
    ['faq', 'FAQ', 'Les réponses à tes questions'],
    ['journal', 'Journal', 'Le suivi de ta pratique']
  ];
  class AcademyPreview extends HTMLElement {
    connectedCallback() {
      if (this.shadowRoot) return;
      const root = this.attachShadow({mode:'open'});
      root.innerHTML = `<link rel="stylesheet" href="/static/css/academy-preview.css?v=3">
        <section class="tour" aria-label="Découvrir la véritable application sur mobile">
          <div class="stage"><div class="halo" aria-hidden="true"></div><div class="orbit" aria-hidden="true"></div>
            <div class="floating-label"><span></span>La web app Bectanse</div>
            <div class="phone" aria-label="Aperçu dans un iPhone 17 Pro Max"><span class="side-buttons" aria-hidden="true"></span><div class="hardware" aria-hidden="true"><span class="clock">9:41</span><i class="dynamic-island"></i><span class="status-icons"><svg viewBox="0 0 18 12"><path d="M1 8h2v3H1zM5 6h2v5H5zM9 3h2v8H9zM13 0h2v11h-2z" fill="currentColor"/></svg><svg viewBox="0 0 16 12" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><path d="M1 3c4-3 10-3 14 0M4 6c2-2 6-2 8 0M7 9h2"/></svg><svg class="battery" viewBox="0 0 27 13" fill="none"><rect x="1" y="1" width="22" height="11" rx="3" stroke="currentColor"/><path d="M25 4v5" stroke="currentColor" stroke-width="2"/><rect x="3" y="3" width="18" height="7" rx="1" fill="currentColor"/></svg></span></div>
              <div class="viewport"><iframe title="Application Bectanse — Accueil" sandbox="allow-scripts allow-same-origin" loading="lazy" referrerpolicy="no-referrer"></iframe></div>
              <div class="home-bar" aria-hidden="true"></div>
            </div>
            <div class="touch-note">Clique et explore</div>
          </div>
          <div class="controls"><p class="caption" aria-live="polite">Ton espace, au même endroit</p>
            <nav aria-label="Espaces de l’application">${screens.map(([key,label],i)=>`<button type="button" data-screen="${key}" aria-pressed="${i===0}">${label}</button>`).join('')}</nav>
            
            <p class="notice" role="status"></p>
            <a class="open">Entrer dans l’Académie</a>
          </div>
        </section>`;
      this.frame = root.querySelector('iframe');
      if (this.hasAttribute('scroll-through')) {
        const style = document.createElement('style');
        style.textContent = '@media (pointer: coarse), (max-width: 800px) { .viewport { touch-action: pan-y pinch-zoom; } iframe { pointer-events: none; } }';
        root.append(style);
        if (matchMedia('(pointer: coarse)').matches) this.frame.tabIndex = -1;
        root.querySelector('.touch-note').textContent = 'Choisis un espace ci-dessous';
      }
      root.querySelector('.open').href = this.getAttribute('explore-href') || 'https://acces.bectanse-academie.com/vip#capture';
      this.current = '';
      this.show('accueil');
      root.querySelectorAll('[data-screen]').forEach(button => button.addEventListener('click', () => this.show(button.dataset.screen)));
      this.onMessage = event => {
        if (event.source !== this.frame.contentWindow || event.data?.source !== 'bectanse-mobile-tour') return;
        if (event.data.type === 'navigate') {
          const key = String(event.data.path).replace(/^\//,'');
          if (screens.some(([id])=>id===key)) this.show(key);
          else this.restricted();
        } else if (event.data.type === 'restricted') this.restricted();
      };
      window.addEventListener('message', this.onMessage);
      this.visibilityObserver = new IntersectionObserver(([entry]) => {
        document.body.classList.toggle('academy-tour-in-view', entry.isIntersecting);
      }, {threshold: .15});
      this.visibilityObserver.observe(root.querySelector('.tour'));
      root.querySelectorAll('button').forEach((button,i,all)=>button.addEventListener('keydown',event=>{
        if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
        event.preventDefault();
        const next = event.key==='Home'?0:event.key==='End'?all.length-1:(i+(event.key==='ArrowRight'?1:-1)+all.length)%all.length;
        all[next].focus(); all[next].click();
      }));
    }
    show(key) {
      if(key===this.current) return;
      const screen = screens.find(([id])=>id===key); if(!screen) return;
      this.current=key;
      this.frame.title='Application Bectanse — '+screen[1];
      this.loadScreen(key);
      const root=this.shadowRoot;
      root.querySelector('.caption').textContent=screen[2];
      root.querySelector('.notice').textContent='';
      root.querySelectorAll('[data-screen]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.screen===key)));
      if(!matchMedia('(prefers-reduced-motion: reduce)').matches){
        this.frame.animate([{opacity:.25,transform:'translateX(22px) scale(.98)'},{opacity:1,transform:'none'}],{duration:600,easing:'cubic-bezier(.22,1,.36,1)'});
      }
    }
    async loadScreen(key) {
      try {
        const source = ({live:'/canal', faq:'/faq'})[key] || '/static/academy-tour/'+key+'.html';
        const response = await fetch(source, {cache:'no-store'});
        if (!response.ok) throw new Error('Unavailable screen');
        const html = await response.text();
        if (key !== this.current || !this.isConnected) return;
        const doc = new DOMParser().parseFromString(html, 'text/html');
        doc.querySelectorAll('script').forEach(script => {
          if (script.getAttribute('src') !== '/static/academy-tour/tour-screen.js?v=3') script.remove();
        });
        this.frame.srcdoc = '<!doctype html>'+doc.documentElement.outerHTML;
      } catch (_) {
        if (key === this.current) this.shadowRoot.querySelector('.notice').textContent='L’aperçu est indisponible. Tu peux ouvrir l’Académie ci-dessous.';
      }
    }
    restricted(){this.shadowRoot.querySelector('.notice').textContent='Cet espace s’ouvre avec ton accès Académie';}
    disconnectedCallback(){window.removeEventListener('message',this.onMessage);this.visibilityObserver?.disconnect();document.body.classList.remove('academy-tour-in-view');}
  }
  customElements.define('academy-preview',AcademyPreview);
})();
